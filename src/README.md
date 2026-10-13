# 能力服务

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --reload
```

健康检查：`GET http://127.0.0.1:8000/api/health`。

当前完成 T08 的最小骨架：FastAPI 启动、外置配置、统一响应结构、项目错误码和健康检查。后续路由按 SDD-03 继续挂载，恒脑凭据只能配置在服务端 `.env`。

## 数据层

- `src/models/`：SQLAlchemy 2 ORM 模型，映射 SDD-02 的 11 张表；`Base.metadata` 是模型事实源。
- `db/schema.sql`：由 ORM 元数据生成的 PostgreSQL 16 幂等建表脚本。
- 运行时依赖已加入 `requirements.txt`：`SQLAlchemy>=2.0,<3`。
