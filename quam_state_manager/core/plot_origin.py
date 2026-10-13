"""Bounded, persistable plot origin records shared by HTTP and recovery."""
import json


def valid(value):
    if not isinstance(value, dict) or set(value) - {"src", "run_uid", "request_hash", "text", "map"}:
        return False
    for key, item in value.items():
        if key == "map":
            if value.get("src") != "map_fit" or not isinstance(item, dict):
                return False
            try:
                encoded = json.dumps(item, ensure_ascii=False, allow_nan=False)
                if len(encoded) > 16000:
                    return False
                encoded.encode("utf-8")
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return False
        elif (not isinstance(item, str) or len(item) > 300
              or any(0xD800 <= ord(c) <= 0xDFFF for c in item)):
            return False
    return True
