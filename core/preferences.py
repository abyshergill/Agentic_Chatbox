"""Personal settings from Settings > General: what to call the user, and custom instructions for every chat."""

from __future__ import annotations

import json
from pathlib import Path

LIMITS = {"display_name": 60, "custom_instructions": 8000}


class Preferences:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        return {key: str(data.get(key, "") or "")[:limit] for key, limit in LIMITS.items()}

    def save(self, values: dict) -> dict:
        data = self.load()
        for key, limit in LIMITS.items():
            if key in values and values[key] is not None:
                text = str(values[key]).strip()
                if len(text) > limit:
                    raise ValueError(f"{key.replace('_', ' ').capitalize()} can be at most {limit} characters.")
                data[key] = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
