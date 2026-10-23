# tests/ 测试目录

## B 组 T09/T10 与 T11 回归（2026-10-19）

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe scripts/verify_b_auth_agent.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -t tests/unit
.\.venv\Scripts\python.exe scripts/verify_b_models.py
```

T09/T10 专项 38 项通过；使用私有临时 SQLite、真实 ASGI/JWT 和 `httpx.MockTransport` 回放 A 的平台数据。覆盖跨服务重启退出失效、改密、多角色、错误信封、平台字符串返回、734 条 SSE、question/resume、二进制下载、并发/退避/轮询及审计落库。报告见 `reports/b-auth-agent-results.json`。不调用线上平台，也不代表 PostgreSQL 或 T28–T30 端到端已通过。工具注册表成功/失败/超时/拒绝的审计均保存目标指纹，配合 D 的 `test_sensitive.py` 验证脱敏。

测试与用例设计以 `specs/03-测试规格/TEST-01-测试计划与用例设计.md` 为准。

```
tests/
├── dataset/            # 统一测试集（样例集，版本化）
├── fixtures/           # 靶场配置/样本（漏洞、挂马、篡改、泄露）
├── unit/               # 单元测试（服务、工具适配器、规则）
├── integration/        # 集成测试（流水线、调度、接口）
├── security/           # 安全合规测试（授权边界、限速、非破坏）
└── e2e/                # 端到端测试（完整巡检闭环）
```

约定：测试可重复执行；检测效果评估记录测试集版本与指标口径；结果汇总到 `reports/测试报告.md`。

T40 的本机靶场、样例标签和验证方式见 [`dataset/README.md`](dataset/README.md)。

T41 的 29 条功能用例和逐轮执行记录模板见 [`functional/T41-功能用例.md`](functional/T41-功能用例.md) 与 [`functional/T41-执行记录模板.md`](functional/T41-执行记录模板.md)。用例已编写，执行结果待系统功能就绪后填写。

## T20 离线检测验证

在仓库根目录安装依赖后执行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p test_sensitive.py -v
python scripts/verify_t20.py --output reports/t20-sensitive-offline-results.json
```

单元测试覆盖四类规则、规则配置、授权边界、脱敏和资源限制；验收脚本只读取 F 的 v1.0.0 固定本地样例，验证 D-01~D-03 与 E-04，不启动服务或请求站点。四条合成标签的通过率不能代表真实站点检测效果。详细接口与验收说明见 `docs/03-模块实现/T20-敏感信息离线引擎.md`。

## T21 归一化与去重验证

```powershell
python -m unittest discover -s tests/unit -p test_findings.py -v
python scripts/verify_t21.py --output reports/t21-normalization-dedup-results.json
```

专项测试覆盖现有检测器字段/数据类适配、稳定身份、URL 等价、跨轮次与多工具归并、重试幂等、资产隔离、历史状态与来源追溯，以及非法历史数据拒绝。验收脚本使用固定合成记录和显式时间，输出脱敏 JSON 报告，不执行真实扫描或数据库读写。实现契约见 `specs/02-设计规格/SDD-05-结果归一化与去重契约.md`，复现与集成边界见 `docs/03-模块实现/T21-结果归一化与去重.md`。

## T22 结果验证与状态流转

在仓库根目录运行：

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests/unit -p test_finding_verification.py -v
python -m unittest discover -s tests/integration -p "test_finding_verification*.py" -v
python scripts/verify_t22.py --output reports/t22-verification-results.json
```

SQL/API 测试使用 `tests/support/verification_db.py` 创建的独立内存 SQLite 夹具，仅在克隆的测试 DDL 中替换 PostgreSQL JSONB 与自增主键类型；生产 ORM、配置数据库及迁移不受影响。覆盖成功持久化与重新查询、审计失败回滚、状态/来源 CAS、角色与资产权限、同源规则复核和默认拒绝的 API 工厂。契约及接入边界见 `specs/02-设计规格/SDD-06-结果验证与状态流转契约.md`、`docs/03-模块实现/T22-结果验证与状态流转.md`。

## T23 风险分级验证

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests/unit -p test_risk_grading.py -v
python -m unittest discover -s tests/integration -p test_risk_grading_sql.py -v
python scripts/verify_t23.py --output reports/t23-risk-grading-results.json
python -m unittest discover -s tests/unit -t tests/unit -q
```

34 项专项单元测试覆盖 CVSS 等级边界/非法数值、按工具和类型绑定规则、影响面取高、不降级、INFO 保持、策略校验与摘要、资产错配、脱敏及输入不可变。2 项集成测试验证隔离 SQLite 的 ORM 字段往返与状态/身份保持。18 个确定性验收场景包含 T20→T21→T23 对 F 正负样例的验证，以及 T18/T19 合成输出接入；不调用外部站点或恒脑。分级需在归一化/去重后执行，生产持久化和在线装配另行联调；见 `specs/02-设计规格/SDD-08-风险分级契约.md`、`docs/03-模块实现/T23-风险分级模型.md`。
