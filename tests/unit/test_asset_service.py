"""授权资产服务单测（T12）。

规格依据：
- SRS-02 F1.1 授权域名录入（域名格式校验、授权信息、写入授权资产表）
- SRS-02 F1.2 巡检范围配置（全站 / 路径前缀 / 子域；范围外不参与巡检）
- SDD-03 §4.2 授权资产接口、§3 错误码（40310 未授权资产、40901 重复操作、40400 资源不存在）
- SDD-02 §4.2 auth_scope（domain 唯一、scope_type、status、deleted 逻辑删除）
- 验收标准 §4 授权边界：未授权域名一律拒绝并留痕

说明：B 的持久化层（models/db）尚未产出，本测试按仓储接口注入**内存实现**；
B 到位后只需新增 SQLAlchemy 实现，服务层不改。

运行：python tests/unit/test_asset_service.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.errors import ErrorCode  # noqa: E402
from src.core.exceptions import ServiceError  # noqa: E402
from src.services.assets import AssetService, InMemoryAssetRepository  # noqa: E402


def _service() -> AssetService:
    return AssetService(InMemoryAssetRepository())


class AssetCreateTest(unittest.TestCase):
    def test_creates_enabled_asset(self):
        asset = _service().create({"domain": "example.com"})
        self.assertEqual(asset.domain, "example.com")
        self.assertEqual(asset.scope_type, "WHOLE")
        self.assertEqual(asset.status, "ENABLED")
        self.assertIsNotNone(asset.id)
        self.assertFalse(asset.deleted)

    def test_normalizes_domain_to_lowercase(self):
        asset = _service().create({"domain": "  Example.COM  "})
        self.assertEqual(asset.domain, "example.com")

    def test_rejects_duplicate_domain_with_40901(self):
        service = _service()
        service.create({"domain": "example.com"})
        with self.assertRaises(ServiceError) as ctx:
            service.create({"domain": "EXAMPLE.com"})
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_OPERATION)

    def test_rejects_malformed_domain_with_40001(self):
        for domain in ("", "   ", "http://example.com", "example.com/path", "exa mple.com", "-bad.com"):
            with self.subTest(domain=domain):
                with self.assertRaises(ServiceError) as ctx:
                    _service().create({"domain": domain})
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_accepts_loopback_and_single_label_hosts(self):
        # 本项目授权靶场就是 localhost / 127.0.0.1，必须可用
        for domain in ("localhost", "127.0.0.1"):
            with self.subTest(domain=domain):
                self.assertEqual(_service().create({"domain": domain}).domain, domain)

    def test_defaults_scope_type_to_whole(self):
        self.assertEqual(_service().create({"domain": "a.test"}).scope_type, "WHOLE")

    def test_path_scope_requires_leading_slash_value(self):
        with self.assertRaises(ServiceError) as ctx:
            _service().create({"domain": "a.test", "scope_type": "PATH"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

        with self.assertRaises(ServiceError) as ctx:
            _service().create({"domain": "a.test", "scope_type": "PATH", "scope_value": "dvwa"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_path_scope_accepts_slash_prefixed_value(self):
        asset = _service().create(
            {"domain": "localhost", "scope_type": "PATH", "scope_value": "/dvwa"}
        )
        self.assertEqual(asset.scope_value, "/dvwa")

    def test_whole_scope_rejects_scope_value(self):
        with self.assertRaises(ServiceError) as ctx:
            _service().create({"domain": "a.test", "scope_type": "WHOLE", "scope_value": "/x"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_rejects_unknown_scope_type(self):
        with self.assertRaises(ServiceError) as ctx:
            _service().create({"domain": "a.test", "scope_type": "EVERYTHING"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_keeps_authorization_note_and_owner(self):
        asset = _service().create(
            {"domain": "a.test", "auth_note": "已授权-2026", "owner": "运维部"}
        )
        self.assertEqual(asset.auth_note, "已授权-2026")
        self.assertEqual(asset.owner, "运维部")


class AssetQueryTest(unittest.TestCase):
    def setUp(self):
        self.service = _service()
        self.asset = self.service.create({"domain": "example.com"})

    def test_get_returns_asset(self):
        self.assertEqual(self.service.get(self.asset.id).domain, "example.com")

    def test_get_missing_raises_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.get(999)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_list_returns_all_enabled_assets(self):
        self.service.create({"domain": "b.test"})
        self.assertEqual(len(self.service.list()), 2)

    def test_list_filters_by_status(self):
        self.service.disable(self.asset.id)
        self.assertEqual(self.service.list(status="ENABLED"), [])
        # 已停用资产被逻辑删除，需显式 include_deleted 才出现在查询结果中
        self.assertEqual(len(self.service.list(status="DISABLED", include_deleted=True)), 1)

    def test_disable_hides_asset_from_list(self):
        self.service.disable(self.asset.id)
        self.assertEqual(self.service.list(), [])

    def test_disable_marks_deleted_and_disabled(self):
        disabled = self.service.disable(self.asset.id)
        self.assertTrue(disabled.deleted)
        self.assertEqual(disabled.status, "DISABLED")
        with self.assertRaises(ServiceError):
            self.service.get(self.asset.id)


class AssetUpdateTest(unittest.TestCase):
    def setUp(self):
        self.service = _service()
        self.asset = self.service.create({"domain": "example.com"})

    def test_updates_scope_and_owner(self):
        updated = self.service.update(
            self.asset.id,
            {"scope_type": "PATH", "scope_value": "/dvwa", "owner": "安全部"},
        )
        self.assertEqual(updated.scope_type, "PATH")
        self.assertEqual(updated.scope_value, "/dvwa")
        self.assertEqual(updated.owner, "安全部")

    def test_update_missing_raises_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.update(999, {"owner": "x"})
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_update_validates_scope_like_create(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.update(self.asset.id, {"scope_type": "PATH"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)


class AssetAuthorizationTest(unittest.TestCase):
    """授权边界（验收标准 §4）：未授权域名一律拒绝（40310）。"""

    def setUp(self):
        self.service = _service()
        self.service.create({"domain": "example.com"})
        self.service.create({"domain": "localhost", "scope_type": "PATH", "scope_value": "/dvwa"})

    def test_authorizes_registered_domain(self):
        self.assertTrue(self.service.is_authorized("http://example.com/"))

    def test_rejects_unregistered_domain_with_40310(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.ensure_authorized("http://evil.test/")
        self.assertEqual(ctx.exception.code, ErrorCode.UNAUTHORIZED_ASSET)

    def test_rejects_path_outside_configured_prefix(self):
        self.assertTrue(self.service.is_authorized("http://localhost/dvwa/login.php"))
        self.assertFalse(self.service.is_authorized("http://localhost/other"))
        self.assertFalse(self.service.is_authorized("http://localhost/dvwaX"))

    def test_disabled_asset_is_no_longer_authorized(self):
        asset = self.service.list()[0]
        self.service.disable(asset.id)
        self.assertFalse(self.service.is_authorized(f"http://{asset.domain}/"))

    def test_resolve_scope_returns_discovery_scope(self):
        scope = self.service.resolve_scope("localhost")
        self.assertEqual(scope.domain, "localhost")
        self.assertEqual(scope.scope_type, "PATH")
        self.assertEqual(scope.scope_value, "/dvwa")

    def test_resolve_scope_rejects_unauthorized_domain(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.resolve_scope("evil.test")
        self.assertEqual(ctx.exception.code, ErrorCode.UNAUTHORIZED_ASSET)


if __name__ == "__main__":
    unittest.main(verbosity=2)
