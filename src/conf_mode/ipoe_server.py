#!/usr/bin/env python3
#
# Copyright (C) 2018 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#

import sys
import os
import re
import time
import socket
import subprocess
import jinja2
import syslog as sl

from vyos.config import Config
from vyos import ConfigError

ipoe_cnf_dir = r'/etc/accel-ppp/ipoe'
ipoe_cnf = ipoe_cnf_dir + r'/ipoe.config'

pidfile = r'/var/run/accel_ipoe.pid'
cmd_port = r'2002'

chap_secrets = ipoe_cnf_dir + '/chap-secrets'
## accel-pppd -d -c /etc/accel-ppp/pppoe/pppoe.config -p /var/run/accel_pppoe.pid

ipoe_config = '''
### generated by ipoe.py ### 
[modules]
log_syslog
ipoe
shaper
ipv6pool
ipv6_nd
ipv6_dhcp
{% if auth['mech'] == 'radius' %}
radius
{% endif -%}
ippool
{% if auth['mech'] == 'local' %}
chap-secrets
{% endif %}

[core]
thread-count={{thread_cnt}}

[log]
syslog=accel-ipoe,daemon
copy=1
level=5

[ipoe]
verbose=1
{% for intfc in interfaces %}
{% if interfaces[intfc]['vlan_mon'] %}
interface=re:{{intfc}}\.\d+,\
{% else %}
interface={{intfc}},\
{% endif %}
shared={{interfaces[intfc]['shared']}},\
mode={{interfaces[intfc]['mode']}},\
ifcfg={{interfaces[intfc]['ifcfg']}},\
range={{interfaces[intfc]['range']}},\
start={{interfaces[intfc]['sess_start']}},\
ipv6=1
{% endfor %}
{% if auth['mech'] == 'noauth' %}
noauth=1
{% endif %}
{% if auth['mech'] == 'local' %}
username=ifname
password=csid
{% endif %}

{%- for intfc in interfaces %}
{% if (interfaces[intfc]['shared'] == '0') and (interfaces[intfc]['vlan_mon']) %}
vlan-mon={{intfc}},{{interfaces[intfc]['vlan_mon']|join(',')}}
{% endif %}
{% endfor %}

{% if (dns['server1']) or (dns['server2']) %}
[dns]
{% if dns['server1'] %}
dns1={{dns['server1']}}
{% endif -%}
{% if dns['server2'] %}
dns2={{dns['server2']}}
{% endif -%}
{% endif -%}

{% if (dnsv6['server1']) or (dnsv6['server2']) or (dnsv6['server3']) %}
[dnsv6]
dns={{dnsv6['server1']}}
dns={{dnsv6['server2']}}
dns={{dnsv6['server3']}}
{% endif %}

[ipv6-nd]
verbose=1

[ipv6-dhcp]
verbose=1

{% if ipv6['prfx'] %}
[ipv6-pool]
{% for prfx in ipv6['prfx'] %}
{{prfx}}
{% endfor %}
{% for pd in ipv6['pd'] %}
delegate={{pd}}
{% endfor %}
{% endif %}

{% if auth['mech'] == 'local' %}
[chap-secrets]
chap-secrets=/etc/accel-ppp/ipoe/chap-secrets 
{% endif %}

{% if auth['mech'] == 'radius' %}
[radius]
verbose=1
{% for srv in auth['radius'] %}
server={{srv}},{{auth['radius'][srv]['secret']}},\
req-limit={{auth['radius'][srv]['req-limit']}},\
fail-time={{auth['radius'][srv]['fail-time']}}
{% endfor %}
{% if auth['radsettings']['dae-server']['ip-address'] %}
dae-server={{auth['radsettings']['dae-server']['ip-address']}}:\
{{auth['radsettings']['dae-server']['port']}},\
{{auth['radsettings']['dae-server']['secret']}}
{% endif -%}
{% if auth['radsettings']['acct-timeout'] %}
acct-timeout={{auth['radsettings']['acct-timeout']}}
{% endif -%}
{% if auth['radsettings']['max-try'] %}
max-try={{auth['radsettings']['max-try']}}
{% endif -%}
{% if auth['radsettings']['timeout'] %}
timeout={{auth['radsettings']['timeout']}}
{% endif -%}
{% if auth['radsettings']['nas-ip-address'] %}
nas-ip-address={{auth['radsettings']['nas-ip-address']}}
{% endif -%}
{% if auth['radsettings']['nas-identifier'] %}
nas-identifier={{auth['radsettings']['nas-identifier']}}
{% endif -%}
{% endif %}

[cli]
tcp=127.0.0.1:2002
'''

