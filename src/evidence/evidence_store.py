"""Evidence-aware classification for agent results.

This is the M3 bridge layer. It does not replace SharedMemoryStore; it turns
raw AgentResult outputs and tool trajectories into explicit EvidenceItem
records that can be stored in memory metadata, used by the summarizer, and fed
back to replanning.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..orchestrator.schemas import AgentResult, AgentStatus, EvidenceItem, EvidenceLevel, SubTask, TaskType


class EvidenceStore:
    """Classify agent outputs into evidence-aware claims."""

    def annotate_result(self, result: AgentResult, task: SubTask | None = None) -> AgentResult:
        """Attach evidence items to an AgentResult and return it."""
        if result.evidence_items:
            return result
        result.evidence_items = self.build_evidence_items(result, task=task)
        return result

    def build_evidence_items(self, result: AgentResult, task: SubTask | None = None) -> list[EvidenceItem]:
        """Build evidence items for one result.

        Structured GIS/RS tools may emit multiple claim-level checks. Preserve
        those checks as separate evidence items so one invalid claim does not
        make every valid correction from the same task look rejected.
        """
        if result.status in (AgentStatus.FAILED, AgentStatus.TIMEOUT):
            return [self._fallback_evidence_item(result, task=task, sources=[])]

        structured_items = self._structured_evidence_items(result, task=task)
        if structured_items:
            return structured_items

        sources = self.extract_sources(result)
        level, rationale = self.classify(result, sources=sources, task=task)
        source = sources[0].get("url") or sources[0].get("title", "") if sources else ""

        return [self._fallback_evidence_item(result, task=task, sources=sources, level=level, rationale=rationale, source=source)]

    def _fallback_evidence_item(
        self,
        result: AgentResult,
        task: SubTask | None = None,
        sources: list[dict[str, Any]] | None = None,
        level: EvidenceLevel | None = None,
        rationale: str | None = None,
        source: str = "",
    ) -> EvidenceItem:
        sources = sources or []
        if level is None or rationale is None:
            level, rationale = self.classify(result, sources=sources, task=task)
        return EvidenceItem(
            claim=self._claim_from_output(result.output),
            level=level,
            source=source,
            rationale=rationale,
            task_id=result.task_id,
            confidence=result.confidence,
            metadata={
                "sources": sources,
                "source_count": len(sources),
                "task_type": task.task_type.value if task else "",
                "status": result.status.value,
            },
        )

    def classify(
        self,
        result: AgentResult,
        sources: list[dict[str, Any]] | None = None,
        task: SubTask | None = None,
    ) -> tuple[EvidenceLevel, str]:
        """Assign an evidence level using execution status, tool evidence, and task type."""
        sources = sources or []
        output_text = str(result.output or "").lower()

        if result.status in (AgentStatus.FAILED, AgentStatus.TIMEOUT):
            return EvidenceLevel.REJECTED, f"Task ended with status={result.status.value}."

        tool_level = self._level_from_structured_tool(result, sources=sources)
        if tool_level is not None:
            return tool_level, "Structured tool evidence level was capped by source provenance."

        if self._looks_rejected(output_text):
            return EvidenceLevel.REJECTED, "Output explicitly states that the claim is unsupported or the tool failed."

        has_external_sources = any(not self._is_mock_source(src) for src in sources)
        has_mock_sources = any(self._is_mock_source(src) for src in sources)
        is_validation_task = bool(task and task.task_type in (TaskType.VERIFY, TaskType.GEO_VALIDATION))

        if is_validation_task and has_external_sources and result.confidence >= 0.7:
            return EvidenceLevel.VERIFIED, "Validation task succeeded with external source evidence."

        if has_external_sources:
            return EvidenceLevel.EVIDENCE_BACKED, "Result is supported by external tool/search evidence."

        if has_mock_sources:
            return EvidenceLevel.SPECULATIVE, "Result used mock sources, so it is useful for workflow testing but not real evidence."

        if is_validation_task and result.confidence >= 0.7:
            return EvidenceLevel.SPECULATIVE, "Validation-style task succeeded, but no source evidence was available."

        return EvidenceLevel.SPECULATIVE, "No external evidence source was found in the tool trajectory."

    def summarize(self, results: list[AgentResult]) -> dict[str, Any]:
        """Group evidence counts and claims by evidence level."""
        counts: Counter[str] = Counter()
        claims_by_level: dict[str, list[dict[str, Any]]] = {
            level.value: [] for level in EvidenceLevel
        }
        for result in results:
            for item in result.evidence_items:
                counts[item.level.value] += 1
                claims_by_level[item.level.value].append(item.to_dict())
        return {
            "counts": dict(counts),
            "claims_by_level": claims_by_level,
        }

    def build_replan_feedback(self, results: list[AgentResult]) -> str:
        """Create compact feedback for replanning."""
        rejected = []
        speculative = []
        for result in results:
            for item in result.evidence_items:
                if item.level == EvidenceLevel.REJECTED:
                    rejected.append(f"- {item.task_id}: {item.rationale} Claim: {item.claim[:160]}")
                elif item.level == EvidenceLevel.SPECULATIVE:
                    speculative.append(f"- {item.task_id}: {item.rationale} Claim: {item.claim[:160]}")

        parts = []
        if rejected:
            parts.append("Rejected / unsupported results:\n" + "\n".join(rejected[:5]))
        if speculative:
            parts.append("Speculative results needing verification:\n" + "\n".join(speculative[:5]))
        return "\n\n".join(parts)

    def extract_sources(self, result: AgentResult) -> list[dict[str, Any]]:
        """Extract source-like objects from tool trajectory."""
        sources: list[dict[str, Any]] = []
        for step in result.trajectory:
            if step.get("role") != "tool":
                continue
            tool_name = step.get("name", "")
            payload = step.get("result")
            if not isinstance(payload, dict):
                continue

            if isinstance(payload.get("results"), list):
                for item in payload["results"]:
                    if isinstance(item, dict):
                        official_sources = item.get("official_sources")
                        if isinstance(official_sources, list):
                            for official_source in official_sources:
                                if isinstance(official_source, dict):
                                    self._append_source(sources, {
                                        "tool": tool_name,
                                        "url": official_source.get("url", ""),
                                        "title": official_source.get("title", ""),
                                        "snippet": item.get("dataset", "") or item.get("method", ""),
                                    })
                        self._append_source(sources, {
                            "tool": tool_name,
                            "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                        })

            if isinstance(payload.get("papers"), list):
                for paper in payload["papers"]:
                    if isinstance(paper, dict):
                        self._append_source(sources, {
                            "tool": tool_name,
                            "url": paper.get("pdf_url", "") or paper.get("url", ""),
                            "title": paper.get("title", ""),
                            "snippet": str(paper.get("summary", ""))[:300],
                        })

        seen = set()
        unique = []
        for source in sources:
            key = (source.get("url", ""), source.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            unique.append(source)
        return unique

    def _append_source(self, sources: list[dict[str, Any]], source: dict[str, Any]) -> None:
        """Append only non-empty sources so classification cannot be inflated by blanks."""
        if source.get("url") or source.get("title") or source.get("snippet"):
            sources.append(source)

    def _claim_from_output(self, output: Any) -> str:
        text = str(output or "").strip()
        if len(text) <= 500:
            return text
        return text[:500]

    def _looks_rejected(self, output_text: str) -> bool:
        markers = [
            "tool failed",
            "failed:",
            "无法验证",
            "无法获取",
            "不支持",
            "not supported",
            "insufficient evidence",
        ]
        return any(marker in output_text for marker in markers)

    def _is_mock_source(self, source: dict[str, Any]) -> bool:
        url = str(source.get("url", "")).lower()
        title = str(source.get("title", "")).lower()
        snippet = str(source.get("snippet", "")).lower()
        return "example.com/mock" in url or "mock result" in title or "mock search result" in snippet

    def _is_external_url(self, source: str) -> bool:
        normalized = str(source or "").lower()
        return normalized.startswith("http://") or normalized.startswith("https://")

    def _structured_source_type(
        self,
        tool_name: str,
        payload: dict[str, Any],
        source: str = "",
        official_sources: list[Any] | None = None,
    ) -> str:
        explicit = str(payload.get("source_type") or "")
        if explicit:
            if official_sources and self._is_external_url(source):
                return f"{explicit}_with_official_url"
            return explicit
        if tool_name == "official_source_search":
            return "official_search"
        if tool_name == "official_doc_fetcher" or payload.get("source_type") == "official_doc":
            return "official_doc"
        registry_type = payload.get("registry_type")
        if registry_type == "geo_plan_validation" or str(source).startswith("geo-registry://"):
            return "registry_heuristic"
        if registry_type in ("dataset", "method"):
            return "registry_curated_with_official_url" if self._is_external_url(source) else "registry_heuristic"
        return "structured_tool"

    def _cap_structured_level(
        self,
        level: EvidenceLevel,
        tool_name: str,
        payload: dict[str, Any],
        source: str = "",
    ) -> EvidenceLevel:
        """Prevent local registries from promoting hints into verified evidence."""
        if level == EvidenceLevel.REJECTED:
            return EvidenceLevel.REJECTED

        registry_type = payload.get("registry_type")
        if registry_type == "geo_plan_validation" or str(source).startswith("geo-registry://"):
            return EvidenceLevel.SPECULATIVE

        if registry_type in ("dataset", "method"):
            if self._is_external_url(source):
                return EvidenceLevel.EVIDENCE_BACKED
            return EvidenceLevel.SPECULATIVE

        if tool_name == "official_source_search" and level == EvidenceLevel.VERIFIED:
            return EvidenceLevel.EVIDENCE_BACKED

        if tool_name == "official_doc_fetcher" or payload.get("source_type") == "official_doc":
            if level == EvidenceLevel.VERIFIED:
                return EvidenceLevel.EVIDENCE_BACKED
            return level

        return level

    def _structured_evidence_items(self, result: AgentResult, task: SubTask | None = None) -> list[EvidenceItem]:
        """Convert structured registry payloads into claim-level evidence items."""
        items: list[EvidenceItem] = []
        task_type = task.task_type.value if task else ""

        for step in result.trajectory:
            if step.get("role") != "tool":
                continue
            tool_name = step.get("name", "")
            payload = step.get("result")
            if not isinstance(payload, dict):
                continue

            if isinstance(payload.get("checks"), list):
                source = self._source_for_structured_payload(tool_name, payload)
                source_type = self._structured_source_type(tool_name, payload, source=source)
                for check in payload["checks"]:
                    if not isinstance(check, dict):
                        continue
                    raw_level = self._normalize_level(check.get("level"))
                    level = self._cap_structured_level(raw_level, tool_name, payload, source=source)
                    rationale = str(check.get("reason", "") or "Structured GIS/remote-sensing validation check.")
                    if source_type.startswith("registry_"):
                        rationale = f"{rationale} Source type: {source_type}; external verification is required."
                    items.append(EvidenceItem(
                        claim=str(check.get("claim", "") or "Structured validation check"),
                        level=level,
                        source=source,
                        rationale=rationale,
                        task_id=result.task_id,
                        confidence=result.confidence,
                        metadata={
                            "tool": tool_name,
                            "task_type": task_type,
                            "status": result.status.value,
                            "fix": check.get("fix", ""),
                            "registry_type": payload.get("registry_type", ""),
                            "source_type": source_type,
                            "raw_evidence_level": raw_level.value,
                            "requires_external_verification": bool(payload.get("requires_external_verification", False)),
                        },
                    ))
                continue

            registry_type = payload.get("registry_type")
            if (tool_name == "official_doc_fetcher" or payload.get("source_type") == "official_doc") and isinstance(payload.get("results"), list):
                raw_level = self._normalize_level(payload.get("evidence_level"))
                for record in payload["results"]:
                    if not isinstance(record, dict):
                        continue
                    snippets = record.get("snippets") if isinstance(record.get("snippets"), list) else []
                    has_query_match = bool(payload.get("match_count", 0)) or any(
                        isinstance(snippet, dict) and snippet.get("match_score", 0) > 0
                        for snippet in snippets
                    )
                    source = record.get("url", "")
                    base_level = self._cap_structured_level(raw_level, tool_name, payload, source=source)
                    if task and task.task_type in (TaskType.VERIFY, TaskType.GEO_VALIDATION) and has_query_match and result.confidence >= 0.7:
                        level = EvidenceLevel.VERIFIED
                    else:
                        level = base_level if has_query_match else EvidenceLevel.SPECULATIVE
                    if snippets:
                        claim = str(snippets[0].get("text", "") or record.get("snippet", "") or record.get("title", ""))
                    else:
                        claim = str(record.get("snippet", "") or record.get("title", "") or "Official document fetched without query match.")
                    items.append(EvidenceItem(
                        claim=self._claim_from_output(claim),
                        level=level,
                        source=source,
                        rationale=(
                            "Fetched official documentation page and extracted query-matched snippets."
                            if snippets else
                            "Fetched official documentation page, but no query-matched snippet was found."
                        ),
                        task_id=result.task_id,
                        confidence=result.confidence,
                        metadata={
                            "tool": tool_name,
                            "task_type": task_type,
                            "status": result.status.value,
                            "source_type": "official_doc",
                            "title": record.get("title", ""),
                            "official_domain": record.get("official_domain", ""),
                            "content_chars": record.get("content_chars", 0),
                            "match_count": payload.get("match_count", 0),
                            "has_query_match": has_query_match,
                            "snippets": snippets[:3],
                        },
                    ))
                continue

            if registry_type in ("dataset", "method") and isinstance(payload.get("results"), list):
                raw_level = self._normalize_level(payload.get("evidence_level"))
                for record in payload["results"]:
                    if not isinstance(record, dict):
                        continue
                    sources = record.get("official_sources") if isinstance(record.get("official_sources"), list) else []
                    source = ""
                    if sources and isinstance(sources[0], dict):
                        source = sources[0].get("url", "") or sources[0].get("title", "")
                    level = self._cap_structured_level(raw_level, tool_name, payload, source=source)
                    source_type = self._structured_source_type(tool_name, payload, source=source, official_sources=sources)
                    claim = record.get("dataset") or record.get("method") or str(record)[:300]
                    rationale_parts = record.get("limitations") or record.get("valid_for") or []
                    rationale = "; ".join(str(part) for part in rationale_parts[:2]) if isinstance(rationale_parts, list) else str(rationale_parts)
                    if source_type == "registry_curated_with_official_url":
                        rationale = (rationale + " " if rationale else "") + "Curated registry record with an official URL; source-backed but not page-grounded."
                    else:
                        rationale = (rationale + " " if rationale else "") + "Curated registry record; treat as heuristic until verified by external retrieval."
                    items.append(EvidenceItem(
                        claim=str(claim),
                        level=level,
                        source=source,
                        rationale=rationale,
                        task_id=result.task_id,
                        confidence=result.confidence,
                        metadata={
                            "tool": tool_name,
                            "task_type": task_type,
                            "status": result.status.value,
                            "registry_type": registry_type,
                            "sources": sources,
                            "source_type": source_type,
                            "raw_evidence_level": raw_level.value,
                            "requires_external_verification": bool(payload.get("requires_external_verification", True)),
                        },
                    ))

        return items

    def _source_for_structured_payload(self, tool_name: str, payload: dict[str, Any]) -> str:
        results = payload.get("results")
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    return item.get("url", "") or item.get("title", "") or tool_name
        return tool_name

    def _normalize_level(self, value: Any) -> EvidenceLevel:
        normalized = str(value or "").lower()
        if normalized == "verified":
            return EvidenceLevel.VERIFIED
        if normalized == "evidence_backed":
            return EvidenceLevel.EVIDENCE_BACKED
        if normalized == "rejected":
            return EvidenceLevel.REJECTED
        return EvidenceLevel.SPECULATIVE

    def _level_from_structured_tool(self, result: AgentResult, sources: list[dict[str, Any]] | None = None) -> EvidenceLevel | None:
        """Read explicit evidence levels from structured tools when available."""
        sources = sources or []
        priority = {
            EvidenceLevel.REJECTED: 4,
            EvidenceLevel.VERIFIED: 3,
            EvidenceLevel.EVIDENCE_BACKED: 2,
            EvidenceLevel.SPECULATIVE: 1,
        }
        best: EvidenceLevel | None = None
        for step in result.trajectory:
            if step.get("role") != "tool":
                continue
            payload = step.get("result")
            if not isinstance(payload, dict):
                continue
            tool_name = step.get("name", "")
            source = self._source_for_structured_payload(tool_name, payload)
            if payload.get("registry_type") in ("dataset", "method"):
                for external_source in sources:
                    candidate_source = external_source.get("url", "") or external_source.get("title", "")
                    if self._is_external_url(candidate_source):
                        source = candidate_source
                        break

            levels = []
            if payload.get("evidence_level"):
                levels.append(str(payload["evidence_level"]))
            for check in payload.get("checks", []) or []:
                if isinstance(check, dict) and check.get("level"):
                    levels.append(str(check["level"]))

            for raw_level in levels:
                candidate = self._cap_structured_level(
                    self._normalize_level(raw_level),
                    tool_name,
                    payload,
                    source=source,
                )
                if best is None or priority[candidate] > priority[best]:
                    best = candidate
        return best
