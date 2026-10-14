# SDD-06 · 结果验证与状态流转实现契约

> 状态：实现契约；日期：2026-10-14；负责人：D。
> 依据：SRS-02 F4.3、SDD-02 finding/agent_log、SDD-03 §4.5。
> 范围：细化 T22 的权限、人工确认、原始证据规则复核与事务审计，不修改既有数据库或公共接口基线。

## 1. 状态与职责边界

T22 只允许 `OPEN → CONFIRMED` 或 `OPEN → FALSE_POSITIVE`。已确认、误报、修复或复测记录不能通过本任务再次改写；重复提交也按状态冲突处理。`FIXED`、`RETESTED` 及修复后重新检测属于 T25，严重级别策略属于 T23。

人工入口依照 SDD-03：`PATCH /api/findings/{id}/status`。补充查询入口 `GET /api/findings/{id}/verification` 仅返回本任务的验证状态与脱敏审计读模型，不替代既有完整风险详情接口。路由工厂由集成方按 T09 的可信认证依赖挂载；本任务不直接挂入 `src/main.py`，不引入未经认证的用户/角色请求头。

## 2. 身份与授权

服务使用可信、有效的 `Principal`，包含用户身份、角色与授权资产集合。调用方负责从已验证会话/令牌及有效用户状态构造主体，不得直接采用请求 JSON 或自报角色。

| 操作 | 允许角色 | 共同约束 |
| --- | --- | --- |
| 修改验证状态、执行规则复核 | `SEC_OPS` | 主体有效，Finding 所属资产在授权集合中且未停用 |
| 查询验证读模型 | `SEC_OPS`、`RULE_ADMIN` | 主体有效，Finding 所属资产在授权集合中且未停用 |

`ADMIN` 没有隐式越权通道，仍按上述角色规则检查。未配置可信认证依赖时，路由默认拒绝请求，返回 `40100`。

## 3. 人工确认契约

服务入口：

```python
VerificationService.verify(
    finding_id, status, reason_code, principal,
    *, note=None, observed_at=None, expected_status="OPEN",
)
```

请求示例：

```json
{
  "status": "CONFIRMED",
  "reasonCode": "EVIDENCE_REVIEWED",
  "expectedStatus": "OPEN",
  "note": "可选人工说明；仅计算摘要"
}
```

`expectedStatus` 可省略，默认并且只接受 `OPEN`。状态与原因必须符合下表，不能把任意字符串当作理由标签。

| 目标状态 | 合法 reasonCode | 含义 |
| --- | --- | --- |
| `CONFIRMED` | `EVIDENCE_REVIEWED` | 已复核证据并确认风险 |
| `FALSE_POSITIVE` | `EXPECTED_PUBLIC_CONTENT` | 内容为预期公开信息 |
| `FALSE_POSITIVE` | `RULE_FALSE_MATCH` | 原始证据中的规则误匹配 |
| `FALSE_POSITIVE` | `AUTHORIZED_CHANGE` | 已确认是授权变更 |

原因码及固定原因标签可查询。`note` 为不超过 1000 字符的可选字符串，不作为正文落库或回显，只保留 SHA-256 摘要。客户端的任意说明不进入审计摘要、错误响应或读模型。处理时间统一为上海时间，按秒输出 `YYYY-MM-DD HH:MM:SS`。

## 4. 非破坏规则复核

规则复核使用只读结果：

```python
RuleReview(
    outcome,  # MATCH / FALSE_MATCH / INCONCLUSIVE
    source_sha256,
    reviewed_sha256,
    rule_version,
)

VerificationService.review(
    finding_id, review, principal, *, observed_at=None,
)
```

复核只接收可信只读复核器对已保存原始来源的结果，服务自身不抓取新页面或执行破坏性操作。`source_sha256` 与 `reviewed_sha256` 必须一致，并且都等于持久化 Finding 的 `raw_data.source_sha256`。规则版本、复核结论与两份来源摘要共同参与 `review_sha256` 计算，原始版本说明不进入审计正文。

同源 `MATCH` 转为 `CONFIRMED`，原因码为 `RULE_REVIEW_MATCH`；同源 `FALSE_MATCH` 转为 `FALSE_POSITIVE`，原因码为 `RULE_REVIEW_FALSE_MATCH`。缺少可复核来源、摘要不一致或 `INCONCLUSIVE` 时返回 `40903`，保留 `OPEN` 并记录 `BLOCKED` 审计。后来抓取的新页面无命中不能证明之前的风险是误报，这类修复复测交给 T25。

## 5. 持久化与审计

