# tests/ 测试目录

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

## T22 结果验证与状态流转

在仓库根目录运行：

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests/unit -p test_finding_verification.py -v
python -m unittest discover -s tests/integration -p "test_finding_verification*.py" -v
python scripts/verify_t22.py --output reports/t22-verification-results.json
```

SQL/API 测试使用 `tests/support/verification_db.py` 创建的独立内存 SQLite 夹具，仅在克隆的测试 DDL 中替换 PostgreSQL JSONB 与自增主键类型；生产 ORM、配置数据库及迁移不受影响。覆盖成功持久化与重新查询、审计失败回滚、状态/来源 CAS、角色与资产权限、同源规则复核和默认拒绝的 API 工厂。契约及接入边界见 `specs/02-设计规格/SDD-06-结果验证与状态流转契约.md`、`docs/03-模块实现/T22-结果验证与状态流转.md`。
