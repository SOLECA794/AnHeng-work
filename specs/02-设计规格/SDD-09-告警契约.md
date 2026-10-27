# SDD-09 · 告警契约（T24）

> 状态：v1.0 实现提案，随功能 PR 评审；日期：2026-10-16；责任人：D。
> 依据：SRS-02 F5.3、SDD-01 §5.7、SDD-02 §4.7、SDD-03 §4.6。

## 1. 规则与通知

外置 `src/config/alert-policy.json` 控制 enabled、min_severity 与 always_types。默认 HIGH 阈值，TAMPER/SENSITIVE 命中独立触发；INFO 及 FALSE_POSITIVE 不告警。只对 OPEN/CONFIRMED 生成告警，FIXED/RETESTED 记录风险修复边界。输入读取已持久化 Finding，调用方需先落库检测/验证结果；不接收客户端传来的级别或身份。分级服务尚未合并时仍可按现有 Finding.severity 独立执行。

站内通知由事务内持久化的 alert 提供，列表即站内收件箱，不额外发送网络消息。邮件为可选能力，本次不配置外发或宣称邮件送达。

## 2. 风险去重与复现

风险身份为 `(scope_id, dedup_key)`，跨任务/跨 Finding ID 去重。以该风险最早 ID 的 Finding 为锚，在其 raw_data.alerting 保存 v1 状态：phase（ACTIVE/RESOLVED）、cycle（正整数）、watermark（来源时间）、alert_id（可空）。此为应用元数据扩展，不改表结构。检测/持久化更新必须保留该锚的 alerting；其历史记录不可物理删除或更换身份，否则会破坏去重。正式流水线装配时必须落实这一保存约定。

PostgreSQL 事务先锁资产行，再锁 Finding；相同资产内串行处理，避免跨工作进程并发生成重复告警。SQLite 使用 BEGIN IMMEDIATE 验证竞争与回滚；不以本地线程锁代替数据库事务。风险生命周期元数据、告警和成功操作审计同事务提交。

ACTIVE 内最多一个告警，即使已读、处理、关闭也不重新触发。仅持久化 FIXED/RETESTED 的 updated_at 建立 RESOLVED 边界；随后 last_seen 严格晚于该边界的 OPEN/CONFIRMED 才开启下一 cycle。旧任务/乱序观测不能回退水位或重开，时间相等保守拒绝复现。处理队列必须包含修复状态事件；T24 不自行把 Finding 标记为修复，此动作属于 T25。

首次遇到已有告警但无元数据时，按同风险既有告警引导去重，包含已关闭告警；不因部署重放而重复通知。同 key 不同类型、损坏元数据或错误告警关联拒绝处理，不静默重建状态。

## 3. 管理接口与权限

提供默认不挂载的路由工厂，宿主需注入真实鉴权上下文。无可信身份返回 401，伪造请求头无效。

| 接口 | 角色与行为 |
| --- | --- |
| GET /api/alerts | SEC_OPS/RULE_ADMIN；分页、status/severity/scopeId 筛选；只返回显式授权且启用资产 |
| POST /api/alerts/{id}/read | ADMIN/SEC_OPS/RULE_ADMIN；仍须有该资产授权；幂等置 read_status=1 |
| PATCH /api/alerts/{id}/status | SEC_OPS；NEW→HANDLED/CLOSED、HANDLED→CLOSED；相同状态幂等，禁止重开 |

生成告警的 `AlertService.process` 是内部服务调用，要求可信 SEC_OPS 与资产授权，没有公开生成接口。所有输出使用固定标题与摘要，不输出 Finding 的原始标题、URL、证据或自由文本。已读状态沿用现有全局 read_status，不宣称每个用户独立已读。状态修改与成功审计同事务；失败返回统一脱敏错误。

## 4. 验收与集成边界

验收覆盖阈值、类型特例、配置禁用、误报、重试、跨轮次身份、资产隔离、关闭后重复、修复后复现、乱序、已有告警引导、事务回滚、并发、分页权限与状态管理。SQLite 测试使用独立临时夹具，不连接配置中的数据库；不执行生产迁移或发送邮件。PostgreSQL 锁行为及真实 JWT/前端挂载后续联调，不把 SQLite 结果称作生产并发验证。

## 5. 变更记录

| 版本 | 日期 | 变更人 | 内容 | 范围 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-10-16 | D | 细化告警规则、风险周期去重、站内通知、管理权限及验收边界 | T24 |
