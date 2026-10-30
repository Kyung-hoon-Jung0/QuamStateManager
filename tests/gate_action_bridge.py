"""Real computation behind the jsdom fetch adapter (no copied decisions)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quam_state_manager.core import additions, gate_action

data = json.load(sys.stdin)
if data["mode"] == "display":
    result = {"display": gate_action.display_context(data["spec"])}
elif data["mode"] == "lint":
    result = additions.lint_proposal(data["spec"], data["addition"], None)
else:
    result = gate_action.resolve(data["spec"], data["pair"], data["action"])
json.dump(result, sys.stdout)
