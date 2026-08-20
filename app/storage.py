"""
SQLite storage layer.

Хранит creators / competitors / integrations в нормализованном виде
(JSON-сериализация pydantic-моделей в TEXT-колонки - для хакатон-MVP
этого достаточно и это прозрачно для отладки).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models import Competitor, Creator, Integration
from config import settings as settings_module

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    creator_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitors (
    competitor_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integrations (
    integration_id TEXT PRIMARY KEY,
    competitor_id TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT,
    last_checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_integrations_competitor ON integrations(competitor_id);
CREATE INDEX IF NOT EXISTS idx_integrations_creator ON integrations(creator_id);
"""


class Storage:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = str(db_path) if db_path else str(_default_db_path())
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def reset(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                "DELETE FROM creators; DELETE FROM competitors; "
                "DELETE FROM integrations; DELETE FROM source_health;"
            )

    # --- writes -------------------------------------------------------
    def upsert_creator(self, creator: Creator) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO creators(creator_id, payload) VALUES (?, ?) "
                "ON CONFLICT(creator_id) DO UPDATE SET payload=excluded.payload",
                (creator.creator_id, creator.model_dump_json()),
            )

    def upsert_competitor(self, competitor: Competitor) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO competitors(competitor_id, payload) VALUES (?, ?) "
                "ON CONFLICT(competitor_id) DO UPDATE SET payload=excluded.payload",
                (competitor.competitor_id, competitor.model_dump_json()),
            )

    def upsert_integration(self, integration: Integration) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO integrations(integration_id, competitor_id, creator_id, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(integration_id) DO UPDATE SET payload=excluded.payload",
                (
                    integration.integration_id,
                    integration.competitor_id,
                    integration.creator_id,
                    integration.model_dump_json(),
                ),
            )

    def set_source_health(self, source: str, status: str, detail: str | None, last_checked_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO source_health(source, status, detail, last_checked_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
                "last_checked_at=excluded.last_checked_at",
                (source, status, detail, last_checked_at),
            )

    # --- reads ----------------------------------------------------------
    def list_creators(self) -> list[Creator]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM creators").fetchall()
        return [Creator.model_validate_json(r[0]) for r in rows]

    def list_competitors(self) -> list[Competitor]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM competitors").fetchall()
        return [Competitor.model_validate_json(r[0]) for r in rows]

    def list_integrations(self) -> list[Integration]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM integrations").fetchall()
        return [Integration.model_validate_json(r[0]) for r in rows]

    def get_creator(self, creator_id: str) -> Creator | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM creators WHERE creator_id=?", (creator_id,)
            ).fetchone()
        return Creator.model_validate_json(row[0]) if row else None

    def list_source_health(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source, status, detail, last_checked_at FROM source_health"
            ).fetchall()
        return [
            {"source": r[0], "status": r[1], "detail": r[2], "last_checked_at": r[3]}
            for r in rows
        ]

    def counts(self) -> dict:
        with self._conn() as conn:
            c = conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0]
            k = conn.execute("SELECT COUNT(*) FROM competitors").fetchone()[0]
            i = conn.execute("SELECT COUNT(*) FROM integrations").fetchone()[0]
        return {"creators": c, "competitors": k, "integrations": i}


def _default_db_path() -> Path:
    return settings_module.STATE_DB_PATH
