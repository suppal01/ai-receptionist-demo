"""Structured logs: one JSON object per line on stdout.

Cloud Run sends stdout to Cloud Logging, which reads `severity` and `message` from JSON
lines and indexes the other keys. Log metadata only (IDs, stages, timings, failures),
never what the caller said; transcripts belong in the database.
"""

import json
import sys
from datetime import datetime, timezone


def log(severity: str, message: str, **fields) -> None:
    entry = {
        "severity": severity,
        "message": message,
        "time": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    print(json.dumps(entry, default=str), file=sys.stdout, flush=True)
