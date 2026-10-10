import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app as app_module
from config import Config
from extensions import db
from models import Item
from schema import upgrade_legacy_schema

# DDL exactly as SQLAlchemy created it in the user's real inventory.db before the pivot.
LEGACY_USER_DDL = """
CREATE TABLE user (
	created_at DATETIME, 
	last_login DATETIME, 
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	email VARCHAR(120), 
	bio VARCHAR(300), 
	avatar VARCHAR(120), 
	PRIMARY KEY (id), 
	UNIQUE (username), 
	UNIQUE (email)
)
"""

LEGACY_ITEM_DDL = """
CREATE TABLE item (
	id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	quantity INTEGER, 
	price FLOAT, 
	category VARCHAR(80), 
	image_filename VARCHAR(200), 
	created_at DATETIME, 
	user_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES user (id)
)
"""

# id, name, quantity, price, category, created_at
LEGACY_ROWS = [
    (1, "Lentes", 1, 7.0, "other", "2026-10-07 04:50:54.433575"),
    (2, "Cable", 10, 5.5, "general", "2026-03-15 14:30:00.123456"),
    (3, "Grapadora", 2, 12.25, "office", "2026-12-31 23:59:59.000001"),
    (4, "Cargador", 4, 20.0, "electronics", "2025-01-02 00:00:01.000000"),
    (5, "Tinta", 8, 3.0, "consumable", "2026-06-30 10:00:00.000000"),
    (6, "Sin categoria", 1, 1.0, None, "2026-02-01 08:00:00.000000"),
    (7, "Sin fecha", 1, 1.0, "general", None),
]

NEW_COLUMNS = ("location", "event_date", "status", "contact")


def make_legacy_db(path, rows=LEGACY_ROWS):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(LEGACY_USER_DDL)
        conn.exec_driver_sql(LEGACY_ITEM_DDL)
        conn.exec_driver_sql(
            "INSERT INTO user (id, username, password_hash) VALUES (1, 'maria', 'x')"
        )
        for row_id, name, quantity, price, category, created_at in rows:
            conn.execute(
                text(
                    "INSERT INTO item (id, name, quantity, price, category, created_at, user_id) "
                    "VALUES (:id, :name, :quantity, :price, :category, :created_at, 1)"
                ),
                {
                    "id": row_id,
                    "name": name,
                    "quantity": quantity,
                    "price": price,
                    "category": category,
                    "created_at": created_at,
                },
            )
    return engine


@pytest.fixture
def legacy_engine(tmp_path):
    engine = make_legacy_db(tmp_path / "legacy.db")
    yield engine
    engine.dispose()


def column_names(engine):
    with engine.connect() as conn:
        return [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(item)")]


def fetch_items(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, name, quantity, price, category, location, event_date, status, "
                "contact FROM item ORDER BY id"
            )
        )
        return {row.id: row for row in rows}


def test_legacy_fixture_has_no_new_columns(legacy_engine):
    names = column_names(legacy_engine)
    for column in NEW_COLUMNS:
        assert column not in names


def test_upgrade_adds_the_new_columns(legacy_engine):
    result = upgrade_legacy_schema(legacy_engine)

    assert result.table_found is True
    assert result.columns_added == NEW_COLUMNS
    names = column_names(legacy_engine)
    for column in NEW_COLUMNS:
        assert column in names


def test_upgrade_keeps_legacy_columns_and_data_intact(legacy_engine):
    upgrade_legacy_schema(legacy_engine)

    names = column_names(legacy_engine)
    for column in ("quantity", "price", "image_filename", "created_at", "user_id"):
        assert column in names
    rows = fetch_items(legacy_engine)
    assert len(rows) == len(LEGACY_ROWS)
    for row_id, name, quantity, price, _category, _created_at in LEGACY_ROWS:
        assert rows[row_id].name == name
        assert rows[row_id].quantity == quantity
        assert rows[row_id].price == price


def test_upgrade_backfills_defaults(legacy_engine):
    upgrade_legacy_schema(legacy_engine)

    rows = fetch_items(legacy_engine)
    for row in rows.values():
        assert row.status == "lost"
        assert row.location == "Sin especificar"
        assert row.contact is None
    assert rows[1].event_date == "2026-10-07"
    assert rows[2].event_date == "2026-03-15"
    assert rows[3].event_date == "2026-12-31"
    assert rows[4].event_date == "2025-01-02"
    # No created_at to derive the date from: left empty rather than invented.
    assert rows[7].event_date is None


def test_upgrade_maps_legacy_categories(legacy_engine):
    upgrade_legacy_schema(legacy_engine)

    rows = fetch_items(legacy_engine)
    assert rows[1].category == "other"
    assert rows[2].category == "other"
    assert rows[3].category == "other"
    assert rows[4].category == "electronics"
    assert rows[5].category == "other"
    assert rows[6].category is None
    assert rows[7].category == "other"


def test_upgrade_reports_what_it_changed(legacy_engine):
    result = upgrade_legacy_schema(legacy_engine)

    assert result.changed is True
    assert result.backfilled["location"] == len(LEGACY_ROWS)
    assert result.backfilled["event_date"] == len(LEGACY_ROWS) - 1
    # Rows 2, 3, 5 and 7 change category; 1 is already "other", 4 is "electronics", 6 is NULL.
    assert result.backfilled["category"] == 4
    description = result.describe()
    for column in NEW_COLUMNS:
        assert column in description


