"""Revert each stress fix in isolation; every focused pin must be RED.
All subprocess tests use fake workers/SDKs only. Sources are restored in finally.
"""
import json
import os
from evidence_output import evidence_path
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE_REV = 'fe38aac6d2ef4e5822354f8f24258dd04671e006'
CORE = 'quam_state_manager/core/opx_monitor.py'
WORKER = 'quam_state_manager/generator/opx_monitor_worker.py'
BODY = 'quam_state_manager/web/templates/_opx_monitor_body.html'
JS = 'quam_state_manager/web/static/opx-monitor.js'
HEALTH = 'quam_state_manager/web/static/opx-health.js'
CSS = 'quam_state_manager/web/static/ui-kit.css'


def replace(old, new):
    def mutate(source):
        assert old in source, old
        return source.replace(old, new)
    return mutate


def prior_function(file, name):
    old = subprocess.check_output(['git','show',BASE_REV+':'+file], cwd=ROOT).decode('utf-8')
    pattern = r'(?ms)^    def ' + re.escape(name) + r'\(.*?(?=^    def |^#|\Z)'
    previous = re.search(pattern, old).group()
    def mutate(source):
        match = re.search(pattern, source)
        return source[:match.start()] + previous + source[match.end():]
    return mutate

PINS = [
 ('D1',CORE,replace("if state == 'unconfigured':", "if state == 'disabled':"),'test_stress_d1_unconfigured'),
 ('D2 polling',BODY,replace("('live', 'jobs', 'history', 'cleanup')", "('live', 'jobs', 'history')"),'test_stress_d2_cleanup_poll_and_close'),
 ('D2 refresh',JS,replace('                refresh();','                /* refresh reverted */'),'test_stress_browser_pins'),
 ('D2 count feedback',JS,replace('say("Nothing closed. Type " + ids.length + " to confirm closing all listed QMs.", true);',''),'test_stress_browser_pins'),
 ('D2 stale close controls',HEALTH,replace('button.disabled = true;', 'button.disabled = false;'),'test_stress_browser_pins'),
 ('D3 wake',CORE,replace('        if arriving:\n            self._wake.set()', '        if False:\n            self._wake.set()'),'test_stress_d3_viewer_wakes_and_uses_viewer_freshness'),
 ('D3 freshness',CORE,replace("elif age > STALE_INTERVALS * self.poll_interval():", "elif age > STALE_INTERVALS * self.last_poll_interval:"),'test_stress_d3_viewer_wakes_and_uses_viewer_freshness'),
 ('D4',HEALTH,replace('sentence.textContent = "No fresh answer; " + reason;', 'sentence.title = reason;'),'test_stress_browser_pins'),
 ('D5',CORE,prior_function(CORE,'status'),'test_stress_d5_error_cause'),
 ('D5b',WORKER,prior_function(WORKER,'poll'),'test_stress_d5b_malformed_device_section'),
 ('D6',BODY,replace('{{ j.id }}','{{ j.id[:8] }}'),'test_stress_d6_full_job_ids'),
 ('D7',CORE,replace(" if verified else '',", ','),'test_stress_d7_unverified_type'),
 ('D8 busy',CORE,replace("f'{len(active_qms)} QMs with active jobs' if len(active_qms) > 1 else ", ''),'test_stress_d8_busy_empty_version'),
 ('D8 empty slot',BODY,replace('{% elif r.empty %}{% elif limit is not none %}', '{% elif limit is not none %}'),'test_stress_d8_busy_empty_version'),
 ('D8 QOP version',BODY,replace("{% if ans.version.get('QOP') %}", '{% if true %}'),'test_stress_d8_busy_empty_version'),
 ('R1',CSS,replace('.ui-opxmon-tiles {display:flex; flex-wrap:wrap;', '.ui-opxmon-tiles {display:flex;'),'test_stress_r1_responsive'),
 ('R2',CORE,replace("'basis': 'Job status: ' + ', '.join(statuses or ['unknown'])", "'basis': 'active or unknown job status'"),'test_stress_r2_plain_language'),
 ('R3',BODY,replace("section|replace('_', ' ')|capitalize", "section|replace('_', ' ')"),'test_stress_r3_section_labels'),
 ('R4',CORE,replace("'color': 'red', 'problem': True", "'color': 'grey', 'problem': True"),'test_stress_r4_outage_red'),
 ('R5',BODY,replace('<input name="python" list=', '<select name="python" list='),'test_stress_r5_settings'),
 ('R6',CORE,replace('self.viewer_check_pending = True', 'self.viewer_check_pending = False'),'test_stress_r6_returning_viewer_checks'),
 ('R7',BODY,lambda source: re.sub(r'<circle .*?/>','',source),'test_stress_r7_gap_markers'),
 ('R6 in-flight check',CORE,replace('        try:\n            self._poll_once()', '        self.viewer_check_pending = False\n        try:\n            self._poll_once()'),'test_stress_pending_until_reply'),
 ('D5b job section',WORKER,prior_function(WORKER,'poll'),'test_stress_malformed_jobs_keep_other_sections'),
 ('D3 sleeping poller',CORE,replace('self._wake.wait(wait)', 'self._stop.wait(wait)'),'test_stress_d3_sleeping_poller_wakes'),
 ('Process tree',CORE,prior_function(CORE,'kill'),'test_stress_kill_tree'),
]


def main():
    output = evidence_path('opx_monitor_stress_red.json')
    evidence = []
    for label, file, mutate, test in PINS:
        path = ROOT/file
        original = path.read_bytes()
        try:
            source = original.decode('utf-8').replace('\r\n','\n')
            changed = mutate(source)
            assert changed != source, label
            path.write_text(changed,encoding='utf-8')
            for cache in (path.parent/'__pycache__').glob(path.stem+'.*.pyc'):
                cache.unlink()
            result = subprocess.run([sys.executable,'-B','-m','pytest',f'tests/test_opx_monitor.py::{test}','-q','-p','no:cacheprovider'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',env={**os.environ,'PYTHONUTF8':'1'},timeout=90)
            detected = result.returncode == 1 and '1 failed' in result.stdout
            evidence.append({'item':label,'pin':test,'reverted_result':'RED' if detected else 'NOT DETECTED'})
            print(label+': '+evidence[-1]['reverted_result'],flush=True)
            if not detected:
                print(result.stdout+result.stderr)
                raise AssertionError(label)
        finally:
            path.write_bytes(original)
            for cache in (path.parent/'__pycache__').glob(path.stem+'.*.pyc'):
                cache.unlink()
    output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')

if __name__ == '__main__':
    main()