### chap secrets
chap_secrets_conf = '''
# username  server  password  acceptable local IP addresses   shaper
{% for aifc in auth['auth_if'] %}
{% for mac in auth['auth_if'][aifc] %}
{% if (auth['auth_if'][aifc][mac]['up']) and (auth['auth_if'][aifc][mac]['down']) %}
{% if auth['auth_if'][aifc][mac]['vlan'] %}
{{aifc}}.{{auth['auth_if'][aifc][mac]['vlan']}}\t*\t{{mac.lower()}}\t*\t{{auth['auth_if'][aifc][mac]['down']}}/{{auth['auth_if'][aifc][mac]['up']}}
{% else %}
{{aifc}}\t*\t{{mac.lower()}}\t*\t{{auth['auth_if'][aifc][mac]['down']}}/{{auth['auth_if'][aifc][mac]['up']}}
{% endif %}
{% else %}
{% if auth['auth_if'][aifc][mac]['vlan'] %}
{{aifc}}.{{auth['auth_if'][aifc][mac]['vlan']}}\t*\t{{mac.lower()}}\t*
{% else %}
{{aifc}}\t*\t{{mac.lower()}}\t*
{% endif %}
{% endif %}
{% endfor %}
{% endfor %}
'''

##### Inline functions start ####
### config path creation
if not os.path.exists(ipoe_cnf_dir):
  os.makedirs(ipoe_cnf_dir)
  sl.syslog(sl.LOG_NOTICE, ipoe_cnf_dir + " created")

def get_cpu():
  cpu_cnt = 1
  if os.cpu_count() == 1:
    cpu_cnt = 1
  else:
    cpu_cnt = int(os.cpu_count()/2)
  return cpu_cnt

def chk_con():
  cnt = 0
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  while True:
    try:
      s.connect(("127.0.0.1", int(cmd_port)))
      break
    except ConnectionRefusedError:
      time.sleep(0.5)
      cnt +=1
      if cnt == 100:
        raise("failed to start pppoe server")
        break

def accel_cmd(cmd=''):
  if not cmd:
    return None
  try:
    ret = subprocess.check_output(['/usr/bin/accel-cmd', '-p', cmd_port, cmd]).decode().strip()
    return ret
  except:
    return 1

### chap_secrets file if auth mode local
def gen_chap_secrets(c):
  
  tmpl = jinja2.Template(chap_secrets_conf, trim_blocks=True)
  chap_secrets_txt = tmpl.render(c)
  old_umask = os.umask(0o077)
  open(chap_secrets,'w').write(chap_secrets_txt)
  os.umask(old_umask)
  sl.syslog(sl.LOG_NOTICE, chap_secrets + ' written')

##### Inline functions end ####

