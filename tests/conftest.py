"""Keep unit tests offline and away from real data.

Set (to empty) before the app is imported, so load_dotenv() won't fill them from .env:
the app then uses the in-memory store and the KB-only model. Database tests read .env
themselves and work in a throwaway schema (tests/test_db.py).
"""

import os

os.environ["DATABASE_URL"] = ""
os.environ["AGENT_MODEL"] = ""
