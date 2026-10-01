"""Apply database migrations (app/db/migrations/*.sql) to DATABASE_URL.

    python -m app.db.migrate

Safe to rerun: each migration is applied once and recorded in schema_migrations.
"""

import os
import sys

from dotenv import load_dotenv

from app.db.store import PostgresStore


def main() -> int:
    load_dotenv()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set (environment or .env).", file=sys.stderr)
        return 1
    store = PostgresStore(url)
    try:
        applied = store.migrate()
    finally:
        store.close()
    print("Applied: " + ", ".join(applied) if applied else "Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
