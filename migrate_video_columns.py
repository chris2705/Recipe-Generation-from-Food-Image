"""
migrate_video_columns.py
========================
Safe one-time migration: adds video_path, video_language, video_created_at
to the saved_recipe table if they are not already present.

Preserves all existing rows — only ALTERs the schema.

Run standalone:
    food\\Scripts\\python.exe migrate_video_columns.py

Or it is automatically called at Flask startup via __init__.py.
"""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Columns to add: (column_name, sqlite_type)
VIDEO_COLUMNS = [
    ("video_path",       "TEXT"),
    ("video_language",   "TEXT"),
    ("video_created_at", "DATETIME"),
]


def run_migration(db_path: str) -> None:
    """
    Inspect the saved_recipe table and add any missing video columns.
    Safe to call multiple times — idempotent.
    """
    if not os.path.exists(db_path):
        logger.warning(f"[Migration] DB not found at {db_path} — skipping.")
        return

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        # Fetch existing column names
        cursor.execute("PRAGMA table_info(saved_recipe);")
        existing_cols = {row[1] for row in cursor.fetchall()}

        if not existing_cols:
            logger.warning("[Migration] saved_recipe table does not exist yet — skipping.")
            return

        added = []
        for col_name, col_type in VIDEO_COLUMNS:
            if col_name not in existing_cols:
                sql = f"ALTER TABLE saved_recipe ADD COLUMN {col_name} {col_type};"
                logger.info(f"[Migration] Adding column: {col_name} ({col_type})")
                cursor.execute(sql)
                added.append(col_name)

        if added:
            conn.commit()
            logger.info(f"[Migration] ✅ Added {len(added)} column(s): {', '.join(added)}")
        else:
            logger.info("[Migration] ✅ saved_recipe schema is up-to-date — no changes needed.")

    except Exception as exc:
        conn.rollback()
        logger.error(f"[Migration] ❌ Failed: {exc}", exc_info=True)
        raise
    finally:
        conn.close()


def run_migration_from_app(app) -> None:
    """
    Run migration using the database URI configured in the Flask app.
    Call this inside an app context (or before first request).
    """
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    # Extract filesystem path from sqlite:///path
    if db_uri.startswith("sqlite:///"):
        db_path = db_uri[len("sqlite:///"):]
    elif db_uri.startswith("sqlite://"):
        db_path = db_uri[len("sqlite://"):]
    else:
        logger.warning(f"[Migration] Non-SQLite DB URI — skipping schema migration: {db_uri}")
        return
    run_migration(db_path)


# ── Standalone entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Default path relative to project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_db = os.path.join(script_dir, "Foodimg2Ing", "data", "recipe_generator.db")
    db_path = sys.argv[1] if len(sys.argv) > 1 else default_db

    print(f"Running migration on: {db_path}")
    run_migration(db_path)
    print("Done.")
