from concurrent.futures import ThreadPoolExecutor
from tests.test_autosync_merge import env, _arm, _edit


def test_idle_window_during_first_auto_applied_edit(env):
    _arm(env, pull=True, push=True)
    idle = env["app"].test_client()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for run in range(20):
            assert _edit(env, value=str(5.1e9 + run * 1e6)).status_code == 200
            apply = pool.submit(env["client"].post, "/state/apply-to-live", data={"auto_flush": "1"})
            poll = pool.submit(idle.get, "/state/drift")
            for response in (apply.result(), poll.result()):
                assert response.status_code == 200, response.get_data(as_text=True)
                assert "shared_working_copy" not in response.get_data(as_text=True)
    print("W4-1: not reproduced after 20 overlapping edit/poll runs")