def test_upgrade_is_idempotent(legacy_engine):
    upgrade_legacy_schema(legacy_engine)
    before = fetch_items(legacy_engine)

    second = upgrade_legacy_schema(legacy_engine)

    assert second.changed is False
    assert second.columns_added == ()
    assert second.backfilled == {}
    assert "nothing changed" in second.describe()
    after = fetch_items(legacy_engine)
    assert {key: tuple(row) for key, row in before.items()} == {
        key: tuple(row) for key, row in after.items()
    }


def test_upgrade_is_a_noop_when_the_table_is_missing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        result = upgrade_legacy_schema(engine)

        assert result.table_found is False
        assert result.changed is False
        assert "nothing changed" in result.describe()
        with engine.connect() as conn:
            tables = conn.exec_driver_sql("SELECT name FROM sqlite_master").fetchall()
        assert tables == []
    finally:
        engine.dispose()


def test_upgrade_does_not_overwrite_existing_values(legacy_engine):
    upgrade_legacy_schema(legacy_engine)
    with legacy_engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE item SET location = 'Biblioteca', event_date = '2026-01-02', "
            "status = 'found', contact = 'ana@uni.edu', category = 'keys' WHERE id = 2"
        )
        conn.exec_driver_sql("UPDATE item SET location = NULL WHERE id = 3")

    result = upgrade_legacy_schema(legacy_engine)

    rows = fetch_items(legacy_engine)
    assert rows[2].location == "Biblioteca"
    assert rows[2].event_date == "2026-01-02"
    assert rows[2].status == "found"
    assert rows[2].contact == "ana@uni.edu"
    assert rows[2].category == "keys"
    # Only the row that was still NULL gets backfilled.
    assert rows[3].location == "Sin especificar"
    assert result.backfilled == {"location": 1}


def test_upgrade_adds_only_the_missing_columns(legacy_engine):
    with legacy_engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE item ADD COLUMN contact VARCHAR(120)")
        conn.exec_driver_sql("UPDATE item SET contact = 'x@uni.edu' WHERE id = 1")

    result = upgrade_legacy_schema(legacy_engine)

    assert result.columns_added == ("location", "event_date", "status")
    assert fetch_items(legacy_engine)[1].contact == "x@uni.edu"


def test_upgraded_table_works_with_the_item_model(legacy_engine):
    from admin import ensure_moderation_columns

    upgrade_legacy_schema(legacy_engine)
    ensure_moderation_columns(legacy_engine)  # create_app runs both on start

    with Session(legacy_engine) as session:
        item = session.get(Item, 2)
        assert item.event_date == date(2026, 3, 15)
        assert item.status == "lost"
        assert item.location == "Sin especificar"
        assert item.category == "other"

        session.add(Item(name="Llaves", user_id=1))
        session.commit()
        created = session.query(Item).filter_by(name="Llaves").one()
        assert created.status == "lost"
        assert created.location is None
        assert created.event_date is None


def test_new_status_column_rejects_null(legacy_engine):
    upgrade_legacy_schema(legacy_engine)

    with pytest.raises(Exception):
        with legacy_engine.begin() as conn:
            conn.exec_driver_sql("UPDATE item SET status = NULL WHERE id = 1")


# ---------------------------------------------------------------------------
# create_app wiring
# ---------------------------------------------------------------------------
class SpyUpgrade:
    def __init__(self, real):
        self.real = real
        self.calls = []

    def __call__(self, engine):
        self.calls.append(engine)
        return self.real(engine)


@pytest.fixture
def spy(monkeypatch):
    spy = SpyUpgrade(app_module.upgrade_legacy_schema)
    monkeypatch.setattr(app_module, "upgrade_legacy_schema", spy)
    return spy


def point_config_at(monkeypatch, db_path):
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")


def test_create_app_with_test_config_never_runs_the_upgrade(spy, tmp_path):
    application = app_module.create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            "AVATAR_UPLOAD_FOLDER": str(tmp_path / "avatars"),
            "ITEM_UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )

    assert application.config["TESTING"] is True
    assert spy.calls == []


def test_create_app_without_test_config_upgrades_an_existing_database(
    spy, monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "existing.db"
    make_legacy_db(db_path).dispose()
    point_config_at(monkeypatch, db_path)

    with caplog.at_level("INFO"):
        application = app_module.create_app()
    try:
        assert len(spy.calls) == 1
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            names = column_names(engine)
            rows = fetch_items(engine)
        finally:
            engine.dispose()
        for column in NEW_COLUMNS:
            assert column in names
        assert rows[2].location == "Sin especificar"
        assert rows[2].event_date == "2026-03-15"
        assert rows[4].category == "electronics"
        assert any(
            record.levelname == "INFO" and "location" in record.getMessage()
            for record in caplog.records
        )
    finally:
        with application.app_context():
            db.engine.dispose()


def test_create_app_without_test_config_creates_a_missing_database(spy, monkeypatch, tmp_path):
    db_path = tmp_path / "fresh.db"
    assert not os.path.exists(db_path)
    point_config_at(monkeypatch, db_path)

    application = app_module.create_app()
    try:
        assert os.path.exists(db_path)
        assert len(spy.calls) == 1
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            names = column_names(engine)
        finally:
            engine.dispose()
        for column in NEW_COLUMNS:
            assert column in names
    finally:
        with application.app_context():
            db.engine.dispose()
