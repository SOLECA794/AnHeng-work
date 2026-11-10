"""Persist normalized scanner observations without rewriting the risk lifecycle."""
from dataclasses import dataclass
import json

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.models import AuthScope, DetectionTask, Finding, SitePage
from src.repositories.alerts import AlertTransaction
from src.services.findings import (
    INGESTION_MARKER, _IMPACT, _SUGGESTION, _validate_record,
    deduplicate_findings, to_orm_values,
)
from src.services.finding_verification import VerificationError, _id, _timestamp, require_principal
from src.services.risk_grading import graded_orm_values


_CANONICAL_RAW = {"identity", "source_sha256", "observations"}
_LIFECYCLE_RAW = {"alerting", "retest_pending"}
MAX_BATCH = 500
MAX_PROVENANCE_BYTES = 500_000  # Leave room for T25's revision and lifecycle metadata.


class FindingIngestionError(ValueError):
    pass


@dataclass(frozen=True)
class IngestedFinding:
    finding_id: int
    dedup_key: str
    occurrence: int
    change: str


def _history(row):
    raw = row.raw_data
    if not isinstance(raw, dict) or not _CANONICAL_RAW <= raw.keys():
        raise FindingIngestionError("historical finding lacks canonical provenance")
    if raw.keys() - _CANONICAL_RAW - _LIFECYCLE_RAW - {"grading", INGESTION_MARKER}:
        raise FindingIngestionError("historical finding has unsupported metadata")
    managed = INGESTION_MARKER in raw
    severity, impact, suggestion = row.severity, row.impact, row.suggestion
    if managed:
        marker = raw[INGESTION_MARKER]
        if not isinstance(marker, dict) or set(marker) != {"detector_severity"}:
            raise FindingIngestionError("historical ingestion marker is invalid")
        severity = marker["detector_severity"]
        if "grading" in raw:
            impact, suggestion = _IMPACT, _SUGGESTION
    elif "grading" in raw:
        raise FindingIngestionError("legacy grading requires a reviewed history conversion")

    # T25 MATCH can move the public last_seen past the latest scanner observation.
    # Derive the canonical end from observations, without undoing the public end.
    try:
        observation_end = max(item["seen_at"] for item in raw["observations"])
        record = _validate_record(dict(
            id=row.id, task_id=row.task_id, page_id=row.page_id, scope_id=row.scope_id,
            type=row.finding_type, severity=severity, title=row.title, url=row.url,
            evidence=row.evidence, impact=impact, suggestion=suggestion, tool_name=row.tool_name,
            dedup_key=row.dedup_key, status=row.status, occurrence=row.occurrence,
            first_seen=row.first_seen, last_seen=observation_end,
            raw={key: raw[key] for key in _CANONICAL_RAW},
        ))
        if (record["last_seen"] > row.last_seen.isoformat(sep=" ", timespec="seconds")
                or not any((item["task_id"], item["tool_name"], item["page_id"], item["source_sha256"])
                           == (row.task_id, row.tool_name, row.page_id, raw["source_sha256"])
                           for item in record["raw"]["observations"])):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        raise FindingIngestionError("historical finding provenance is inconsistent") from None
    return record


