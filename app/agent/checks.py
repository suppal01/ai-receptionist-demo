"""Deterministic checks the code runs on caller text and on every model draft.

None of these call a model. They back up the model's own labels, so a mislabel or a
drifting draft is caught before the caller hears it.
"""

import re
from collections.abc import Iterable

from app.agent.scripts import banned_phrases

# --- Phone numbers ------------------------------------------------------------------------


def normalize_phone(raw: str) -> str | None:
    """Return a 10-digit US number, or None if the text doesn't contain exactly one."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def speak_digits(phone: str) -> str:
    """Read-back form, digit by digit in 3-3-4 groups: '5 0 3, 5 5 5, 0 1 4 7'."""
    groups = (phone[:3], phone[3:6], phone[6:])
    return ", ".join(" ".join(g) for g in groups)


# --- Specific facts in a draft must appear in its sources --------------------------------

# Capitalized words that are never facts on their own.
_NOT_NAMES = {"I", "I'm", "I'll", "I've", "I'd", "AI", "AM", "PM", "Dr", "Mr", "Ms", "Mrs"}
_TITLES = re.compile(r"\b(Dr|Mr|Ms|Mrs|St)\.")


def _present(token: str, source: str) -> bool:
    pattern = r"(?<![\w:])" + re.escape(token) + r"(?![\w:])"
    return re.search(pattern, source) is not None


def unsupported_specifics(
    draft: str, sources: Iterable[str], allowed: Iterable[str] = ()
) -> list[str]:
    """Numbers, times and names in the draft that don't appear in any source text.

    `allowed` holds values the caller gave (their name, number), which the agent may repeat.
    """
    source = " ".join([*sources, *allowed]).replace("’", "'")
    text = _TITLES.sub(r"\1", draft.replace("’", "'"))
    found: list[str] = []

    for number in re.findall(r"\d+(?::\d+)?", text):
        if not _present(number, source):
            found.append(number)

    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
        for word in words[1:]:  # the first word of a sentence is capitalized anyway
            if not word[0].isupper() or word in _NOT_NAMES:
                continue
            bare = word.removesuffix("'s")
            if not (_present(word, source) or _present(bare, source)):
                found.append(bare)

    return list(dict.fromkeys(found))


# --- Prices and banned phrases --------------------------------------------------------------

_MONEY = re.compile(
    r"\$\s*\d|\bdollars?\b|\b\d+\s*(?:per|a|/)\s*(?:month|visit|year)\b", re.IGNORECASE
)
_PRICE_QUESTION = re.compile(
    r"\b(how much|costs?|costing|prices?|pricing|fees?|co-?pays?|out[- ]of[- ]pocket"
    r"|charges?|expensive|afford)\b",
    re.IGNORECASE,
)


def mentions_price(text: str) -> bool:
    """True if a reply states an amount of money. The KB has no prices, so this never passes."""
    return _MONEY.search(text) is not None


def asks_about_price(text: str) -> bool:
    """Keyword backup for the model's 'price' label."""
    return _PRICE_QUESTION.search(text) is not None


def find_banned(text: str) -> list[str]:
    normalized = text.lower().replace("’", "'")
    return [p for p in banned_phrases() if p.lower() in normalized]
