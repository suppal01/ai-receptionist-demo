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

    # Times ("8:00") and ordinals ("4th") are single tokens.
    for number in re.findall(r"\d+(?::\d+|st|nd|rd|th)?", text):
        if not _present(number, source):
            found.append(number)

    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
        for word in words[1:]:  # the first word of a sentence is capitalized anyway
            if not word[0].isupper() or word in _NOT_NAMES:
                continue
            bare = word.removesuffix("'s")
            singular = bare.removesuffix("s")  # "Fridays" is supported by "Friday"
            if not any(_present(w, source) for w in (word, bare, singular)):
                found.append(bare)

    return list(dict.fromkeys(found))


# --- Prices and banned phrases --------------------------------------------------------------

_MONEY = re.compile(
    r"\$\s*\d|\bdollars?\b|\b\d+\s*(?:per|a|/)\s*(?:month|visit|year)\b", re.IGNORECASE
)
_PRICE_QUESTION = re.compile(
    # "how much" only when followed by a verb, pronoun or "for" ("how much is a cleaning",
    # "how much for whitening"), not by a noun ("how much notice", "how much time").
    r"\bhow much (?:is|are|was|does|do|did|would|will|should|can|could|for|it|that|this|i|we)\b"
    r"|\b(costs?|costing|prices?|pricing|fees?|co-?pays?|out[- ]of[- ]pocket"
    r"|charges?|expensive|afford)\b",
    re.IGNORECASE,
)


def mentions_price(text: str) -> bool:
    """True if a reply states an amount of money. The KB has no prices, so this never passes."""
    return _MONEY.search(text) is not None


def asks_about_price(text: str) -> bool:
    """Keyword backup for the model's 'price' label."""
    return _PRICE_QUESTION.search(text) is not None


_BOOKED_WORD = re.compile(r"\b(booked|scheduled|confirmed|all set)\b", re.IGNORECASE)
_QUESTION_START = re.compile(r"^\s*(so|am|is|are|was|did|have|has|we're|i'm|that's|then)\b", re.IGNORECASE)


def asks_if_booked(text: str) -> bool:
    """True when the caller asks whether they now have an appointment ("So I'm booked then?")."""
    return bool(_BOOKED_WORD.search(text)) and ("?" in text or bool(_QUESTION_START.search(text)))


_YES_START = re.compile(
    r"^\s*(yes|yeah|yep|yup|correct|right|that's right|that is right|that's correct|"
    r"all correct|sounds good|perfect|exactly|looks good)\b",
    re.IGNORECASE,
)
_NOT_PLAIN = re.compile(r"\d|\b(but|except|actually|no|not|wrong|change|instead)\b", re.IGNORECASE)


def is_plain_yes(text: str) -> bool:
    """A bare confirmation ("Yes, that's all correct.") with no change in it."""
    t = text.replace("’", "'")
    return bool(_YES_START.search(t)) and not _NOT_PLAIN.search(t) and len(t) <= 80


def find_banned(text: str) -> list[str]:
    normalized = text.lower().replace("’", "'")
    return [p for p in banned_phrases() if p.lower() in normalized]
