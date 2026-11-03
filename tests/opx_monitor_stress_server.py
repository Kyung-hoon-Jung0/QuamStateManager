"""Local-only Chrome fixture server, using the supplied fake SDK launcher."""
import argparse
import socket
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--fixtures', type=Path, required=True)
parser.add_argument('--instance', type=Path, required=True)
parser.add_argument('--port', type=int, default=5687)
args = parser.parse_args()
os.environ['QUALIBRATE_CONFIG_FILE'] = str(args.instance / 'empty-config')
os.environ.pop('QUALIBRATE_STATE_PATH', None)
original_connect = socket.socket.connect

def local_only(sock, address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
        raise OSError('Only loopback connections allowed in this fake-only server')
    return original_connect(sock, address)

socket.socket.connect = local_only
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quam_state_manager.web.app import create_app
from quam_state_manager.core import opx_monitor as om
app = create_app(testing=True, instance_path=str(args.instance.resolve()))
om.save_settings(args.instance, {'target': {'kind':'qmm','host':'127.0.0.1','port':9510},
                                'python':str((args.fixtures/'stubpy.cmd').resolve()),
                                'interval_s':2,'thresholds':{'temp:all_fems':70}})
m = om.Monitor(args.instance)
app.config['opx_monitor'] = m

@app.post('/_stress/stop')
def stop():
    m.stop()
    return {'ok':True}

app.run(host='127.0.0.1',port=args.port,threaded=True,use_reloader=False)
