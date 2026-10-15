# SDD-05 · 结果归一化与去重实现契约

> 状态：实现契约；日期：2026-10-13；负责人：D。
> 依据：SRS-02 F4.1/F4.2、SDD-01 §5.5、SDD-02 §4.6。
> 范围：细化 T21 的纯函数输入、输出和归并规则，不改变数据库或公共 API 基线。

## 1. 交付边界

T21 把工具与自研检测器的风险输出映射到统一 Finding，形成稳定的风险标识，并归并调用方提供的当前结果与历史结果。模块路径为 `src/services/findings.py`，不执行扫描、网络请求、数据库事务或自动状态流转。

跨轮次归并要求调用方提供 `existing` 历史记录；只传本轮结果时，只能完成本轮归并。风险确认、误报、复测及告警策略分别属于 T22、T25、T24。

## 2. 公共函数

```python
normalize_url(url: str) -> str

normalize_finding(
    source,  # Mapping 或 dataclass 实例
    *,
    task_id: int,
    scope_id: int,
    tool_name: str,
    seen_at,  # str 或 datetime
    evidence_feature=None,  # JSON 值
    identity_url=None,  # str
) -> dict

deduplicate_findings(findings, *, existing=()) -> list[dict]

to_orm_values(record) -> dict
```

`task_id`、`scope_id`、`tool_name`、`seen_at` 由调用方显式提供，避免把不可信工具输出直接作为运行上下文。`identity_url` 用于调用方提供完整风险目标标识，不替代检测入口的授权校验。上下文 ID 必须是正整数，时间输入可带时区：转换到上海时间，按秒输出 `YYYY-MM-DD HH:MM:SS`；无时区时间按上海本地时间使用。

输入或记录验证失败抛出 `FindingValidationError`；`FINGERPRINT` 抛出其子类 `NonRiskFindingError`，由调用方转送资产信息链路。

归一化字段采用 SDD-01 的 `type`、`raw`，并包括 `severity`、`title`、`url`、`evidence`、`impact`、`suggestion`、`tool_name`、`dedup_key`、`first_seen`、`last_seen`、`status`、`occurrence` 及数据库上下文 `task_id`、`scope_id`。用于归并的来源摘要与任务信息保存在脱敏元数据中。

## 3. 输入适配与类型

接受字段别名：

| 标准字段 | 可接受别名 | 约束 |
| --- | --- | --- |
| `type` | `finding_type`、`findingType` | 同时出现且值不一致时拒绝 |
| `raw` | `raw_data`、`rawData` | 同时出现且值不一致时拒绝 |

支持当前风险类型 `VULN`、`WEAK_CONFIG`、`MALICIOUS_LINK`、`MALICIOUS_SCRIPT`、`TAMPER`、`SENSITIVE`。`FINGERPRINT` 是资产指纹而非风险，调用方应送往独立资产信息链路，不能强制映射为 Finding。

`MALICIOUS_SCRIPT` 已由 T19 与 F 样例产生；SDD-02 §4.6 的枚举说明仍需上游规格同步。现有 ORM 使用字符串列，本契约接纳生产者已有类别，不进行模型迁移或修改公共接口。

## 4. URL 与风险标识

`normalize_url` 统一协议与主机大小写、默认端口表示，并去除 fragment。处理 IDNA 主机、路径非保留字符的百分号表示和点路径段，保留重复斜杠及末尾斜杠的区别。查询参数按参数名归一排序；同名参数的重复值保留原顺序，以免把依赖值顺序的不同请求错误合并。此函数返回完整身份 URL，调用方不应把其返回值直接当作脱敏展示或日志内容。Finding 的展示结果只保留 origin；根路径保留 `/`，其他路径统一为 `/[REDACTED_PATH]`，且不保留 query/fragment。

风险键依据 SDD-01 的 `MD5(目标 + 类型 + 证据特征)`。实现先对规范化 JSON 特征计算 SHA-256，再对 `[目标标识, 类型, 特征摘要]` 的规范化 JSON 数组计算 MD5，明确三部分边界，避免直接字符串拼接歧义。MD5 仅用于非安全用途的去重标识，不作为完整性校验或凭据保护算法。

`evidence_feature` 为显式 JSON 特征。跨工具语义归并必须由调用方提供相同的稳定特征，例如同一问题的类别、参数与位置，而不是使用两种工具的不同标题或说明文本。

未提供显式特征时，允许从当前生产者的稳定结构提取工具内标识：Nuclei `template-id + matcher`、ZAP `pluginId + 参数`、T19 `rule_id`、T20 `rule_id + location + rule_type`。ZAP 参数优先来自结构化字段，也兼容当前适配器证据中的约定后缀 `；参数：`。缺少这些标识时，可将非空且未使用通用脱敏占位符的 evidence 作空白规范化后计算特征摘要；正文只在计算过程中使用。所有回退特征都包含可信 `tool_name` 命名空间，避免不同工具恰好使用相同规则 ID 而误合；只有显式共享 `evidence_feature` 不受工具名隔离。只有通用 `[REDACTED]` 文本且没有结构化规则/位置标识的敏感结果应拒绝。

