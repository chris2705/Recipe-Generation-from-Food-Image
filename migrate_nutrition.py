"""
migrate_nutrition.py — One-shot database migration for Nutrition Analysis feature.

Adds the following nullable columns to the 'saved_recipe' table:
  calories, protein, carbs, fat, fiber, sugar, sodium,
  nutrition_servings, health_score, diet_tags

Safe to run on existing databases. Idempotent: skips columns that already exist.

Usage:
    python migrate_nutrition.py

Run from the project root directory.
"""

import sqlite3
import os
import sys

# Resolve path to the SQLite database
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'Foodimg2Ing', 'data', 'recipe_generator.db')


NUTRITION_COLUMNS = [
    ("calories",           "INTEGER"),
    ("protein",            "REAL"),
    ("carbs",              "REAL"),
    ("fat",                "REAL"),
    ("fiber",              "REAL"),
    ("sugar",              "REAL"),
    ("sodium",             "INTEGER"),
    ("nutrition_servings", "INTEGER"),
    ("health_score",       "INTEGER"),
    ("diet_tags",          "TEXT"),
]


def get_existing_columns(cursor, table: str) -> set:
    """Return the set of column names in the given table."""
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def run_migration():
    if not os.path.exists(DB_PATH):
        print(f"[INFO] Database not found at: {DB_PATH}")
        print("[INFO] It will be created automatically on first app run.")
        print("[INFO] No migration needed for a fresh install.")
        return

    print(f"[INFO] Connecting to: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        existing = get_existing_columns(cursor, 'saved_recipe')
        print(f"[INFO] Existing columns: {sorted(existing)}")

        added = []
        skipped = []

        for col_name, col_type in NUTRITION_COLUMNS:
            if col_name in existing:
                skipped.append(col_name)
            else:
                sql = f"ALTER TABLE saved_recipe ADD COLUMN {col_name} {col_type}"
                cursor.execute(sql)
                added.append(col_name)
                print(f"  [+] Added column: {col_name} ({col_type})")

        conn.commit()

        print()
        if added:
            print(f"[SUCCESS] Migration complete. Added {len(added)} column(s): {added}")
        else:
            print("[SUCCESS] Migration complete. All nutrition columns already present.")

        if skipped:
            print(f"[INFO]    Skipped (already existed): {skipped}")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    run_migration()
