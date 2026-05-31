"""Wiki Ingest — LLM-driven knowledge extraction and wiki page management.

Takes a research report + evidence items, decides what knowledge is worth
writing into the wiki, and executes the plan with evidence gate filtering.

Reference: Wiki Ingest Skill (https://github.com/sanyuan0704/sanyuan-skills)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .wiki_store import WikiStore

logger = logging.getLogger(__name__)

__all__ = ["WikiIngest", "IngestPlan", "IngestResult"]


@dataclass
class PageAction:
    """A planned page creation or update."""
    name: str
    category: str
    content: str = ""
    action: str = "create"  # "create" | "update"
    update_path: str = ""
    update_section: str = ""
    evidence_level: str = "speculative"
    confidence: float = 0.0
    source: str = ""


@dataclass
class IngestPlan:
    """LLM-generated ingest plan."""
    new_pages: list[PageAction] = field(default_factory=list)
    updates: list[PageAction] = field(default_factory=list)
    raw_path: str = ""


@dataclass
class IngestResult:
    """Result of executing an ingest plan."""
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    confirmation_bumps: list[str] = field(default_factory=list)
    raw_path: str = ""


class WikiIngest:
    """Manages the ingest pipeline: report → LLM plan → evidence gate → wiki update."""

    def __init__(self, wiki_store: WikiStore, policy=None):
        """
        Args:
            wiki_store: The user's WikiStore instance.
            policy: LLM policy for generating ingest plans (optional).
                    If None, uses a simplified rule-based approach.
        """
        self.wiki = wiki_store
        self.policy = policy

    async def ingest(
        self,
        report_content: str,
        query: str = "",
        evidence_items: list[Any] | None = None,
    ) -> IngestResult:
        """Full ingest pipeline: save raw → plan → execute.

        Args:
            report_content: The final research report (markdown).
            query: The original research query.
            evidence_items: List of EvidenceItem objects from the research.

        Returns:
            IngestResult with created/updated/skipped pages.
        """
        self.wiki.ensure_wiki()
        config = self.wiki.get_config()

        # Step 1: Save raw report to Layer 1
        raw_path = self.wiki.save_raw(report_content, query)
        logger.info(f"[Ingest] Raw saved: {raw_path}")

        # Step 2: Generate ingest plan
        if self.policy:
            plan = await self._plan_with_llm(report_content, evidence_items or [], config)
        else:
            plan = self._plan_rule_based(report_content, evidence_items or [], config)
        plan.raw_path = raw_path

        # Step 3: Execute plan with evidence gate
        result = self._execute_plan(plan, config)
        result.raw_path = raw_path

        # Step 4: Rebuild index
        self.wiki.rebuild_index()

        # Step 5: Log
        self.wiki._append_log(
            f"Ingest completed: created={len(result.created)}, "
            f"updated={len(result.updated)}, skipped={len(result.skipped)}"
        )

        return result

    # ── Planning ─────────────────────────────────────────────────────

    async def _plan_with_llm(
        self,
        report: str,
        evidence_items: list[Any],
        config: dict,
    ) -> IngestPlan:
        """Use LLM to analyze the report and generate an ingest plan.

        The LLM receives the report + existing wiki index + config, and returns
        a JSON plan specifying which pages to create/update.
        """
        index = self.wiki.get_index()
        existing_pages = self.wiki.list_pages()
        existing_paths = [p["path"] for p in existing_pages]

        prompt = self._build_ingest_prompt(report, index, existing_paths, config, evidence_items)

        try:
            response = await self.policy([{"role": "user", "content": prompt}])
            content = response.get("content", "")
            return self._parse_ingest_plan(content, evidence_items)
        except Exception as e:
            logger.warning(f"[Ingest] LLM planning failed: {e}, falling back to rule-based")
            return self._plan_rule_based(report, evidence_items, config)

    def _plan_rule_based(
        self,
        report: str,
        evidence_items: list[Any],
        config: dict,
    ) -> IngestPlan:
        """Rule-based ingest planning (no LLM required).

        Extracts knowledge entities from the report using keyword matching
        and creates/updates pages accordingly.
        """
        plan = IngestPlan()
        existing_pages = {p["path"]: p for p in self.wiki.list_pages()}
        categories = self.wiki.get_categories()

        # Extract sensor mentions
        sensor_keywords = {
            "landsat": ("sensors/landsat-8-9.md", "Landsat 8/9"),
            "sentinel-2": ("sensors/sentinel-2.md", "Sentinel-2"),
            "modis": ("sensors/modis.md", "MODIS"),
            "era5": ("sensors/era5.md", "ERA5"),
        }
        matched_paths: set[str] = set()
        for keyword, (path, name) in sensor_keywords.items():
            if keyword in report.lower() and path not in matched_paths:
                matched_paths.add(path)
                if path in existing_pages:
                    plan.updates.append(PageAction(
                        name=name, category="sensors", action="bump",
                        update_path=path,
                    ))
                else:
                    snippet = self._extract_entity_snippet(report, keyword)
                    plan.new_pages.append(PageAction(
                        name=name, category="sensors", action="create",
                        content=self._format_sensor_page(name, snippet, evidence_items),
                        evidence_level="evidence_backed",
                        confidence=0.7,
                    ))

        # Extract method mentions
        method_keywords = {
            "lst": ("methods/lst-retrieval.md", "LST Retrieval"),
            "ndvi": ("methods/ndvi.md", "NDVI"),
            "ndbi": ("methods/ndbi.md", "NDBI"),
            "single channel": ("methods/single-channel-lst.md", "Single Channel LST"),
            "split window": ("methods/split-window-lst.md", "Split Window LST"),
        }
        matched_method_paths: set[str] = set()
        for keyword, (path, name) in method_keywords.items():
            if keyword in report.lower() and path not in matched_method_paths:
                matched_method_paths.add(path)
                if path in existing_pages:
                    plan.updates.append(PageAction(
                        name=name, category="methods", action="bump",
                        update_path=path,
                    ))
                else:
                    snippet = self._extract_entity_snippet(report, keyword)
                    plan.new_pages.append(PageAction(
                        name=name, category="methods", action="create",
                    content=self._format_method_page(name, snippet, evidence_items),
                    evidence_level="evidence_backed",
                    confidence=0.6,
                ))

        return plan

    # ── Evidence Gate ────────────────────────────────────────────────

    def _passes_evidence_gate(self, action: PageAction, config: dict) -> bool:
        """Check if a page action passes the evidence gate.

        Only pages with sufficient evidence quality are written to the wiki.
        """
        gate = config.get("evidence_gate", {})
        min_level = gate.get("min_level", "evidence_backed")
        min_conf = gate.get("min_confidence", 0.6)

        level_rank = {"verified": 4, "evidence_backed": 3, "speculative": 2, "rejected": 1}
        action_rank = level_rank.get(action.evidence_level, 0)
        min_rank = level_rank.get(min_level, 3)

        # Pass if level meets minimum
        if action_rank >= min_rank:
            return True

        # SPECULATIVE with high confidence can still pass
        if gate.get("allow_speculative_if_high_conf") and action.evidence_level == "speculative":
            threshold = gate.get("speculative_confidence_threshold", 0.8)
            if action.confidence >= threshold:
                return True

        return False

    # ── Execution ────────────────────────────────────────────────────

    def _execute_plan(self, plan: IngestPlan, config: dict) -> IngestResult:
        """Execute the ingest plan with evidence gate filtering."""
        result = IngestResult()

        # Process new pages
        for action in plan.new_pages:
            if not self._passes_evidence_gate(action, config):
                result.skipped.append(f"{action.category}/{action.name} (evidence gate)")
                logger.info(f"[Ingest] Skipped {action.name}: evidence_level={action.evidence_level}, confidence={action.confidence:.2f}")
                continue

            try:
                path = f"{action.category}/{WikiStore._slugify(action.name)}.md"
                self.wiki.create_page(path, action.content, action.category)
                result.created.append(path)
                logger.info(f"[Ingest] Created: {path}")
            except ValueError as e:
                logger.warning(f"[Ingest] Create failed: {e}")

        # Process updates (bumps)
        for action in plan.updates:
            if action.action == "bump":
                try:
                    new_status = self.wiki.bump_confirmation(action.update_path)
                    result.confirmation_bumps.append(f"{action.update_path} → {new_status}")
                    logger.info(f"[Ingest] Bumped: {action.update_path} → {new_status}")
                except Exception as e:
                    logger.warning(f"[Ingest] Bump failed: {e}")
            elif action.action == "update":
                try:
                    self.wiki.update_page_section(action.update_path, action.update_section, action.content)
                    result.updated.append(action.update_path)
                except Exception as e:
                    logger.warning(f"[Ingest] Update failed: {e}")

        return result

    # ── Prompt Building ──────────────────────────────────────────────

    def _build_ingest_prompt(
        self,
        report: str,
        index: str,
        existing_paths: list[str],
        config: dict,
        evidence_items: list[Any],
    ) -> str:
        """Build the LLM prompt for ingest planning."""
        templates = config.get("templates", {})
        categories = [c["name"] for c in config.get("structure", {}).get("categories", [])]

        # Build evidence summary
        evidence_summary = ""
        if evidence_items:
            by_level = {}
            for item in evidence_items:
                level = getattr(item, "level", None)
                level_str = level.value if hasattr(level, "value") else str(level)
                by_level.setdefault(level_str, []).append(getattr(item, "claim", "")[:100])
            evidence_summary = "\n".join(
                f"- {level}: {len(claims)} claims" for level, claims in by_level.items()
            )

        return f"""You are a knowledge base manager. Analyze the research report below and decide what knowledge to extract into the wiki.

