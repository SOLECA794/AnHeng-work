# SDD-13 · D 模块落库与联调契约

> 2026-11-10，D，待评审。补充 SDD-05、SDD-10、SDD-11、SDD-12。公共接口沿用 SDD-03。

## 1. 检测结果入库

- `SqlAlchemyFindingIngestion.ingest(scope_id, task_id, records, ...)` 是供宿主调用的服务，非公共 HTTP 接口。每批最多 500 条 T21 标准新观测；调用方先完成受控检测，不能传入已确认状态、现成风险 ID 或历史集合。写入前重新核对资产启用、任务归属和页面归属。
- PostgreSQL 使用 READ COMMITTED，锁顺序是资产 → 任务 → 页面 → 风险；同资产的 T24/T25 也先锁资产。SQLite 使用 `BEGIN IMMEDIATE`。同资产同风险键只保留一个主记录，出现次数按不同任务计。重复输入返回 UNCHANGED；失败回滚整批。历史已出现多条主记录或来源不可验证时拒绝写入，等待人工核对，不合并删除旧数据。数据库的旧唯一键仍是 `(task_id,dedup_key)`；其他写入口必须遵循同一事务协议。
- 一旦建立主记录，保留 ID、代表任务、工具、脱敏来源、原分级、验证状态、告警周期和复测占用。跨轮次更新首末观测、计数与来源，不变更处理时间，也不将已修复风险擅自重开。T25 的复测命中仍是明确重开路径。历史观测至多 10,000 条、序列化不超过 500,000 字节，超限拒绝而不截断。
- JSON 中的 `t21_ingestion.detector_severity` 保留 T21 原始等级，以便和 T23 分级结果分开。可采用具备完整 T21 来源的旧行；已经分级但缺少原级证明的旧行需单独迁移。T23 可在首次写入时接受显式、可信的分级上下文；之后的普通观测不改原级。
- 宿主可显式传入有该资产授权的安全运营身份以同时执行 T24。风险、告警和审计同一事务；完全重复的输入不重复处理。无该身份时只入库。T26 按期内任务关联观测，即使主记录的代表任务在期外仍计入；区间首末时间是该区间的检测观测，当前状态仍是报告生成时现状。已有报告快照不会被改写。

## 2. 可选装配和授权

- 默认关闭 `D_MODULE_ENABLED`。设置为 `true` 后，宿主挂载 T22、T24、T25、T26 的原有 `/api` 路由，和 T09 使用同一个数据库身份命名空间。通过 `D_REPORT_DIRECTORY` 设置专用报告路径；正式环境应配置受控目录与存储权限。
- PostgreSQL 现有库先评审并执行 `db/migrations/20261110_d_runtime.sql`；新库 `db/schema.sql` 已包含相同结构。迁移不预置任何权限；同时将 B 的密码哈希列从 100 扩至 255，解决 116 字符的原 scrypt 哈希在真实 PostgreSQL 无法写入的问题，算法与已有数据不变。仅显式授权且启用的资产可见；用户停用或角色变化时不会被授权解析器放行。撤权对下一请求生效；已经开始的请求依旧使用其起始授权上下文。
- 权限维护由受信运维执行：`python scripts/manage_d_grants.py <user_id>` 查询有效资产；`--scope-id <id>` 可重复，表示替换**全部**权限；`--revoke-all` 清空。操作应在目标环境权限制度下运行。没有对外增加授予权限接口，不从资产 owner 字段推导权限。
- 启用后宿主可用 `app.state.d_page_detection.ingest_document(...)` 将已登记页面的完整 200 观测交给 T20；也可提供已批准的基线哈希和黑名单来执行 T18/T19。事务提交前重核页面与范围。返回风险 ID 供宿主关联复测。页面采集、采集并发/限速、任务调度、正式规则维护仍由对应宿主提供；D 不访问外链。

## 3. 复测绑定

宿主用 `create_app(..., d_retest_runner=RetestDispatcher(...))` 显式注册可信 runner；未注册的类型保持不确定。不得由用户请求提供 runner、工具参数、模板版本、目标地址或覆盖证明。

| 风险 | 执行与确定条件 |
| --- | --- |
| 敏感信息 | 沿用 T20 规则及 `SensitiveRetestRunner`，由可信数据源提供原规则绑定和新鲜完整页面 |
| 篡改 | `PageRetestRunner` 重新计算当前正文哈希并与原批准基线比较；基线变化、绑定变化或结果不完整时保留原状态 |
| 外链/挂马 | `PageRetestRunner` 对原页面重新运行 T19，核对原规则和资源身份，不访问外链；同一外链出现在不同宿主页面时身份不同 |
| 漏洞/弱配置 | `ToolRetestRunner` 校验原资产、URL、工具、规则及 matcher/参数和规则摘要；只有上游证明独立新运行、准确覆盖且成功解析才允许使用结果。现有通用工具 SUCCESS 无法单独证明完整覆盖，不能作为空结果即修复的依据 |

旧 T18/T19 风险若未绑定原规则与宿主页面，不能自动推断新风险键或复测为已修复。真实 Nuclei/ZAP 执行提供方需要接入可验证的模板/插件覆盖和隔离扫描会话。验收使用固定样例及受控提供方。

## 4. 复验

```powershell
python -m unittest discover -s tests/unit -p test_extended_retest.py -v
python -m unittest discover -s tests/integration -p test_finding_ingestion.py -v
python -m unittest discover -s tests/integration -p test_d_runtime.py -v
```

设置 `ANHENG_D_PRIVATE_PG_URL` 指向专用测试库可运行同一组真实 PostgreSQL 用例，每个用例创建和清理自己的随机 schema。全量集成的旧资产测试另外使用 `ANHENG_TEST_DB_URL`，会清空其资产和计划表，只能给它专用空库。正式授权数据、部署、前端、工具和恒脑联调须在约定环境单独验收。
