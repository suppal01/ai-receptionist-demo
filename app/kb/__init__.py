"""Knowledge base: approved entries from kb_seed/ and a keyword search over them.

This in-memory search stands in for Postgres full-text search (docs/plan.md section 3)
until the database increment. The interface, search(query) -> hits with entry IDs,
stays the same when the backend changes.
"""

import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

KB_PATH = Path(__file__).resolve().parents[2] / "kb_seed" / "sparkle_kb.yaml"

STOPWORDS = frozenset(
    """a an the and or but of for in on at to from by with about as into
    i me my we us our you your it its is are am was were be been being
    do does did have has had can could will would should may might
    what when how who which why that this these those there here
    any some all no not if so than then too very just also
    like please want need get got know tell let im ive id youre dont
    hi hello hey thanks thank ok okay yes""".split()
)


@dataclass(frozen=True)
class Entry:
    id: str
    category: str
    text: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit:
    id: str
    category: str
    text: str
    score: float


def _stem(word: str) -> str:
    """Very light stemming so plurals match: braces -> brace, hours -> hour."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))
    return [_stem(w) for w in words if w not in STOPWORDS]


@cache
def entries() -> tuple[Entry, ...]:
    with KB_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return tuple(
        Entry(
            id=e["id"],
            category=e["category"],
            text=e["approved_text"],
            keywords=tuple(e.get("keywords", ())),
        )
        for e in data["entries"]
    )


@cache
def _index() -> tuple[list[set[str]], dict[str, float]]:
    """Token set per entry, and an IDF weight per token so rare words count more."""
    docs = [
        set(tokenize(" ".join([e.text, e.category.replace("_", " "), *e.keywords])))
        for e in entries()
    ]
    n = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for token in doc:
            df[token] = df.get(token, 0) + 1
    idf = {t: math.log(1 + n / c) for t, c in df.items()}
    return docs, idf


# A hit must cover at least this share of the query's weight, so one shared common word
# ("last" in "last night" vs "your last dental visit") is not enough. That suits raw caller
# sentences (the KB-only model). The agent model searches with keyword lists instead
# ("dentists providers staff team"), where 0.5 found nothing; 0.2 finds them and still
# returns nothing for off-topic keywords (measured on practice_info_v1, 2026-10-01).
MIN_COVERAGE = 0.5
KEYWORD_QUERY_MIN_COVERAGE = 0.2


def search(query: str, limit: int = 3, min_coverage: float = MIN_COVERAGE) -> list[Hit]:
    """Return up to `limit` entries that match the query, best first.

    An empty list means the knowledge base has nothing on the question.
    """
    docs, idf = _index()
    terms = set(tokenize(query))
    # Words the KB never uses get the highest weight: the question is about something else.
    unseen_weight = math.log(1 + len(docs))
    total = sum(idf.get(t, unseen_weight) for t in terms)
    scored = []
    for entry, doc in zip(entries(), docs):
        score = sum(idf[t] for t in terms & doc)
        if score > 0 and score / total >= min_coverage:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [Hit(e.id, e.category, e.text, round(s, 3)) for s, e in scored[:limit]]
