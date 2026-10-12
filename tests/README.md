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

## T20 离线检测验证

在仓库根目录安装依赖后执行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p test_sensitive.py -v
python scripts/verify_t20.py --output reports/t20-sensitive-offline-results.json
```

单元测试覆盖四类规则、规则配置、授权边界、脱敏和资源限制；验收脚本只读取 F 的 v1.0.0 固定本地样例，验证 D-01~D-03 与 E-04，不启动服务或请求站点。四条合成标签的通过率不能代表真实站点检测效果。详细接口与验收说明见 `docs/03-模块实现/T20-敏感信息离线引擎.md`。
