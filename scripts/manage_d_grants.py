"""Inspect or explicitly replace one D user's grants in the configured database."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.d_grants import SqlAlchemyDGrants
from src.core.config import get_settings
from src.core.database import create_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", type=int)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--scope-id", type=int, action="append", help="explicit replacement set; repeat for each asset")
    action.add_argument("--revoke-all", action="store_true", help="remove all grants for this user")
    args = parser.parse_args()
    engine = None
    try:
        settings = get_settings()
        engine, sessions = create_database(settings.database_url, development=settings.app_env.lower() in {"dev", "test"})
        grants = SqlAlchemyDGrants(sessions)
        if args.scope_id or args.revoke_all:
            grants.replace(args.user_id, frozenset(args.scope_id or ()))
        print("Effective scope IDs:", ", ".join(str(i) for i in sorted(grants.resolve(args.user_id))) or "(none)")
        return 0
    except Exception:
        print("Grant operation failed; check the configured database, migration and identifiers.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
