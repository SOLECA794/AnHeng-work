# scripts/ 脚本目录

规划中的脚本（随开发阶段产出）：

| 脚本 | 用途 | 阶段 |
| --- | --- | --- |
| `init_db.py` / `reset_db.py` | 数据库初始化与重置 | A |
| `run_scheduler.py` | 调度器进程入口 | B |
| `crawl_site.py` | 页面发现爬虫 | B |
| `run_detection.py` | 检测任务执行入口（调用工具层） | C |
| `embed_baseline.py` | 页面基线采集 | C |
| `seed_demo.py` | 演示数据（测试集资产/规则）初始化 | G |

约定：脚本幂等、可重复执行；涉及数据库操作遵循 `specs/02-设计规格/SDD-02-数据模型设计.md`。

`verify_t26.py`：使用隔离合成数据完成 14 个报告验收场景，通过真实 ASGI 生成并下载中文 PDF/HTML；JSON 结果在 `reports/t26-acceptance.json`，示例在 `output/pdf/`。不访问配置数据库或外部目标。

`verify_d_closure.py`：在 D 模块统一候选中将真实 T20–T26 服务串联到同一私有 SQLite，执行 19 个跨模块检查，生成 `reports/d-closure-20261023.json` 与 `output/pdf/d-closure-20261023.pdf` / HTML。输入与时钟为固定合成场景；不是线上采集或鉴权适配器。支持 `--output` 和 `--artifact-dir`。

`verify_d_auth_bridge.py`：复现 32 项 D 身份桥单元与真实 JWT/私有 SQLite 接口测试，写入 `reports/d-auth-bridge-20261027.json`。使用 B 的真实签发/校验路径和可信测试授权映射，不访问真实账号/业务库，也不自动装配默认应用。支持 `--output`。
