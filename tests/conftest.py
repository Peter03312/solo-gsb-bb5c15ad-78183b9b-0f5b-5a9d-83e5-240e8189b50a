"""Test bootstrap: environment must be configured before app modules import."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="radio-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("GUARD_BAND", "2.0")

import pytest
from fastapi.testclient import TestClient

from app.db import engine, init_db
from app.main import app
from app.models import Base


@pytest.fixture()
def client():
    # Fresh schema for every test.
    Base.metadata.drop_all(bind=engine)
    init_db()
    with TestClient(app) as test_client:
        yield test_client
