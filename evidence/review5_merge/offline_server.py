import sys, runpy, socket, logging, subprocess
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from flask import request, Response
from quam_state_manager.web.app import create_app
from quam_state_manager.core import opx_monitor as om

fixtures = runpy.run_path('tests/test_opx_monitor.py')
app = create_app(testing=True, instance_path=str(Path('.scratch_review5/instance').resolve()))
clock = fixtures['Clock']()
data = fixtures['answer'](qms=(), temps={f'FEM {i}': v for i, v in enumerate([66.6,64.6,68.1,0,55.2,53.2,60.1,0])})
data['controllers'][0].update(name='Controller 1', fems={str(i): ('MW' if i < 4 else 'LF') for i in [1,2,3,5,6,7]})
data['cluster_name'] = None
worker = fixtures['FakeWorker']([data])
monitor = om.Monitor(app.instance_path, worker_factory=lambda *a: worker, clock=clock)
monitor._start_thread = lambda: None
app.config['opx_monitor'] = monitor
om.save_settings(app.instance_path, {'target': {'kind':'qmm','host':'127.0.0.1','port':9510}, 'python':'fake-worker', 'interval_s':5, 'thresholds':{}, 'alarms_armed':False})
om.read_admin = lambda *a: {'known':True, 'cluster':{'name':'', 'health':{'status':'OPERATIONAL_CLUSTER','messages':[],'deviceMessages':[]}, 'hasUnreachableDevices':False,'devices':[],'packages':[]}, 'devices':[], 'selection':'offline fixture'}
def blocked(*a, **kw):
    raise AssertionError('Outbound connection forbidden in visual review')
socket.create_connection = blocked
socket.socket.connect = blocked
for _ in range(5):
    monitor.poll_once()
    clock.t += 5
monitor.poll_once()
@app.before_request
def scenario():
    if request.path.startswith('/opx-monitor/'):
        tab = request.path.rsplit('/',1)[-1]
        if tab not in ('live','jobs','history','cleanup','settings'):
            return
        data['open_qms'] = [] if tab == 'live' else [{'id':'sample-1','jobs':[],'ports':['Controller 1/1']}, {'id':'sample-2','jobs':[],'ports':['Controller 1/2']}]
        monitor.poll_once()
legacy_style = subprocess.check_output(['git','show','42190a1babe9dabc2df1023aa2bba1e9972ce82d:quam_state_manager/web/static/style.css']).decode('utf-8').replace('opxmon-', 'ui-opxmon-')
legacy_kit = subprocess.check_output(['git','show','42190a1babe9dabc2df1023aa2bba1e9972ce82d:quam_state_manager/web/static/ui-kit.css']).decode('utf-8')
@app.route('/review5/legacy.css')
def old_style(): return Response(legacy_style, mimetype='text/css')
@app.route('/review5/kit.css')
def old_kit(): return Response(legacy_kit, mimetype='text/css')
@app.after_request
def legacy(response):
    if request.args.get('legacy') and response.mimetype == 'text/html':
        import re
        text = response.get_data(as_text=True)
        text = re.sub(r'/static/style.css[^"\s]*', '/review5/legacy.css', text)
        text = re.sub(r'/static/ui-kit.css[^"\s]*', '/review5/kit.css', text)
        response.set_data(text)
    return response
logging.getLogger('werkzeug').setLevel(logging.ERROR)
app.run(host='127.0.0.1',port=5745,threaded=True,use_reloader=False)
