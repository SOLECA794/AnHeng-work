"""Explicit D route assembly with verified JWTs and server-side asset grants."""
from dataclasses import dataclass

from fastapi import APIRouter

from src.api.alerts import create_alert_router
from src.api.finding_verification import create_verification_router
from src.api.reporting import create_report_router
from src.api.retesting import create_retest_router
from src.auth.d_principal import GrantResolver, create_d_principal_dependency
from src.services.alerts import AlertService
from src.services.finding_verification import VerificationService
from src.services.reporting import ReportService
from src.services.retesting import RetestService


@dataclass(frozen=True)
class DModuleServices:
    verification: VerificationService
    alerts: AlertService
    retesting: RetestService
    reports: ReportService


def create_d_module_router(services: DModuleServices, *, grant_resolver: GrantResolver | None = None,
                           verification_clock=None, alert_clock=None, report_clock=None) -> APIRouter:
    """The host owns services, sessions and a trusted grant resolver; no default mount."""
    if not isinstance(services, DModuleServices) or any(value is None for value in (
        services.verification, services.alerts, services.retesting, services.reports
    )):
        raise ValueError("all D module services must be explicitly supplied")
    identity = create_d_principal_dependency(grant_resolver)
    router = APIRouter()
    router.include_router(create_verification_router(services.verification, principal_dependency=identity, clock=verification_clock))
    router.include_router(create_alert_router(services.alerts, principal_dependency=identity, clock=alert_clock))
    router.include_router(create_retest_router(services.retesting, principal_dependency=identity))
    router.include_router(create_report_router(services.reports, principal_dependency=identity, clock=report_clock))
    return router
