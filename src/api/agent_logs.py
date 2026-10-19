"""T10 audit query; authentication/role checks happen before database access."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from src.auth.dependencies import require_roles
from src.core.response import success
from src.models import AgentLog

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/logs")
def agent_logs(request: Request, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
               action: str | None = Query(None, max_length=100),
               resultStatus: str | None = Query(None, max_length=20),
               user=Depends(require_roles("ADMIN"))):
    conditions = [AgentLog.log_type == "AGENT"]
    if action:
        conditions.append(AgentLog.action == action)
    if resultStatus:
        conditions.append(AgentLog.result_status == resultStatus)
    with request.app.state.sessions() as session:
        total = session.scalar(select(func.count()).select_from(AgentLog).where(*conditions))
        rows = session.scalars(select(AgentLog).where(*conditions).order_by(AgentLog.id.desc())
                               .offset((page - 1) * size).limit(size)).all()
        data = [{"id": row.id, "action": row.action, "toolName": row.tool_name,
                 "resultStatus": row.result_status, "latencyMs": row.latency_ms,
                 "paramsSummary": row.params_summary,
                 "createdAt": row.created_at.strftime("%Y-%m-%d %H:%M:%S")} for row in rows]
    return success({"list": data, "total": total, "page": page, "size": size})
