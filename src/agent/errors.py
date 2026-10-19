"""SDD-03 mapping; never expose upstream error text or credentials."""
from src.agent.provider import AgentError


def map_error(code: int | None, message: str = "") -> AgentError:
    if code in {-402, -403, -405}:
        return AgentError(code, 40300, "恒脑接口鉴权或权限校验失败")
    if code in {-409, -504}:
        return AgentError(code, 40001, "恒脑输入参数不合法，请核对智能体 inputParameters")
    if code == -304:
        return AgentError(code, 50002, "恒脑今日调用配额已耗尽", False)
    if code in {-300, -301}:
        return AgentError(code, 50002, "恒脑并发配额已满，请稍后重试", True)
    return AgentError(code, 50001, "恒脑服务暂不可用", True)
