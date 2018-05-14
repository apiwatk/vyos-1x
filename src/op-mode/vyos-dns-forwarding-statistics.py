#!/usr/bin/env python3

import subprocess
import jinja2

PDNS_CMD='/usr/bin/rec_control'

OUT_TMPL_SRC = """
DNS forwarding statistics:

Cache entries: {{ cache_entries -}}
Cache size: {{ cache_size }} kbytes

"""


if __name__ == '__main__':
    data = {}

    data['cache_entries'] = subprocess.check_output([PDNS_CMD, 'get cache-entries']).decode()
    data['cache_size'] = "{0:.2f}".format( int(subprocess.check_output([PDNS_CMD, 'get cache-bytes']).decode()) / 1024 )

    tmpl = jinja2.Template(OUT_TMPL_SRC)
    print(tmpl.render(data))
