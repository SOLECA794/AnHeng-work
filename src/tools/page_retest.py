"""Fresh T18/T19 retesting over authorized documents provided by the host."""
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from src.services.discovery import Page, Scope, content_hash, is_in_scope
from src.services.findings import normalize_finding, normalize_url
from src.services.retesting import RetestResult, fingerprint
from src.tools.external_link import _RULES, scan_page
from src.tools.sensitive_retest import FreshDocument
from src.tools.sensitive import AuthorizedScope, ScanDocument
from src.tools.tamper import Baseline, detect_tamper


MAX_PAGE_CHARS = 500_000
TAMPER_TOOL = "tamper-detector"
LINK_TOOL = "external-link-detector"
PAGE_TOOLS = {
    "TAMPER": TAMPER_TOOL, "MALICIOUS_LINK": LINK_TOOL, "MALICIOUS_SCRIPT": LINK_TOOL,
}


def page_authorization(scope_id, url, scope):
    canonical = normalize_url(url)
    if scope.scope_type not in {"WHOLE", "PATH", "SUBDOMAIN"} or not is_in_scope(canonical, scope):
        raise ValueError("page is outside the approved scope")
    parts = urlsplit(canonical)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    allowed = AuthorizedScope(str(scope_id), origin, scope.scope_value if scope.scope_type == "PATH" else "/")
    if not allowed.allows(url):
        raise ValueError("page path is outside the approved scope")
    return allowed


@dataclass(frozen=True)
class PageRetestBinding:
    scope_id: int
    dedup_key: str
    page_url: str
    kind: str
    target_url: str
    rule_id: str
    rules_sha256: str


def normalize_page_finding(source, *, scope_id, task_id, page_url, seen_at, page_id=None):
    """T18/T19 identity includes the host page; changing evidence does not create a new risk."""
    kind = source.finding_type
    rule_id = "CONTENT_HASH" if kind == "TAMPER" else source.rule_id
    normalized = normalize_finding(
        {"type": kind, "url": source.url, "severity": source.severity, "evidence": source.evidence,
         "page_id": page_id},
        scope_id=scope_id, task_id=task_id, tool_name=PAGE_TOOLS[kind], seen_at=seen_at,
        evidence_feature={"page": normalize_url(page_url), "rule": rule_id},
    )
    return normalized


def _blacklist(values):
    if not isinstance(values, (tuple, list, frozenset, set)) or len(values) > 1000:
        raise ValueError("invalid blacklist")
    if any(not isinstance(value, str) or len(value) > 253 or
           urlsplit(normalize_url("https://" + value)).hostname != value.lower() for value in values):
        raise ValueError("invalid blacklist domain")
    return tuple(sorted({value.lower() for value in values}))


def tamper_policy_sha256(baseline_hash):
    if (not isinstance(baseline_hash, str) or len(baseline_hash) != 64
            or any(char not in "0123456789abcdef" for char in baseline_hash)):
        raise ValueError("invalid approved baseline")
    return fingerprint({"engine": "T18-content-hash-v1", "baseline_sha256": baseline_hash})


def link_policy_sha256(blacklist):
    return fingerprint({"engine": "T19-page-rules-v1", "blacklist": _blacklist(blacklist)})


class PageRetestRunner:
    """Providers resolve reviewed bindings and obtain fresh authorized content; no network here."""
    def __init__(self, binding_provider, document_provider, *, baseline_provider=None, blacklist_provider=None):
        self.binding_provider = binding_provider
        self.document_provider = document_provider
        self.baseline_provider = baseline_provider
        self.blacklist_provider = blacklist_provider

    def run(self, snapshot):
        unknown = RetestResult("INCONCLUSIVE")
        if PAGE_TOOLS.get(snapshot.kind) != snapshot.tool_name:
            return unknown
        binding = self.binding_provider(snapshot.finding_id)
        if not isinstance(binding, PageRetestBinding) or (
            binding.scope_id, binding.dedup_key, binding.kind
        ) != (snapshot.scope_id, snapshot.dedup_key, snapshot.kind):
            return unknown
        try:
            canonical = normalize_url(binding.page_url)
            target = normalize_url(binding.target_url)
            page_authorization(snapshot.scope_id, binding.page_url,
                               Scope(snapshot.domain, snapshot.scope_type, snapshot.scope_value))
            # An external URL is an evidence identity only. It is never fetched.
            expected = normalize_finding(
                {"type": binding.kind, "url": target}, scope_id=snapshot.scope_id,
                task_id=snapshot.task_id, tool_name=snapshot.tool_name, seen_at=snapshot.latest_at,
                evidence_feature={"page": canonical, "rule": binding.rule_id},
            )
            if (expected["dedup_key"], expected["raw"]["identity"]["target_sha256"]) != (
                snapshot.dedup_key, snapshot.target_sha256
            ):
                return unknown
            if binding.kind == "TAMPER":
                if not self.baseline_provider or target != canonical or binding.rule_id != "CONTENT_HASH":
                    return unknown
                policy = self.baseline_provider(binding)
                digest = tamper_policy_sha256(policy)
                current_digest = lambda: tamper_policy_sha256(self.baseline_provider(binding))
            else:
                if not self.blacklist_provider or _RULES.get(binding.rule_id, (None,))[0] != binding.kind:
                    return unknown
                policy = _blacklist(self.blacklist_provider(binding))
                digest = link_policy_sha256(policy)
                current_digest = lambda: link_policy_sha256(self.blacklist_provider(binding))
            if digest != binding.rules_sha256:
                return unknown
            fresh = self.document_provider(binding)
            if (not isinstance(fresh, FreshDocument) or fresh.status_code != 200 or fresh.coverage_complete is not True
                    or not isinstance(fresh.document, ScanDocument) or fresh.document.url != binding.page_url
                    or not isinstance(fresh.document.text, str) or len(fresh.document.text) > MAX_PAGE_CHARS):
                return unknown
            body = fresh.document.text
            if binding.kind == "TAMPER":
                page = Page(binding.page_url, canonical, None, content_hash(body), 0)
                matched = bool(detect_tamper([page], {canonical: Baseline(canonical, policy)}))
            else:
                findings = scan_page(body, binding.page_url, site_host=urlsplit(canonical).hostname, blacklist=policy)
                matched = any(
                    item.finding_type == binding.kind and item.rule_id == binding.rule_id
                    and normalize_url(item.url) == target for item in findings
                )
            if current_digest() != digest or self.binding_provider(snapshot.finding_id) != binding:
                return unknown
            return RetestResult("MATCH" if matched else "CLEAN", fresh.observed_at, content_hash(body), digest, True)
        except (ValueError, TypeError):
            return unknown