`SqlAlchemyVerificationRepository(session_factory)` 使用既有 Finding 和 AgentLog 模型。成功验证在同一个事务内执行条件状态更新及 `SUCCESS` 审计写入，原子提交。审计 flush 失败时回滚未提交的状态更新，这一路径由本地 SQL 测试验证。若连接或提交确认异常导致结果不确定，返回 `50000` 并要求查询当前状态后重试，不对远端最终提交结果作无依据断言。

更新使用状态比较并交换（CAS），条件同时检查 Finding 仍为 `OPEN`、资产仍启用且未删除。规则复核还在 UPDATE 条件中检查 `raw_data.source_sha256` 仍等于复核的原始摘要；读取后来源被替换或状态/资产发生变化时，受影响行数不符合预期，返回 `40902` 并记录 `BLOCKED`，本次不修改状态。进入服务的权限、资产范围、参数或复核拒绝操作记录 `BLOCKED` 审计，使用数字错误码，不保存原始请求正文。

审计采用现有 `AgentLog.params_summary` 的紧凑 JSON，长度不超过 500 字符。内容限于关联 ID、操作者、时间、固定原因码、来源/目标状态、验证方式及来源/说明摘要；部分事件字段存入 AgentLog 的既有列，摘要只写固定字段子集。事件在保存前及查询输出前均检查字段白名单与值类型、枚举、固定原因标签和摘要格式，禁止借用合法键夹带原文。服务查询读模型负责展开为可读标签，不查询或回显任意原始说明。

## 6. 错误与查询

| 项目错误码 | 含义 |
| --- | --- |
| `40001` | 参数、状态/原因组合或复核输入不合法 |
| `40100` | 缺少可信有效主体 |
| `40300` | 当前角色不允许执行该操作 |
| `40310` | Finding 所属资产超出主体授权范围 |
| `40400` | 风险记录不存在 |
| `40902` | 当前状态不是 OPEN，或并发条件更新失败 |
| `40903` | 无法对同一原始来源得出规则复核结论 |
| `50000` | 数据库事务、审计写入或读取失败；不给出数据库原始异常 |

`40902`、`40903` 已在 SDD-03 基线中定义，本次补齐其现有错误码枚举名称。

路由使用项目统一响应 `{code, message, data}`。成功变更的 data 为 `findingId`、`scopeId`、`taskId`、`status`、`verification`；事件使用 `actor_id`、`at`、`from_status`、`to_status`、`reason_code`、`result`、`error_code`、`method` 等字段。`VerificationService.get(finding_id, principal, *, limit=100)` 按同样的身份、角色与资产范围检查，返回上述风险身份/状态及 `trail`：默认返回最近写入的 100 条，允许 1–200 条，按审计 ID 倒序；本次未实现完整历史分页。拒绝响应不包含目标内容、原始证据或用户说明。

路由自带 `APIRoute` 包装，将损坏的 wire JSON 和请求解析/校验异常转为安全 `40001` 响应。请求体未成功解码时业务服务未执行，不声称这类解析拒绝已有业务审计；可以解码但语义无效的请求则进入服务的授权与拒绝审计路径。不可编码的说明、非字符串 outcome 等按 `40001` 拒绝，不回显非法值。

## 7. 集成与验证范围

T22 直接依赖当前主分支既有 ORM，不导入尚未合入的 T20/T21 实现。T20 PR #1 与 T21 PR #2 的合并状态由各自 PR 记录确认。

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests/unit -p test_finding_verification.py -v
python -m unittest discover -s tests/integration -p "test_finding_verification*.py" -v
python scripts/verify_t22.py --output reports/t22-verification-results.json
```

SQL/API 测试使用 SQLite 中克隆的表元数据，仅在测试辅助代码内将 JSONB 改为 JSON、BigInteger 主键改为 Integer。生产模型与数据库模式保持原样。该验证能检查服务事务、持久化、审计与路由行为，但不等于 PostgreSQL 实测、JWT 认证联调或已挂载线上服务。

2026-10-14 最终结果：T22 专项测试 38/38（18 项单元、20 项 SQL/API 集成），本分支 `tests/unit` 全目录 138/138；离线验收报告 16/16，`passed=true`。重复运行得到相同报告。全目录统计基于当前主分支及本次 T22，未包含尚在独立 PR 评审的 T21 分支测试。

| 日期 | 变更 | 影响与验证 |
| --- | --- | --- |
| 2026-10-14 | 新增 T22 实现契约：精确状态边界、同源复核、可信授权、CAS 与原子审计及测试范围 | 专项 38/38、全目录单元 138/138、离线验收 16/16；既有数据库与公共接口基线保持不变 |
