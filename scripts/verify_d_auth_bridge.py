"""Verify D's JWT bridge using isolated users, real JWTs and private SQLite."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/d-auth-bridge-20261027.json")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for directory, pattern in (("unit", "test_d_principal.py"), ("integration", "test_d_module_auth.py")):
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests" / directory), pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "task": "D_AUTH_BRIDGE", "date": "2026-10-27",
        "observedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "execution": "Real JWT from B AuthService, private SQLite and D route factories",
        "testsRun": result.testsRun, "passed": result.wasSuccessful(),
        "failures": [str(test) for test, _ in result.failures],
        "errors": [str(test) for test, _ in result.errors],
        "skipped": [str(test) for test, _ in result.skipped],
        "boundaries": [
            "服务器授权解析器在测试中由可信内存映射提供，不代表生产 ACL 表或维护服务已实现。",
            "每请求重新读取授权，不承诺即时取消已在执行的请求。",
            "仅可选路由装配，src.main 默认应用未自动挂载 D 业务路由。",
            "使用隔离用户与私有数据库；不访问真实账号、业务库、外部靶标或恒脑。",
            "未验证生产 PostgreSQL、前端按钮或线上采集/历史 upsert。",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"D JWT bridge: {result.testsRun} tests, {'PASS' if result.wasSuccessful() else 'FAIL'}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
