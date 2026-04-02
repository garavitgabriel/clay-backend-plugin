"""SQLite database initialization and connection management."""

import os
import sqlite3
from pathlib import Path

import sqlite_vec

_DB_PATH: Path | None = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = os.environ.get("CLAY_DATA_DIR", ".")
        _DB_PATH = Path(data_dir) / "clay.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with sqlite-vec loaded."""
    db = sqlite3.connect(str(_get_db_path()))
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    db = get_connection()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_records (
                id              TEXT PRIMARY KEY,
                record_id       TEXT NOT NULL,
                analysis_type   TEXT NOT NULL,
                data            TEXT NOT NULL,
                source          TEXT,
                entity_id       TEXT,
                entity_name     TEXT,
                tags            TEXT DEFAULT '[]',
                embedding_model TEXT,
                embedding_text  TEXT,
                created_at      TEXT NOT NULL,
                received_at     TEXT NOT NULL,
                UNIQUE (record_id, analysis_type)
            );

            CREATE INDEX IF NOT EXISTS idx_analysis_type
                ON analysis_records(analysis_type);
            CREATE INDEX IF NOT EXISTS idx_entity_id
                ON analysis_records(entity_id);
            CREATE INDEX IF NOT EXISTS idx_created_at
                ON analysis_records(created_at);

            CREATE TABLE IF NOT EXISTS metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        db.commit()
    finally:
        db.close()


def init_vec_table(dimension: int) -> None:
    """Create the sqlite-vec virtual table for vector search.

    The dimension is fixed at creation time and must match the embedding provider.
    """
    db = get_connection()
    try:
        # Check if vec table already exists
        existing = db.execute(
            "SELECT value FROM metadata WHERE key = 'vec_dimension'"
        ).fetchone()

        if existing is not None:
            existing_dim = int(existing["value"])
            if existing_dim != dimension:
                raise ValueError(
                    f"Vector table exists with dimension {existing_dim}, "
                    f"but provider requires {dimension}. "
                    f"Run delete_records to clear data and re-initialize."
                )
            return

        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_records USING vec0("
            f"  record_id TEXT, "
            f"  embedding float[{dimension}]"
            f")"
        )
        db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('vec_dimension', ?)",
            (str(dimension),),
        )
        db.commit()
    finally:
        db.close()


def get_vec_dimension() -> int | None:
    """Get the current vector table dimension, or None if not initialized."""
    db = get_connection()
    try:
        row = db.execute(
            "SELECT value FROM metadata WHERE key = 'vec_dimension'"
        ).fetchone()
        return int(row["value"]) if row else None
    finally:
        db.close()