def get_config():
  c = Config()
  if not c.exists('service ipoe-server'):
    return None

  config_data = {}

  c.set_level('service ipoe-server')
  config_data['interfaces'] = {}
  for intfc in c.list_nodes('interface'):
    config_data['interfaces'][intfc] = {
      'mode'        : 'L2',
      'shared'      : '1',
      'sess_start'  : 'dhcpv4',  ### may need a conifg option, can be dhcpv4 or up for unclassified pkts
      'range'       : None,
      'ifcfg'       : '1',
      'vlan_mon'    : []
    }
    config_data['dns'] = {
      'server1'     : None,
      'server2'     : None
    }
    config_data['dnsv6'] = {
      'server1'     : None,
      'server2'     : None,
      'server3'     : None
    }
    config_data['ipv6'] = {
      'prfx'        : [],
      'pd'     : [],
    }
    config_data['auth'] = {
      'auth_if'       : {},
      'mech'          : 'noauth',
      'radius'        : {},
      'radsettings'   : {
        'dae-server'  : {}
      }
    }

    if c.exists('interface ' + intfc + ' network-mode'):
      config_data['interfaces'][intfc]['mode'] = c.return_value('interface ' + intfc + ' network-mode')
    if c.return_value('interface ' + intfc + ' network') == 'vlan':
      config_data['interfaces'][intfc]['shared'] = '0'
      if c.exists('interface ' + intfc + ' vlan-id'):
        config_data['interfaces'][intfc]['vlan_mon'] += c.return_values('interface ' + intfc + ' vlan-id')
      if c.exists('interface ' + intfc + ' vlan-range'):
        config_data['interfaces'][intfc]['vlan_mon'] += c.return_values('interface ' + intfc + ' vlan-range')
    if c.exists('interface ' + intfc + ' client-subnet'):
      config_data['interfaces'][intfc]['range'] = c.return_value('interface ' + intfc + ' client-subnet')
    if c.exists('dns-server server-1'):
      config_data['dns']['server1'] = c.return_value('dns-server server-1')
    if c.exists('dns-server server-2'):
      config_data['dns']['server2'] = c.return_value('dns-server server-2')
    if c.exists('dnsv6-server server-1'):
      config_data['dnsv6']['server1'] = c.return_value('dnsv6-server server-1')
    if c.exists('dnsv6-server server-2'):
      config_data['dnsv6']['server2'] = c.return_value('dnsv6-server server-2')
    if c.exists('dnsv6-server server-3'):
      config_data['dnsv6']['server3'] = c.return_value('dnsv6-server server-3')
    if not c.exists('authentication mode noauth'):
      config_data['auth']['mech'] = c.return_value('authentication mode')
    if c.exists('authentication mode local'):
      for auth_int in c.list_nodes('authentication interface'):
        for mac in c.list_nodes('authentication interface ' + auth_int + ' mac-address'):
          config_data['auth']['auth_if'][auth_int] = {}
          if c.exists('authentication interface ' + auth_int + ' mac-address ' + mac + ' rate-limit'):
            config_data['auth']['auth_if'][auth_int][mac] = {}
            config_data['auth']['auth_if'][auth_int][mac]['up'] = c.return_value('authentication interface ' + auth_int + ' mac-address ' + mac + ' rate-limit upload') 
            config_data['auth']['auth_if'][auth_int][mac]['down'] = c.return_value('authentication interface ' + auth_int + ' mac-address ' + mac + ' rate-limit download')
          else:
            config_data['auth']['auth_if'][auth_int][mac] = {}
            config_data['auth']['auth_if'][auth_int][mac]['up'] = None
            config_data['auth']['auth_if'][auth_int][mac]['down'] = None
          ## client vlan-id
          if c.exists('authentication interface ' + auth_int + ' mac-address ' + mac + ' vlan-id'):
            config_data['auth']['auth_if'][auth_int][mac]['vlan'] = c.return_value('authentication interface ' + auth_int + ' mac-address ' + mac + ' vlan-id')
    if c.exists('authentication mode radius'):
      for rsrv in c.list_nodes('authentication radius-server'):
        config_data['auth']['radius'][rsrv] = {}
        if c.exists('authentication radius-server ' + rsrv + ' secret'):
          config_data['auth']['radius'][rsrv]['secret'] = c.return_value('authentication radius-server ' + rsrv + ' secret')
        else:
          config_data['auth']['radius'][rsrv]['secret'] = None
        if c.exists('authentication radius-server ' + rsrv + ' fail-time'):
          config_data['auth']['radius'][rsrv]['fail-time'] = c.return_value('authentication radius-server ' + rsrv + ' fail-time')
        else:
          config_data['auth']['radius'][rsrv]['fail-time'] = '0'
        if c.exists('authentication radius-server ' + rsrv + ' req-limit'):
          config_data['auth']['radius'][rsrv]['req-limit'] = c.return_value('authentication radius-server ' + rsrv + ' req-limit')
        else:
          config_data['auth']['radius'][rsrv]['req-limit'] = '0'
      if c.exists('authentication radius-settings'):
        if c.exists('authentication radius-settings timeout'):
          config_data['auth']['radsettings']['timeout'] = c.return_value('authentication radius-settings timeout')
        if c.exists('authentication radius-settings nas-ip-address'):
           config_data['auth']['radsettings']['nas-ip-address'] = c.return_value('authentication radius-settings nas-ip-address')
        if c.exists('authentication radius-settings nas-identifier'):
          config_data['auth']['radsettings']['nas-identifier'] = c.return_value('authentication radius-settings nas-identifier')
        if c.exists('authentication radius-settings max-try'):
          config_data['auth']['radsettings']['max-try'] = c.return_value('authentication radius-settings max-try')
        if c.exists('authentication radius-settings acct-timeout'):
          config_data['auth']['radsettings']['acct-timeout'] = c.return_value('authentication radius-settings acct-timeout')
        if c.exists('authentication radius-settings dae-server ip-address'):
          config_data['auth']['radsettings']['dae-server']['ip-address'] = c.return_value('authentication radius-settings dae-server ip-address')
        if c.exists('authentication radius-settings dae-server port'):
          config_data['auth']['radsettings']['dae-server']['port'] = c.return_value('authentication radius-settings dae-server port')
        if c.exists('authentication radius-settings dae-server secret'):
           config_data['auth']['radsettings']['dae-server']['secret'] = c.return_value('authentication radius-settings dae-server secret')

    if c.exists('client-ipv6-pool prefix'):
      config_data['ipv6']['prfx'] = c.return_values('client-ipv6-pool prefix')
    if c.exists('client-ipv6-pool delegate-prefix'):
      config_data['ipv6']['pd'] = c.return_values('client-ipv6-pool delegate-prefix')

  return config_data

