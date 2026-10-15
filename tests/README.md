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

T42 的计数口径、逐例结果输入与复算命令见 [`effectiveness/T42-评估口径与执行说明.md`](effectiveness/T42-评估口径与执行说明.md)；使用 `scripts/evaluate_t42.py` 生成每轮指标与报告，不预填检测成绩。