T20 的 `raw_data.target_sha256` 可作为已脱敏目标的不透明标识。它对原始 URL 计算摘要，不能逆向恢复被删除的查询参数，也不能重新排序原始参数；若需要完整 URL 规范化，应由可信调用方提供 `identity_url`。

## 5. 脱敏输出

外部工具的完整原始对象及任意自由文本不能直接进入归一化输出。`title` 使用按风险类型定义的固定标题，`impact`、`suggestion` 使用固定安全说明；`evidence` 为 `[REDACTED] (sha256=<digest>)`。`raw` 只保留 `identity`、`source_sha256`、`observations`，包含身份摘要及工具、任务、页面、时间与来源摘要，供追溯和归并使用。Nuclei 原始请求、响应、token、正文或其他未审核字段不随 `raw` 输出。

来源追溯以工具、任务及摘要关联，不要求复现原始敏感内容。去重使用独立风险身份：完整规范化目标只在计算过程中使用，`raw.identity.target_sha256` 保存目标标识摘要；T20 已脱敏输入使用 `sha256:<原始目标摘要>` 作为不透明目标标识参与计算。真实资源定位需通过可信调用方的 `page_id`、目标摘要及受控来源记录完成。这里采用保守的展示替代策略，不宣称实现了任意敏感文本的通用识别与脱敏。

## 6. 归并规则

同一资产上下文与风险键的结果合为一条；同 URL 的不同风险类型或不同证据特征保持分离。时间取 `first_seen` 最早值、`last_seen` 最新值；按 SDD-01“同键保留最早记录”，保留最早记录的严重级别，`task_id` 对应最早观测。严重级别调整由 T23 的分级策略负责，归并函数不自动升级。

`occurrence` 按不同检测任务 ID 累计；同任务的多工具命中、重试和重复输入不增加出现次数。已有历史结果的记录 ID 及确认/误报/修复等状态保留，纯函数不擅自重开或确认风险；后续状态变更由 T22/T25 处理。

归并对历史输入与输出应可复用，且不能原地修改调用方输入。历史来源信息必须完整提供，才能正确区分已计入的任务和新任务。同一 `(task_id, tool_name, page_id, source_sha256)` 的重复观测保留最新 `seen_at`，记录的 `first_seen` 仍保留最早值。

`findings` 与 `existing` 均必须符合 T21 的安全标准结构。校验使用字段白名单：除可选的数据库 `id` 外不得加入未知顶层字段；`raw` 只接受约定的三项，来源记录也使用固定结构。标题、影响、建议、证据格式、展示 URL、身份摘要、出现次数和来源时间范围须一致。携带原始自由文本、未知 `raw` 字段、错误次数或不一致时间的历史会被拒绝。普通 ORM 行或旧版未脱敏数据库结果不能直接传入；上游需另外实现显式历史适配，保留已有身份与来源轨迹。

## 7. ORM 适配与集成限制

`to_orm_values` 将标准字段映射到现有 ORM：`type → finding_type`、`raw → raw_data`，并只输出已有模型字段。函数只生成构造/更新值，不写库。

现有唯一约束是 `UNIQUE(task_id, dedup_key)`，只能避免同任务重复插入，不能直接保障跨轮次单风险记录。真实持久化阶段还需设计按资产与风险键查历史、事务或锁策略及并发更新方式；这一集成问题不能以纯函数验收替代。当前任务不新增数据库约束、迁移、接口或事务。

## 8. 验证与变更

```powershell
python -m unittest discover -s tests/unit -p test_findings.py -v
python scripts/verify_t21.py --output reports/t21-normalization-dedup-results.json
```

2026-10-13 的 `reports/t21-normalization-dedup-results.json` 中，10 项固定输入离线验收全部通过，`passed=true`。覆盖跨轮次归并、同任务重试、显式跨工具共享特征、URL 等价、风险差异隔离、资产隔离、T20 不透明目标、历史确认状态及输入不可变、ORM 映射与时间转换，以及输出无不可信原文。单元测试通过数量以实际执行输出为准。纯函数验收不替代数据库持久化、真实工具执行或 T22 状态流转。

| 日期 | 变更 | 影响与验证 |
| --- | --- | --- |
| 2026-10-13 | 新增 T21 实现契约，细化结果归一化、风险键、来源归并及纯函数持久化边界 | T21；数据库及公共 API 基线不变。专项单元测试 39/39、全目录 159/159、离线报告 10/10 通过 |
