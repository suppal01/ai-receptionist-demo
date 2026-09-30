"""Emergency keyword guardrail. Runs before the agent on every turn (docs/plan.md section 2).

Rules live in emergency_rules.yaml. Matching is case-insensitive, on whole words, and
ignores extra whitespace and curly apostrophes.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).with_name("emergency_rules.yaml")

# Medical rules are checked first so the caller hears the 911 guidance when both apply.
SEVERITY_ORDER = ("medical", "dental")


@dataclass(frozen=True)
class Match:
    rule_id: str
    severity: str
    phrase: str


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


@cache
def _load() -> dict:
    with RULES_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@cache
def _patterns() -> list[tuple[re.Pattern, Match]]:
    rules = sorted(_load()["rules"], key=lambda r: SEVERITY_ORDER.index(r["severity"]))
    return [
        (
            re.compile(r"(?<!\w)" + re.escape(normalize(phrase)) + r"(?!\w)"),
            Match(rule["id"], rule["severity"], phrase),
        )
        for rule in rules
        for phrase in rule["phrases"]
    ]


def check(text: str) -> Match | None:
    """Return the first matching emergency rule, or None if the text looks routine."""
    normalized = normalize(text)
    for pattern, match in _patterns():
        if pattern.search(normalized):
            return match
    return None


def examples() -> dict[str, list[str]]:
    """Example messages kept with the rules for unit tests (not the frozen test sets)."""
    return _load()["examples"]
