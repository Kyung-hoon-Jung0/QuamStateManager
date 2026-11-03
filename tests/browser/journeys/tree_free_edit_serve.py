"""Isolated Stage A browser rig. Copies only the committed synthetic fixture."""
import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("repo", type=Path)
parser.add_argument("rig", type=Path)
parser.add_argument("port", type=int)
parser.add_argument("--large", action="store_true")
args = parser.parse_args()
fixture = Path(__file__).resolve().parents[2] / "fixtures" / "tree_free_edit"
chip = args.rig / "copy"
chip.mkdir(parents=True, exist_ok=True)
for name in ("state.json", "wiring.json"):
    shutil.copy2(fixture / name, chip / name)
if args.large:
    state = json.loads((chip / "state.json").read_text())
    seed = copy.deepcopy(state["qubits"]["q1"])
    for i in range(30):
        key = f"q{i + 1}"
        row = state["qubits"].setdefault(key, copy.deepcopy(seed))
        row["id"] = key
        row.setdefault("extras", {})["samples"] = {
            f"group_{g}": {f"calibration_value_{k:04}": k / 1040 for k in range(1040)}
            for g in range(10)
        }
    (chip / "state.json").write_text(json.dumps(state, indent=2))
sys.path.insert(0, str(args.repo))
from quam_state_manager.web.app import create_app

app = create_app(testing=True, instance_path=str(args.rig / "instance"))
with app.test_client() as client:
    response = client.post("/load", data={"folder": str(chip)})
    assert response.status_code in (200, 302), response.status_code
app.run(port=args.port, threaded=True, use_reloader=False)
