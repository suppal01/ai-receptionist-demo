"""Fixed wording the agent uses as written (loaded from scripts.yaml)."""

from functools import cache
from pathlib import Path

import yaml

SCRIPTS_PATH = Path(__file__).with_name("scripts.yaml")


@cache
def _load() -> dict:
    with SCRIPTS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def script_names() -> list[str]:
    return [k for k, v in _load().items() if isinstance(v, str)]


def template(name: str) -> str:
    """Return a script as written, with {placeholders} unfilled."""
    return _load()[name]


def script(name: str, **fields: str) -> str:
    """Return a script with its {placeholders} filled in. A missing field raises KeyError."""
    return template(name).format_map(fields)


def banned_phrases() -> list[str]:
    return list(_load()["banned_phrases"])
