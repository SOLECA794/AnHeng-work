from fastapi import APIRouter
from src.core.config import get_settings
from src.core.response import ApiResponse, success

router = APIRouter(tags=["system"])


@router.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    settings = get_settings()
    return success({"status": "up", "service": settings.app_name, "version": settings.app_version})
