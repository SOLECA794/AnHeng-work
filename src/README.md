# 能力服务

## 本地运行

```bash
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env  # 仅首次创建；已有 .env 时不要覆盖
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload
```

健康检查：`GET http://127.0.0.1:8000/api/health`。

当前挂载 health、auth、agent/logs。统一异常处理返回 SDD-03 响应，不回显校验输入。恒脑凭据只配置在服务端 `.env`。其他模块的业务路由仍需相应负责人联调装配。

## 数据层

- `src/models/`：SQLAlchemy 2 ORM 模型，映射 SDD-02 的 11 张表；`Base.metadata` 是模型事实源。
- `db/schema.sql`：由 ORM 元数据生成的 PostgreSQL 16 幂等建表脚本。
- 运行时依赖已加入 `requirements.txt`：`SQLAlchemy>=2.0,<3`。


## T09/T10 当前接口

- `POST /api/auth/login`：请求 `{"username": "admin", "password": "..."}`，返回 Bearer JWT。
- `GET /api/auth/me`：携带 `Authorization: Bearer <token>` 获取当前用户。
- `POST /api/auth/logout`：持久化吊销当前 JWT，重启后仍失效。
- `POST /api/auth/password`：`oldPassword` / `newPassword` 修改本人密码，全部旧 JWT 失效。
- `GET /api/agent/logs`：仅 ADMIN，分页查询平台动作、状态、耗时与脱敏错误码。
- 开发默认使用 `.local/anheng.db`（忽略提交）；首次启动创建 admin，密码来自 `DEV_ADMIN_PASSWORD`。账号只在首次创建时读取该变量，之后用密码接口修改。新增运维账号：`python scripts/create_user.py demo-ops --role SEC_OPS`，密码通过隐藏输入设置。ADMIN 不自动取得 SEC_OPS 权限。
- 生产设置 `APP_ENV=prod`、至少 32 字符的独立 `JWT_SECRET_KEY`、`DATABASE_URL=postgresql+psycopg://...`；先应用 `db/schema.sql`，再运行创建用户脚本。生产不自动建表或创建默认账号。SQLite 的自动建表仅用于开发。
- `src/agent/provider.py`：平台无关的 `AgentProvider`、SSE 事件解析与分片聚合。
- `src/agent/hengan.py`：恒脑签名、execute 流式、异步任务/有界轮询、resume、文件接口与错误码映射。配置平台凭据后，lifespan 创建带 SQL 审计的 `app.state.agent_provider`，供后续业务路由使用。
- `python scripts/verify_b_auth_agent.py`：38 项 T09/T10 专项验收，输出 `reports/b-auth-agent-results.json`；使用隔离 SQLite 和原始样例回放，不访问恒脑或业务数据库。
- T28–T30 的对话路由尚未挂载；需 A 提供本项目已发布 ID、输入契约及授权范围约束后联调。用户/资产授权映射不能从请求体信任传入，也不能直接给 D 的 Principal 开放全部 scope_ids。

详细交付说明：`docs/03-模块实现/T09-T10-B组交付.md`；演示流程：`docs/02-质量与验收/T49-B组演示流程与答辩材料.md`。
