"""T25 checks for exact, covered Nuclei/ZAP rules from a trusted execution provider."""
from dataclasses import dataclass
import re

from src.services.discovery import Scope
from src.services.findings import normalize_finding, normalize_url
from src.services.retesting import RetestResult, fingerprint
from src.tools.contract import ToolResult
from src.tools.page_retest import page_authorization


@dataclass(frozen=True)
class ToolRetestBinding:
    scope_id: int
    dedup_key: str
    kind: str
    tool_id: str
    url: str
    rule_id: str
    qualifier: str | None  # Nuclei matcher or ZAP parameter (None when absent).
    rules_sha256: str


@dataclass(frozen=True)
class CoveredToolRun:
    """Server-side attestation, not fields read from an HTTP body or adapter result."""
    binding: ToolRetestBinding
    observed_at: str
    result: ToolResult
    coverage_complete: bool
    fresh_isolated_run: bool


class ToolRetestRunner:
    def __init__(self, binding_provider, run_provider):
        self.binding_provider, self.run_provider = binding_provider, run_provider

    def run(self, snapshot):
        unknown = RetestResult("INCONCLUSIVE")
        binding = self.binding_provider(snapshot.finding_id)
        if not isinstance(binding, ToolRetestBinding) or (
            binding.scope_id, binding.dedup_key, binding.kind, binding.tool_id
        ) != (snapshot.scope_id, snapshot.dedup_key, snapshot.kind, snapshot.tool_name):
            return unknown
        if (binding.tool_id not in {"nuclei", "zap"} or binding.kind not in {"VULN", "WEAK_CONFIG"}
                or not isinstance(binding.rule_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", binding.rule_id)
                or (binding.qualifier is not None and
                    (not isinstance(binding.qualifier, str) or not 0 < len(binding.qualifier) <= 200))
                or not isinstance(binding.rules_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", binding.rules_sha256)):
            return unknown
        try:
            page_authorization(snapshot.scope_id, binding.url, Scope(snapshot.domain, snapshot.scope_type, snapshot.scope_value))
            rule = ({"template-id": binding.rule_id, "matcher-name": binding.qualifier} if binding.tool_id == "nuclei"
                    else {"pluginId": binding.rule_id, "param": binding.qualifier})

            def normalized(item):
                return normalize_finding(item, task_id=snapshot.task_id, scope_id=snapshot.scope_id,
                                         tool_name=snapshot.tool_name, seen_at=snapshot.latest_at)

            expected = normalized({"type": binding.kind, "url": binding.url, "raw": rule})
            if (expected["dedup_key"], expected["raw"]["identity"]["target_sha256"]) != (
                snapshot.dedup_key, snapshot.target_sha256
            ):
                return unknown
            run = self.run_provider(binding)
            if (not isinstance(run, CoveredToolRun) or run.binding != binding or run.coverage_complete is not True
                    or run.fresh_isolated_run is not True or not isinstance(run.result, ToolResult)
                    or run.result.status != "SUCCESS" or run.result.tool_id != binding.tool_id
                    or run.result.error is not None or len(run.result.findings) > 1000
                    or self.binding_provider(snapshot.finding_id) != binding):
                return unknown
            keys, sources = set(), []
            for item in run.result.findings:
                # Malformed results may hide the original rule; do not treat that as a clean run.
                raw = item.get("raw")
                field = "template-id" if binding.tool_id == "nuclei" else "pluginId"
                if (not isinstance(raw, dict) or not isinstance(raw.get(field), str) or not raw[field]
                        or item.get("type") not in {"VULN", "WEAK_CONFIG"}):
                    return unknown
                page_authorization(snapshot.scope_id, item.get("url"),
                                   Scope(snapshot.domain, snapshot.scope_type, snapshot.scope_value))
                observed = normalized(item)
                if observed["raw"]["identity"] == expected["raw"]["identity"] and observed["type"] != binding.kind:
                    return unknown
                keys.add(observed["dedup_key"])
                sources.append(observed["raw"]["source_sha256"])
            return RetestResult("MATCH" if binding.dedup_key in keys else "CLEAN", run.observed_at,
                                fingerprint([normalize_url(binding.url), sorted(sources)]), binding.rules_sha256, True)
        except (TypeError, ValueError, AttributeError):
            return unknown