def generate(c):
  if c == None or not c:
    return None
  
  c['thread_cnt'] = get_cpu()

  if c['auth']['mech'] == 'local':
    gen_chap_secrets(c)

  tmpl = jinja2.Template(ipoe_config, trim_blocks=True)
  config_text = tmpl.render(c)
  open(ipoe_cnf,'w').write(config_text)
  return c

def verify(c):
  if c == None or not c:
    return None

  for intfc in c['interfaces']:
    if not c['interfaces'][intfc]['range']:
      raise ConfigError("service ipoe-server interface " + intfc + " client-subnet needs a value") 

  if c['auth']['mech'] == 'radius':
    if not c['auth']['radius']:
      raise ConfigError("service ipoe-server authentication radius-server requires a value for authentication mode radius")
    else:
      for radsrv in c['auth']['radius']:
        if not c['auth']['radius'][radsrv]['secret']:
          raise ConfigError("service ipoe-server authentication radius-server " + radsrv + " secret requires a value")

  if c['auth']['radsettings']['dae-server']:
    try:
      if c['auth']['radsettings']['dae-server']['ip-address']:
        pass
    except:
      raise ConfigError("service ipoe-server authentication radius-settings dae-server ip-address value required") 
    try:
      if c['auth']['radsettings']['dae-server']['secret']:
        pass
    except:
      raise ConfigError("service ipoe-server authentication radius-settings dae-server secret value required")
    try:
      if c['auth']['radsettings']['dae-server']['port']:
        pass
    except:
      raise ConfigError("service ipoe-server authentication radius-settings dae-server port value required")

  if len(c['ipv6']['pd']) != 0 and len(c['ipv6']['prfx']) == 0:
    raise ConfigError("service ipoe-server client-ipv6-pool prefix needs a value")

  return c

def apply(c):
  if c == None:
    if os.path.exists(pidfile):
      accel_cmd('shutdown hard')
      if os.path.exists(pidfile):
        os.remove(pidfile)
    return None

  if not os.path.exists(pidfile):
    ret = subprocess.call(['/usr/sbin/accel-pppd', '-c', ipoe_cnf, '-p', pidfile, '-d'])
    chk_con()
    if ret !=0 and os.path.exists(pidfile):
      os.remove(pidfile)
      raise ConfigError('accel-pppd failed to start')
  else:
    accel_cmd('restart')
    sl.syslog(sl.LOG_NOTICE, "reloading config via daemon restart")

if __name__ == '__main__':
  try:
    c = get_config()
    verify(c)
    generate(c)
    apply(c)
  except ConfigError as e:
    print(e)
    sys.exit(1)
