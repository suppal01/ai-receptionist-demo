"""Load and sanity-check versioned test sets from testsets/."""

from pathlib import Path

import yaml

KINDS = {"answerable", "unanswerable"}
DECLINES = {"decline_unknown", "decline_price", "decline_clinical"}


def load_test_set(path: str | Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate case ids")
    for case in data["cases"]:
        if "caller" in case:  # multi-turn case, played by the caller simulator
            for key in ("opening", "facts", "behavior", "persona"):
                if key not in case["caller"]:
                    raise ValueError(f"{case['id']}: caller needs {key}")
            if "request_saved" not in case.get("expected", {}):
                raise ValueError(f"{case['id']}: expected needs request_saved")
            continue
        if case["kind"] not in KINDS:
            raise ValueError(f"{case['id']}: unknown kind {case['kind']!r}")
        if case["kind"] == "answerable" and not case.get("expected_ids"):
            raise ValueError(f"{case['id']}: answerable case needs expected_ids")
        if case["kind"] == "unanswerable" and case.get("expected") not in DECLINES:
            raise ValueError(f"{case['id']}: unanswerable case needs expected in {sorted(DECLINES)}")
    return data