## Existing Wiki Index
{index or "(empty - first ingest)"}

## Existing Pages
{chr(10).join(f"- {p}" for p in existing_paths[:20]) if existing_paths else "(none)"}

## Available Categories
{', '.join(categories)}

## Evidence Summary
{evidence_summary or "(no evidence items)"}

## Research Report (truncated)
{report[:4000]}

## Instructions
Extract knowledge entities from the report. For each entity:
1. Decide if it deserves its own wiki page (would it be referenced by other pages?)
2. If a page already exists, suggest an update instead of creating a new one
3. Use the template format from the config

Return a JSON plan:
```json
{{
  "new_pages": [
    {{
      "name": "entity name",
      "category": "sensors|methods|analyses|comparisons|projects",
      "content": "full markdown page content",
      "evidence_level": "verified|evidence_backed|speculative",
      "confidence": 0.8
    }}
  ],
  "updates": [
    {{
      "path": "existing/page.md",
      "section": "section name to update",
      "new_content": "content to append"
    }}
  ]
}}
```

Only extract entities that would be referenced by other pages. Skip trivial mentions.
Write page content in the same language as the report."""

    # ── Parsing ──────────────────────────────────────────────────────

    def _parse_ingest_plan(self, llm_output: str, evidence_items: list[Any]) -> IngestPlan:
        """Parse LLM output into an IngestPlan."""
        plan = IngestPlan()

        # Extract JSON from LLM response
        json_match = re.search(r"```json\s*(.*?)\s*```", llm_output, re.DOTALL)
        if not json_match:
            json_match = re.search(r"\{.*\}", llm_output, re.DOTALL)
        if not json_match:
            logger.warning("[Ingest] No JSON found in LLM output")
            return plan

        try:
            data = json.loads(json_match.group(1) if json_match.lastindex else json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"[Ingest] JSON parse error: {e}")
            return plan

        for page in data.get("new_pages", []):
            plan.new_pages.append(PageAction(
                name=page.get("name", ""),
                category=page.get("category", "notes"),
                content=page.get("content", ""),
                action="create",
                evidence_level=page.get("evidence_level", "speculative"),
                confidence=page.get("confidence", 0.5),
            ))

        for update in data.get("updates", []):
            plan.updates.append(PageAction(
                name=update.get("path", ""),
                category="",
                action="update",
                update_path=update.get("path", ""),
                update_section=update.get("section", ""),
                content=update.get("new_content", ""),
            ))

        return plan

    # ── Page Formatting ──────────────────────────────────────────────

    def _format_sensor_page(self, name: str, snippet: str, evidence_items: list[Any]) -> str:
        """Format a sensor wiki page from extracted information."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"""# {name}

