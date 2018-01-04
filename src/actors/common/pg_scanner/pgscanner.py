#!/usr/bin/env python
from __future__ import print_function

import json
import shlex
import subprocess
import sys


if __name__ == '__main__':
    inputs = json.load(sys.stdin)

    augeas_pg = inputs['aug_postgresql'][0]
    augeas_hba = inputs['aug_pg_hba'][0]

    out_dict = {}
    out_dict['hba'] = {
      'config': augeas_hba['absolute_path'],
      'properties': augeas_hba['properties']
    }
    out_dict['pg'] = {
      'config': augeas_pg['absolute_path'],
      'properties': augeas_pg['properties']
    }

    pg_dict = {}
    for el in augeas_pg["properties"]:
        pg_dict[el['name']] = el['value']

    du_out = subprocess.Popen(['du', '-s', pg_dict['data_directory']], stdout=subprocess.PIPE)
    cut_out = subprocess.Popen(['cut', '-f', '1'], stdin=du_out.stdout, stdout=subprocess.PIPE)
    du_out.stdout.close()
    pg_data_size = cut_out.communicate()[0]
    pg_data_size = pg_data_size.strip()
    # TODO: 0 size or error when directory does not exist?
    out_dict['pg_data_size'] = int(pg_data_size) if pg_data_size else 0

    try:
        lsof_cmd = 'lsof -t -i tcp:{port} -s tcp:listen'.format(port=pg_dict['port'])
        lsof_out = subprocess.check_output(shlex.split(lsof_cmd))
        lsof_out = lsof_out.strip()

        ps_cmd = 'ps h -o args -p {PID}'.format(PID=lsof_out)
        ps_out = subprocess.check_output(shlex.split(ps_cmd))
        ps_out = ps_out.strip()

        if ps_out.find(augeas_pg['absolute_path']) == -1:
            # TODO: postgres instance running on detected port was executed with different config file
            print('postgres instance running on detected port was executed with different config file', file=sys.stderr)


    except subprocess.CalledProcessError as e:
        #TODO: no process listening on specified port
        print('no process listening on specified port', file=sys.stderr)

    print(json.dumps({'pg_scan_info': out_dict}))
