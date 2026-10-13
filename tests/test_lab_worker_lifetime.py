"""A killed owner must not leave a worker or its plotting helpers alive."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
@pytest.mark.parametrize("mode", ["exit", "kill"])
def test_worker_tree_exits_with_owner(tmp_path, mode):
    script = tmp_path / "helper.py"
    script.write_text('import sys,json,subprocess\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)","plot-helper"])\n'
        'for line in sys.stdin:\n'
        ' print("@"+json.dumps({"status":"ok","child":child.pid}),flush=True)\n')
    info = tmp_path / "owner.json"
    slot = tmp_path / "slot"
    slot.mkdir()
    code = ('import json,sys\nfrom pathlib import Path\n'
            'from quam_state_manager.core import lab_worker\n'
            'with lab_worker.machine_lock:\n'
            ' w=lab_worker.Worker(sys.executable,sys.argv[1],"@")\n'
            ' result=w.ask({},10)\n'
            'info=Path(sys.argv[2]); temporary=info.with_suffix(".tmp")\n'
            'temporary.write_text(json.dumps({"worker":w.proc.pid,"child":result["child"]})); temporary.replace(info)\n'
            'sys.stdin.readline()\n')
    env = dict(os.environ, QSM_LAB_WORKER_DIR=str(slot), PYTHONDONTWRITEBYTECODE="1")
    parent = subprocess.Popen([sys.executable, "-B", "-c", code, str(script), str(info)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=Path(__file__).resolve().parents[1], env=env)
    processes = []
    try:
        deadline = time.monotonic() + 20
        while not info.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert info.exists(), "Owner did not receive its worker response"
        processes = [psutil.Process(pid) for pid in json.loads(info.read_text()).values()]
        if mode == "kill": parent.kill()
        else: parent.stdin.write(b"\n"); parent.stdin.flush()
        parent.wait(timeout=10)
        _, alive = psutil.wait_procs(processes, timeout=5)
        assert not alive, "Owner exit left worker/helper processes alive: " + str([p.pid for p in alive])
    finally:
        if parent.poll() is None: parent.kill(); parent.wait(timeout=5)
        for proc in processes:
            try: proc.kill()
            except psutil.Error: pass
        psutil.wait_procs(processes, timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
def test_cold_worker_tree_exits_when_owner_is_killed(tmp_path):
    info = tmp_path / "cold.json"
    script = tmp_path / "cold.py"
    script.write_text('import json,os,sys,time,subprocess\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)","plot-helper"])\n'
        'info=Path(sys.argv[1]); temporary=info.with_suffix(".tmp")\n'
        'temporary.write_text(json.dumps({"worker":os.getpid(),"child":child.pid})); temporary.replace(info)\n'
        'time.sleep(60)\n')
    code = ('import sys\nfrom quam_state_manager.core import lab_worker\n'
            'lab_worker.run([sys.executable,sys.argv[1],sys.argv[2]],timeout=60,text=True)\n')
    parent = subprocess.Popen([sys.executable, "-B", "-c", code, str(script), str(info)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=Path(__file__).resolve().parents[1])
    processes = []
    try:
        deadline = time.monotonic() + 20
        while not info.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert info.exists(), "Cold worker did not start its helper"
        processes = [psutil.Process(pid) for pid in json.loads(info.read_text()).values()]
        parent.kill()
        parent.wait(timeout=10)
        _, alive = psutil.wait_procs(processes, timeout=5)
        assert not alive, "Owner death left a cold process tree alive"
    finally:
        if parent.poll() is None: parent.kill(); parent.wait(timeout=5)
        for proc in processes:
            try: proc.kill()
            except psutil.Error: pass
        psutil.wait_procs(processes, timeout=5)
