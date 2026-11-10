"""Execute original T20 rules on freshly supplied, authorized content (no fetching here)."""
from dataclasses import asdict, dataclass
import hashlib

from src.services.discovery import Scope, is_in_scope
from src.services.retesting import RetestResult, fingerprint
from src.services.findings import normalize_url
from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner


@dataclass(frozen=True)
class SensitiveRetestTarget:
    scope_id: int
    dedup_key: str
    url: str
    rule_id: str
    rules_sha256: str


@dataclass(frozen=True)
class FreshDocument:
    document: ScanDocument
    observed_at: str
    status_code: int = 200
    coverage_complete: bool = True


def rules_fingerprint(scanner):
    return fingerprint([asdict(rule) for rule in scanner.rules])


class SensitiveRetestRunner:
    def __init__(self, scanner, bindings, document_provider):
        self.scanner, self.bindings, self.document_provider = scanner, dict(bindings), document_provider

    def run(self, snapshot):
        unknown = RetestResult("INCONCLUSIVE")
        binding = self.bindings.get(snapshot.finding_id)
        if not isinstance(binding, SensitiveRetestTarget) or snapshot.kind != "SENSITIVE" or snapshot.tool_name != "sensitive-scanner":
            return unknown
        if binding.scope_id != snapshot.scope_id or binding.dedup_key != snapshot.dedup_key:
            return unknown
        # T20 uses an opaque URL hash; T21 then hashes that identity namespace.
        opaque = hashlib.sha256(binding.url.encode("utf-8")).hexdigest()
        expected_target = hashlib.sha256(("sha256:" + opaque).encode("utf-8")).hexdigest()
        if snapshot.target_sha256 != expected_target:
            return unknown
        canonical = normalize_url(binding.url)  # Reject credentialed/non-HTTP input before obtaining content.
        scope = Scope(snapshot.domain, snapshot.scope_type, snapshot.scope_value)
        if not is_in_scope(canonical, scope):
            return unknown
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(binding.url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        allowed = AuthorizedScope(str(snapshot.scope_id), origin,
                                  snapshot.scope_value if snapshot.scope_type == "PATH" else "/")
        if not allowed.allows(binding.url):
            return unknown
        digest = rules_fingerprint(self.scanner)
        selected = [rule for rule in self.scanner.rules if rule.id == binding.rule_id and rule.enabled
                    and rule.scope_bind in (None, str(snapshot.scope_id))]
        if digest != binding.rules_sha256 or len(selected) != 1:
            return unknown
        fresh = self.document_provider(binding)
        if (not isinstance(fresh, FreshDocument) or fresh.status_code != 200 or fresh.coverage_complete is not True
                or not isinstance(fresh.document, ScanDocument) or fresh.document.url != binding.url):
            return unknown
        detector = SensitiveScanner(selected, regex_timeout=self.scanner.regex_timeout,
                                    max_content_chars=self.scanner.max_content_chars, max_findings=self.scanner.max_findings)
        findings = detector.scan(fresh.document, [allowed])
        if rules_fingerprint(self.scanner) != digest or self.bindings.get(snapshot.finding_id) != binding:
            return unknown
        content = dict(text=fresh.document.text, headers=dict(fresh.document.headers))
        return RetestResult("MATCH" if findings else "CLEAN", fresh.observed_at, fingerprint(content), digest, True)
