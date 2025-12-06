import os
import pathlib
import sys

from sqlalchemy.exc import SQLAlchemyError

import database
from database import Base


def main() -> int:
    url = database.DATABASE_URL
    if not url.startswith("sqlite:///"):
        print(f"Skipping reset: DATABASE_URL is not sqlite (got {url})")
        return 0

    path_str = url.replace("sqlite:///", "", 1)
    db_path = pathlib.Path(path_str).resolve()
    if db_path.exists():
        try:
            db_path.unlink()
            print(f"Removed existing DB file: {db_path}")
        except OSError as exc:
            print(f"Failed to remove {db_path}: {exc}")
            return 1
    else:
        print(f"No existing DB file at: {db_path} (skip remove)")

    try:
        Base.metadata.create_all(bind=database.engine)
        print("Created tables on fresh SQLite database.")
    except SQLAlchemyError as exc:
        print(f"Failed to create tables: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
