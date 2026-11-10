"""Run D's offline page path on a host-collected document and commit safe findings."""
from urllib.parse import urlsplit

from src.models import AuthScope, DetectionTask, SitePage
from src.repositories.finding_ingestion import FindingIngestionError
from src.services.discovery import Page, Scope, content_hash
from src.services.finding_verification import _id, _timestamp
from src.services.findings import normalize_finding, normalize_url
from src.tools.external_link import scan_page
from src.tools.page_retest import MAX_PAGE_CHARS, _blacklist, normalize_page_finding, page_authorization, tamper_policy_sha256
from src.tools.sensitive import ScanDocument
from src.tools.sensitive_retest import FreshDocument
from src.tools.tamper import Baseline, detect_tamper


class PageDetectionPipeline:
    def __init__(self, sessions, ingestion, scanner):
        self.sessions, self.ingestion, self.scanner = sessions, ingestion, scanner

    def ingest_document(self, scope_id, task_id, page_id, fresh, *, blacklist=None, baseline_hash=None,
                        alert_principal=None):
        """The trusted host supplies collected content and optional C detector policies."""
        if not all(_id(value) for value in (scope_id, task_id, page_id)) or (
            not isinstance(fresh, FreshDocument) or fresh.coverage_complete is not True or fresh.status_code != 200
            or not isinstance(fresh.observed_at, str)
            or not isinstance(fresh.document, ScanDocument) or not isinstance(fresh.document.text, str)
            or len(fresh.document.text) > MAX_PAGE_CHARS
        ):
            raise FindingIngestionError("page observation is incomplete or invalid")
        doc = fresh.document
        at = _timestamp(fresh.observed_at)
        with self.sessions() as session:
            asset = session.get(AuthScope, scope_id)
            task = session.get(DetectionTask, task_id)
            page = session.get(SitePage, page_id)
            if (asset is None or asset.deleted != 0 or asset.status != "ENABLED"
                    or task is None or task.scope_id != scope_id
                    or page is None or (page.scope_id, page.url, page.status) != (scope_id, doc.url, "ACTIVE")):
                raise FindingIngestionError("observation is not bound to a registered authorized page")
            scope = Scope(asset.domain, asset.scope_type, asset.scope_value)
        allowed = page_authorization(scope_id, doc.url, scope)
        canonical = normalize_url(doc.url)
        records = [
            normalize_finding(dict(item, page_id=page_id), task_id=task_id, scope_id=scope_id,
                              tool_name="sensitive-scanner", seen_at=at)
            for item in self.scanner.scan(doc, [allowed])
        ]
        page_results = []
        if baseline_hash is not None:
            tamper_policy_sha256(baseline_hash)
            page_results.extend(detect_tamper(
                [Page(doc.url, canonical, None, content_hash(doc.text), 0)],
                {canonical: Baseline(canonical, baseline_hash, page_id)},
            ))
        if blacklist is not None:
            page_results.extend(scan_page(doc.text, doc.url, site_host=urlsplit(canonical).hostname,
                                          blacklist=_blacklist(blacklist)))
        records.extend(normalize_page_finding(item, scope_id=scope_id, task_id=task_id,
                                              page_url=doc.url, seen_at=at, page_id=page_id)
                       for item in page_results)
        return self.ingestion.ingest(
            scope_id, task_id, records, page_guard=(page_id, doc.url, scope.domain, scope.scope_type, scope.scope_value),
            alert_principal=alert_principal,
        )
