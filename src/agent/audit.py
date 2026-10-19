"""Persist minimal T10 traces without inputs, outputs or credentials."""
import json
from src.models import AgentLog


class SqlAlchemyAgentAudit:
    def __init__(self, sessions):
        self.sessions = sessions

    def __call__(self, record):
        with self.sessions.begin() as session:
            session.add(AgentLog(
                log_type="AGENT", action=record["action"], tool_name="hengan",
                result_status=record["status"], latency_ms=record["latency_ms"],
                params_summary=json.dumps({"providerCode": record.get("provider_code")}),
            ))
