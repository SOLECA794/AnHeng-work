"""授权资产服务（T12）。

规格依据：
- SRS-02 F1.1 授权域名录入（域名格式校验、授权信息、写入授权资产表）
- SRS-02 F1.2 巡检范围配置（全站 / 路径前缀 / 子域；范围外不参与巡检）
- SDD-03 §4.2 授权资产接口、§3 错误码
- SDD-02 §4.2 auth_scope（domain 唯一、scope_type、status、deleted 逻辑删除）
- 验收标准 §4 授权边界：未授权域名一律拒绝并留痕（40310）

设计：存储经 `AssetRepository` 接口隔离（依赖倒置）。当前提供内存实现供开发自测；
B 的持久化层（`models/` + `db/`）到位后新增 SQLAlchemy 实现即可，本服务层不改。

范围判定复用 `services.discovery.is_in_scope`，与页面发现共用同一套范围语义，
避免「配置允许、发现拒绝」的双口径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from src.core.errors import ErrorCode
from src.core.exceptions import ServiceError
from src.services.discovery import Scope, is_in_scope

SCOPE_WHOLE = "WHOLE"
SCOPE_PATH = "PATH"
SCOPE_SUBDOMAIN = "SUBDOMAIN"
SCOPE_TYPES = (SCOPE_WHOLE, SCOPE_PATH, SCOPE_SUBDOMAIN)

STATUS_ENABLED = "ENABLED"
STATUS_DISABLED = "DISABLED"

# 域名：允许单标签主机（localhost）与 IPv4（127.0.0.1）；不接受 scheme/端口/路径
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AssetRecord:
    """授权资产（对应 SDD-02 auth_scope）。"""

    domain: str
    id: int | None = None
    scope_type: str = SCOPE_WHOLE
    scope_value: str | None = None
    auth_note: str | None = None
    owner: str | None = None
    status: str = STATUS_ENABLED
    deleted: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def active(self) -> bool:
        """是否可用于巡检：已启停且未逻辑删除。"""
        return self.status == STATUS_ENABLED and not self.deleted

    def to_scope(self) -> Scope:
        return Scope(
            domain=self.domain, scope_type=self.scope_type, scope_value=self.scope_value
        )


class AssetRepository(Protocol):
    """仓储接口；B 的持久化到位后由 SQLAlchemy 实现替换内存实现。"""

    def add(self, record: AssetRecord) -> AssetRecord: ...
    def get(self, asset_id: int) -> AssetRecord | None: ...
    def find_by_domain(self, domain: str) -> AssetRecord | None: ...
    def list(self, *, include_deleted: bool = False) -> list[AssetRecord]: ...
    def save(self, record: AssetRecord) -> AssetRecord: ...


class InMemoryAssetRepository:
    """内存实现：供开发自测与单测使用；进程重启即失，不得用于生产。"""

    def __init__(self) -> None:
        self._items: dict[int, AssetRecord] = {}
        self._next_id = 1

    def add(self, record: AssetRecord) -> AssetRecord:
        record.id = self._next_id
        self._next_id += 1
        self._items[record.id] = record
        return record

    def get(self, asset_id: int) -> AssetRecord | None:
        return self._items.get(asset_id)

    def find_by_domain(self, domain: str) -> AssetRecord | None:
        # 含已逻辑删除记录：SDD-02 对 domain 有 UNIQUE 约束，重复录入一律 40901
        for record in self._items.values():
            if record.domain == domain:
                return record
        return None

    def list(self, *, include_deleted: bool = False) -> list[AssetRecord]:
        return [
            record
            for record in self._items.values()
            if include_deleted or not record.deleted
        ]

    def save(self, record: AssetRecord) -> AssetRecord:
        self._items[record.id] = record
        return record


class AssetService:
    """授权资产服务：CRUD、范围配置、授权边界校验。"""

    def __init__(self, repository: AssetRepository) -> None:
        self._repository = repository

    # ---- 录入与修改（SRS-02 F1.1 / F1.2） ----

    def create(self, payload: Mapping[str, Any]) -> AssetRecord:
        domain = self._normalize_domain(payload.get("domain"))
        self._validate_scope(
            payload.get("scope_type", SCOPE_WHOLE), payload.get("scope_value")
        )

        existing = self._repository.find_by_domain(domain)
        if existing is not None:
            hint = "（该域名已停用，如需恢复请改为启用）" if existing.deleted else ""
            raise ServiceError(
                ErrorCode.DUPLICATE_OPERATION, f"授权域名已存在：{domain}{hint}"
            )

        now = _utcnow()
        record = AssetRecord(
            domain=domain,
            scope_type=payload.get("scope_type", SCOPE_WHOLE),
            scope_value=payload.get("scope_value"),
            auth_note=payload.get("auth_note"),
            owner=payload.get("owner"),
            status=STATUS_ENABLED,
            deleted=False,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(record)

    def update(self, asset_id: int, payload: Mapping[str, Any]) -> AssetRecord:
        record = self._require(asset_id)

        scope_type = payload.get("scope_type", record.scope_type)
        scope_value = payload.get("scope_value", record.scope_value)
        if "scope_type" in payload and "scope_value" not in payload and scope_type != record.scope_type:
            # 只改类型未给新值：避免把旧范围值错误沿用到新类型上
            scope_value = None
        self._validate_scope(scope_type, scope_value)

        status = payload.get("status", record.status)
        if status not in (STATUS_ENABLED, STATUS_DISABLED):
            raise ServiceError(ErrorCode.INVALID_PARAMETER, f"非法状态：{status}")

        updated = replace(
            record,
            scope_type=scope_type,
            scope_value=scope_value,
            auth_note=payload.get("auth_note", record.auth_note),
            owner=payload.get("owner", record.owner),
            status=status,
            deleted=False if status == STATUS_ENABLED else record.deleted,
            updated_at=_utcnow(),
        )
        return self._repository.save(updated)

    def disable(self, asset_id: int) -> AssetRecord:
        """停用资产（SDD-03 §4.2 DELETE：逻辑删除）。"""
        record = self._require(asset_id)
        disabled = replace(
            record, status=STATUS_DISABLED, deleted=True, updated_at=_utcnow()
        )
        return self._repository.save(disabled)

    # ---- 查询（SDD-03 §4.2） ----

    def get(self, asset_id: int) -> AssetRecord:
        return self._require(asset_id)

    def list(
        self, *, status: str | None = None, include_deleted: bool = False
    ) -> list[AssetRecord]:
        records = self._repository.list(include_deleted=include_deleted)
        if status is not None:
            records = [record for record in records if record.status == status]
        return records

    # ---- 授权边界（验收标准 §4） ----

    def is_authorized(self, url: str) -> bool:
        """URL 是否落在任一在册资产的授权范围内。"""
        return any(
            is_in_scope(url, record.to_scope()) for record in self._active_assets()
        )

    def ensure_authorized(self, url: str) -> AssetRecord:
        """未授权即拒绝（40310），供页面发现与各检测入口强制调用。"""
        for record in self._active_assets():
            if is_in_scope(url, record.to_scope()):
                return record
        raise ServiceError(
            ErrorCode.UNAUTHORIZED_ASSET, f"目标不在授权范围内：{url}"
        )

    def resolve_scope(self, domain: str) -> Scope:
        """按域名取回授权范围，供页面发现直接使用。"""
        normalized = (domain or "").strip().lower().rstrip(".")
        for record in self._active_assets():
            if record.domain == normalized:
                return record.to_scope()
        raise ServiceError(
            ErrorCode.UNAUTHORIZED_ASSET, f"域名未登记为授权资产：{domain}"
        )

    # ---- 内部 ----

    def _active_assets(self) -> list[AssetRecord]:
        return [record for record in self._repository.list() if record.active]

    def _require(self, asset_id: int) -> AssetRecord:
        record = self._repository.get(asset_id)
        if record is None or record.deleted:
            raise ServiceError(ErrorCode.NOT_FOUND, f"授权资产不存在：{asset_id}")
        return record

    @staticmethod
    def _normalize_domain(raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ServiceError(ErrorCode.INVALID_PARAMETER, "域名不能为空")
        domain = raw.strip().lower().rstrip(".")
        if not _DOMAIN_RE.match(domain):
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER,
                f"域名格式非法：{raw.strip()}（应为纯域名，不含协议、端口或路径）",
            )
        return domain

    @staticmethod
    def _validate_scope(scope_type: Any, scope_value: Any) -> None:
        if scope_type not in SCOPE_TYPES:
            raise ServiceError(ErrorCode.INVALID_PARAMETER, f"非法范围类型：{scope_type}")

        if scope_type == SCOPE_PATH:
            if not isinstance(scope_value, str) or not scope_value.startswith("/"):
                raise ServiceError(
                    ErrorCode.INVALID_PARAMETER, "路径前缀范围必须提供以 / 开头的 scope_value"
                )
        elif scope_type == SCOPE_WHOLE and scope_value:
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER, "全站范围不应提供 scope_value"
            )
