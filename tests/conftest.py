from datetime import date, datetime

import pytest

from receptionist import clinic

TODAY = date(2026, 10, 10)  # a Saturday
NOW = datetime(2026, 10, 10, 9, 0)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "clinic.db"
    clinic.init_db(db, today=TODAY)
    return clinic.connect(db)
