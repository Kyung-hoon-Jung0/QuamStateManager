from pathlib import Path
import subprocess
from quam_state_manager.web.app import create_app


def test_query_address_offered_before_proxy_load(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    page = tmp_path / "workbench.html"
    page.write_bytes(app.test_client().get("/workbench?qb=http://127.0.0.1:8061").data)
    run = subprocess.run(["node", str(Path(__file__).with_name("leftover_minors_workbench_selfcheck.cjs")), str(page)],
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "3 assertions passed" in run.stdout
