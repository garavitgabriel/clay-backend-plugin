"""SQLite database initialization and connection management."""

import logging
import sqlite3
from pathlib import Path

import sqlite_vec

from .config import resolve_data_dir

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None

# Tri-state cache for sqlite-vec availability: None = not probed yet.
_VEC_AVAILABLE: bool | None = None
_VEC_REASON: str = ""

NO_EXTENSION_SUPPORT = (
    "Your Python was built without SQLite extension support (common with pyenv, "
    "and true of Apple's /usr/bin/python3). "
    "Semantic search is disabled; ingest, filters, and text search still work. "
    "To enable it, recreate the venv with a uv-managed Python "
    "(`uv venv --python 3.12 --managed-python`) or a python.org build."
)


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = resolve_data_dir() / "clay.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def get_db_path() -> str:
    """Absolute path of the SQLite database, as a string.

    Public counterpart to `_get_db_path` — used for startup logging and the
    `db_path` field on analytics so a daemon/MCP split-brain is self-diagnosing.
    """
    return str(_get_db_path())


def _try_load_vec(db: sqlite3.Connection) -> tuple[bool, str]:
    """Attempt to load sqlite-vec into a connection.

    Returns (loaded, reason). A failure is never fatal: embeddings are optional,
    so the plugin degrades to no-embeddings mode rather than dying at import.
    """
    try:
        db.enable_load_extension(True)
    except AttributeError:
        return False, NO_EXTENSION_SUPPORT
    except sqlite3.OperationalError as e:
        return False, f"SQLite refused to enable extension loading: {e}"

    try:
        sqlite_vec.load(db)
    except Exception as e:
        return False, f"sqlite-vec failed to load: {e}"
    finally:
        try:
            db.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError):
            pass

    return True, ""


def vec_available() -> bool:
    """Whether vector search is usable in this interpreter.

    Probed once on first call and cached. False means this Python cannot load
    SQLite extensions (see `vec_unavailable_reason` for the actionable fix).
    """
    global _VEC_AVAILABLE
    if _VEC_AVAILABLE is None:
        get_connection().close()
    return bool(_VEC_AVAILABLE)


def vec_unavailable_reason() -> str:
    """Human-readable reason vector search is off, or '' when it is available."""
    vec_available()
    return _VEC_REASON


def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with sqlite-vec loaded when possible."""
    global _VEC_AVAILABLE, _VEC_REASON

    db = sqlite3.connect(str(_get_db_path()))
    loaded, reason = _try_load_vec(db)

    if _VEC_AVAILABLE is None:
        _VEC_AVAILABLE = loaded
        _VEC_REASON = reason
        if not loaded:
            logger.warning("Vector search unavailable — %s", reason)

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


def init_vec_table(dimension: int) -> bool:
    """Create the sqlite-vec virtual table for vector search.

    The dimension is fixed at creation time and must match the embedding provider.
    Returns False (without raising) when this Python cannot load sqlite-vec.
    """
    if not vec_available():
        return False

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
            return True

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
        return True
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