> Extracted from research report

## Basic Parameters
| Parameter | Value | Source | Confidence |
|-----------|-------|--------|------------|
| (to be filled) | | | |

## Key Findings
{snippet if snippet else "- (to be filled from research)"}

## Limitations
- (to be filled)

## Page Status
- status: draft
- first_seen: {today}
- confirmation_count: 1

## Related Pages

## Sources
- Research report {today}
"""

    def _format_method_page(self, name: str, snippet: str, evidence_items: list[Any]) -> str:
        """Format a method wiki page from extracted information."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"""# {name}

> Extracted from research report

## Formula / Algorithm
(to be filled)

## Input Requirements
(to be filled)

## Applicable Scenarios
{snippet if snippet else "- (to be filled from research)"}

## Limitations
- (to be filled)

## Page Status
- status: draft
- first_seen: {today}
- confirmation_count: 1

## Related Pages

## Sources
- Research report {today}
"""

    # ── Utilities ────────────────────────────────────────────────────

    def _extract_entity_snippet(self, report: str, keyword: str, max_len: int = 300) -> str:
        """Extract a relevant snippet about an entity from the report."""
        report_lower = report.lower()
        pos = report_lower.find(keyword)
        if pos == -1:
            return ""
        # Find sentence boundaries
        start = max(0, report.rfind(".", 0, pos))
        end = min(len(report), report.find(".", pos + len(keyword)))
        if end == -1:
            end = min(len(report), pos + max_len)
        snippet = report[start:end].strip()
        return snippet[:max_len] if snippet else ""
