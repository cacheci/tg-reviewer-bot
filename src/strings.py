import json
import os
import tempfile
from pathlib import Path
from threading import RLock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRINGS_PATH = PROJECT_ROOT / "assets" / "strings.json"
STRING_CATEGORIES = ("reviewer", "submitter", "channel", "others")

_lock = RLock()
_strings = {category: {} for category in STRING_CATEGORIES}


def _validate_strings(data):
    if not isinstance(data, dict):
        raise ValueError("strings.json root must be an object")
    for category in STRING_CATEGORIES:
        if category not in data:
            raise ValueError(f"strings.json missing category: {category}")
        if not isinstance(data[category], dict):
            raise ValueError(f"strings.json category must be an object: {category}")


def reload_strings():
    with _lock:
        with STRINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        _validate_strings(data)
        for category in STRING_CATEGORIES:
            _strings[category].clear()
            _strings[category].update(data[category])


def _save_strings(data):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=STRINGS_PATH.parent,
            prefix=f".{STRINGS_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, STRINGS_PATH)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def update_string(category, key, value):
    if category not in STRING_CATEGORIES:
        raise KeyError(f"unknown string category: {category}")
    if not isinstance(key, str) or not key:
        raise ValueError("string key must be a non-empty string")

    with _lock:
        updated = {name: dict(values) for name, values in _strings.items()}
        updated[category][key] = value
        _validate_strings(updated)
        _save_strings(updated)
        _strings[category][key] = value


reload_strings()

reviewer = _strings["reviewer"]
submitter = _strings["submitter"]
channel = _strings["channel"]
others = _strings["others"]
