import os
import sys
import pytest
from fastapi.testclient import TestClient


# Ensure the backend directory is on sys.path so imports like `import db` and `import main` work
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db as dbmod  # noqa: E402


@pytest.fixture(scope="function")
def temp_db_path(tmp_path_factory):
    return tmp_path_factory.mktemp("db") / "test.db"


@pytest.fixture(scope="function")
def client(temp_db_path, monkeypatch):
    # Patch DB path and initialize schema in temp DB BEFORE importing app
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    dbmod.initialize_db()

    # Import app only after DB is patched
    import main

    return TestClient(main.app)
