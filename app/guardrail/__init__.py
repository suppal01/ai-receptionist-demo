"""Emergency keyword guardrail. Runs before the agent on every turn (docs/plan.md section 2).

Rules live in emergency_rules.yaml. Matching is word by word and case-insensitive:
- apostrophes are ignored, so "can't", "can’t" and "cant" are the same word;
- words of five letters or more may differ by one typo ("bleding" matches "bleeding");
- "*" in a phrase stands for up to two words ("knocked out * tooth" matches
  "knocked out his front tooth").
Meaning-level detection (paraphrases, metaphors) is the classifier's job (classifier.py).
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).with_name("emergency_rules.yaml")

# Medical rules are checked first so the caller hears the 911 guidance when both apply.
SEVERITY_ORDER = ("medical", "dental")
WILDCARD_MAX_WORDS = 2
TYPO_MIN_LENGTH = 5


@dataclass(frozen=True)
class Match:
    rule_id: str
    severity: str
    phrase: str


def words(text: str) -> list[str]:
    text = text.lower().replace("’", "").replace("‘", "").replace("'", "")
    return re.findall(r"[a-z0-9*]+", text)


def _one_edit_apart(a: str, b: str) -> bool:
    """True if a and b differ by one inserted, deleted or substituted letter."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _same_word(said: str, rule: str) -> bool:
    return said == rule or (len(rule) >= TYPO_MIN_LENGTH and _one_edit_apart(said, rule))


def _matches_at(text: list[str], i: int, phrase: list[str]) -> bool:
    if not phrase:
        return True
    head, rest = phrase[0], phrase[1:]
    if head == "*":
        return any(_matches_at(text, i + skip, rest) for skip in range(WILDCARD_MAX_WORDS + 1))
    return i < len(text) and _same_word(text[i], head) and _matches_at(text, i + 1, rest)


def _contains(text: list[str], phrase: list[str]) -> bool:
    return any(_matches_at(text, i, phrase) for i in range(len(text)))


@cache
def _load() -> dict:
    with RULES_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@cache
def _phrases() -> list[tuple[list[str], Match]]:
    rules = sorted(_load()["rules"], key=lambda r: SEVERITY_ORDER.index(r["severity"]))
    return [(words(p), Match(r["id"], r["severity"], p)) for r in rules for p in r["phrases"]]


def check(text: str) -> Match | None:
    """Return the first matching emergency rule, or None if the text looks routine."""
    said = words(text)
    for phrase, match in _phrases():
        if _contains(said, phrase):
            return match
    return None


def examples() -> dict[str, list[str]]:
    """Example messages kept with the rules for unit tests (not the frozen test sets)."""
    return _load()["examples"]