class SqlAlchemyFindingIngestion:
    def __init__(self, sessions, *, grading_policy=None, alert_service=None):
        self.sessions = sessions
        self.grading_policy = grading_policy
        self.alert_service = alert_service

    def ingest(self, scope_id, task_id, records, *, grading_contexts=None, page_guard=None, alert_principal=None):
        """One trusted task batch; upsert a single stable anchor per asset/risk key."""
        if not _id(scope_id) or not _id(task_id):
            raise FindingIngestionError("invalid trusted scope or task")
        if not isinstance(records, (list, tuple)) or len(records) > MAX_BATCH:
            raise FindingIngestionError("invalid or oversized task batch")
        try:
            if alert_principal is not None:
                principal = require_principal(alert_principal)
                if self.alert_service is None or scope_id not in principal.scope_ids:
                    raise FindingIngestionError("alert processing requires an authorized service actor")
                alert_at = _timestamp()
            checked = [_validate_record(record) for record in records]
            if any("id" in record or record["scope_id"] != scope_id or record["task_id"] != task_id
                   or record["status"] != "OPEN" or record["first_seen"] != record["last_seen"]
                   or len(record["raw"]["observations"]) != 1
                   or (record["raw"]["observations"][0]["tool_name"],
                       record["raw"]["observations"][0]["page_id"],
                       record["raw"]["observations"][0]["source_sha256"])
                   != (record["tool_name"], record["page_id"], record["raw"]["source_sha256"])
                   for record in checked):
                raise FindingIngestionError("expected new observations for this task and asset")
            merged = deduplicate_findings(checked)
            contexts = dict(grading_contexts) if grading_contexts is not None else {}
            if contexts and (self.grading_policy is None or contexts.keys() - {r["dedup_key"] for r in merged}):
                raise FindingIngestionError("grading context is not bound to the batch")
            # Validate grading contexts even on retries; only new anchors get a grade.
            values = {
                r["dedup_key"]: (graded_orm_values(r, contexts[r["dedup_key"]], self.grading_policy)
                                 if r["dedup_key"] in contexts else to_orm_values(r))
                for r in merged
            }
            with self.sessions.begin() as session:
                dialect = session.get_bind().dialect.name
                if dialect == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                elif dialect == "postgresql":
                    # A waiter must see the previous holder's newly inserted anchor.
                    session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                else:
                    raise FindingIngestionError("unsupported finding storage")
                scope = session.scalar(select(AuthScope).where(AuthScope.id == scope_id).with_for_update())
                if scope is None or scope.status != "ENABLED" or scope.deleted != 0:
                    raise FindingIngestionError("asset is not enabled")
                task = session.scalar(select(DetectionTask).where(DetectionTask.id == task_id).with_for_update())
                if task is None or task.scope_id != scope_id:
                    raise FindingIngestionError("task does not belong to asset")
                if page_guard is not None:
                    page_id, url, domain, scope_type, scope_value = page_guard
                    page = session.scalar(select(SitePage).where(SitePage.id == page_id).with_for_update())
                    if (page is None or (page.scope_id, page.url, page.status) != (scope_id, url, "ACTIVE")
                            or (scope.domain, scope.scope_type, scope.scope_value) != (domain, scope_type, scope_value)
                            or any(r["page_id"] != page_id for r in checked)):
                        raise FindingIngestionError("registered page or asset changed during detection")
                page_ids = {r["page_id"] for r in checked if r["page_id"] is not None}
                if page_ids and set(session.scalars(select(SitePage.id).where(
                    SitePage.scope_id == scope_id, SitePage.id.in_(page_ids)
                ).order_by(SitePage.id).with_for_update())) != page_ids:
                    raise FindingIngestionError("page does not belong to asset")
                if not merged:
                    return []
                rows = session.scalars(select(Finding).where(
                    Finding.scope_id == scope_id, Finding.dedup_key.in_(values)
                ).order_by(Finding.id).with_for_update()).all()
                by_key = {row.dedup_key: row for row in rows}
                if len(by_key) != len(rows):
                    raise FindingIngestionError("multiple historical anchors require reconciliation")
                result = []
                alert_tx = AlertTransaction(session) if alert_principal is not None else None
                for incoming in merged:
                    key = incoming["dedup_key"]
                    row = by_key.get(key)
                    if row is None:
                        fields = values[key]
                        fields["raw_data"][INGESTION_MARKER] = {"detector_severity": incoming["severity"]}
                        _check_size(fields["raw_data"]["observations"])
                        row = Finding(**fields)
                        session.add(row)
                        session.flush()
                        saved = IngestedFinding(row.id, key, row.occurrence, "INSERTED")
                    else:
                        saved = self._merge(session, row, incoming)
                    if alert_tx is not None and saved.change != "UNCHANGED":
                        self.alert_service.process_transaction(alert_tx, row.id, alert_principal, alert_at)
                    result.append(saved)
                return result
        except FindingIngestionError:
            raise
        except VerificationError:
            raise FindingIngestionError("finding alert processing failed") from None
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise FindingIngestionError("invalid normalized batch or conflicting history") from None
        except SQLAlchemyError:
            raise FindingIngestionError("finding transaction failed") from None

    @staticmethod
    def _merge(session, row, incoming):
        prior = _history(row)
        combined = deduplicate_findings([incoming], existing=[prior])[0]
        observations = combined["raw"]["observations"]
        _check_size(observations)
        raw = dict(row.raw_data, observations=observations)
        raw.setdefault(INGESTION_MARKER, {"detector_severity": prior["severity"]})
        fields = to_orm_values(combined)
        first_seen, last_seen = fields["first_seen"], max(fields["last_seen"], row.last_seen)
        change = "UNCHANGED"
        if (raw, first_seen, last_seen, fields["occurrence"]) != (
            row.raw_data, row.first_seen, row.last_seen, row.occurrence
        ):
            # Preserve the anchor's task/representative, status, grade, audit FKs and fix timestamp.
            session.execute(update(Finding).where(Finding.id == row.id).values(
                raw_data=raw, first_seen=first_seen, last_seen=last_seen,
                occurrence=fields["occurrence"], updated_at=row.updated_at,
            ))
            change = "UPDATED"
        return IngestedFinding(row.id, row.dedup_key, fields["occurrence"], change)


def _check_size(observations):
    if len(json.dumps(observations, ensure_ascii=True, separators=(",", ":"))) > MAX_PROVENANCE_BYTES:
        raise FindingIngestionError("finding provenance exceeds storage limit")
