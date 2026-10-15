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

## T21 归一化与去重验证

在仓库根目录安装依赖后执行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests/unit -p test_findings.py -v
python scripts/verify_t21.py --output reports/t21-normalization-dedup-results.json
```

专项测试覆盖现有检测器字段/数据类适配、稳定身份、URL 等价、跨轮次与多工具归并、重试幂等、资产隔离、历史状态与来源追溯，以及非法历史数据拒绝。验收脚本使用固定合成记录和显式时间，输出脱敏 JSON 报告，不执行真实扫描或数据库读写。实现契约见 `specs/02-设计规格/SDD-05-结果归一化与去重契约.md`，复现与集成边界见 `docs/03-模块实现/T21-结果归一化与去重.md`。
