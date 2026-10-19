"""Create a persistent local/production user; passwords are never CLI arguments."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.persistence import SqlAlchemyUserStore
from src.core.config import get_settings
from src.core.database import create_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--role", choices=["ADMIN", "SEC_OPS", "RULE_ADMIN"], required=True)
    parser.add_argument("--nickname", default="")
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 50 or len(args.nickname) > 50:
        parser.error("username/nickname exceeds column limits")
    password = getpass("Password (8-128 characters): ")
    if password != getpass("Repeat password: ") or not 8 <= len(password) <= 128:
        parser.error("passwords must match and contain 8-128 characters")
    settings = get_settings()
    engine, sessions = create_database(settings.database_url,
                                       development=settings.app_env.lower() in {"dev", "test"})
    try:
        store = SqlAlchemyUserStore(sessions)
        if store.get(args.username):
            parser.error("user already exists; no data changed")
        store.add(args.username, password, args.role, args.nickname)
        print("User created.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
