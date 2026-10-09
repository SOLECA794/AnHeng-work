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
