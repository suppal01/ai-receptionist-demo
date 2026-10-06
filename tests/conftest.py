"""Keep unit tests offline and away from real data.

Set (to empty) before the app is imported, so load_dotenv() won't fill them from .env:
the app then uses the in-memory store and the KB-only model. Database tests use the
`pg_store` fixture: .env's database, in a throwaway schema dropped afterwards.
"""

import os
import uuid

import pytest
from dotenv import dotenv_values

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or dotenv_values().get("DATABASE_URL")

os.environ["DATABASE_URL"] = ""
os.environ["AGENT_MODEL"] = ""
os.environ["EMERGENCY_MODEL"] = ""
os.environ["DASHBOARD_PASSWORD"] = ""


@pytest.fixture(scope="module")
def pg_store():
    if not TEST_DATABASE_URL:
        pytest.skip("DATABASE_URL not set")
    import psycopg

    from app.db.store import PostgresStore

    schema = f"test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f'create schema "{schema}"')
    pg = PostgresStore(TEST_DATABASE_URL, schema=schema)
    pg.migrate()
    try:
        yield pg
    finally:
        pg.close()
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'drop schema "{schema}" cascade')
