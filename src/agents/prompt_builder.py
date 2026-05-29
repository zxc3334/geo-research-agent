"""PromptBuilder — pipe-based prompt construction with cache-friendly layout.

Design principles:
  1. Static sections first (identity, rules, tool guide) → better LLM cache hit
  2. Dynamic sections after (task context, memory, search hints) → per-request
  3. Each pipe is a factory function returning a PipeFn
  4. PipeFn returns str (include) or None (skip)
  5. Chainable builder with debug support

Usage:
    prompt = (PromptBuilder()
        .pipe("identity", identity_section())
        .pipe("rules", system_rules())
        .pipe("tools", tool_guide(tool_names))
        .pipe("task_context", task_context(task, context))
        .build())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..orchestrator.schemas import SubTask


# ── Types ────────────────────────────────────────────────────────────

class PipeFn(Protocol):
    """A pipe function: receives context, returns section text or None to skip."""
    def __call__(self, ctx: "PromptContext") -> str | None: ...


@dataclass
class PromptContext:
    """Context passed to every pipe function."""
    task: SubTask | None = None
    context: dict[str, Any] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    domain: str = ""
    session_message_count: int = 0


# ── Builder ──────────────────────────────────────────────────────────

class PromptBuilder:
    """Chainable prompt builder with pipe-based section composition."""

    def __init__(self):
        self._pipes: list[tuple[str, PipeFn]] = []

    def pipe(self, name: str, fn: PipeFn) -> "PromptBuilder":
        """Add a pipe section. Chain with .pipe(...).pipe(...).build()."""
        self._pipes.append((name, fn))
        return self

    def build(self, ctx: PromptContext | None = None) -> str:
        """Build the final prompt by running all pipes."""
        ctx = ctx or PromptContext()
        sections: list[str] = []
        for name, fn in self._pipes:
            result = fn(ctx)
            if result is not None:
                sections.append(result)
        return "\n\n".join(sections)

    def debug(self, ctx: PromptContext | None = None) -> list[dict[str, Any]]:
        """Run all pipes and return debug info (for logging/trace)."""
        ctx = ctx or PromptContext()
        info = []
        for name, fn in self._pipes:
            result = fn(ctx)
            info.append({
                "name": name,
                "status": "ON" if result is not None else "OFF",
                "chars": len(result) if result else 0,
            })
        return info


# ── Pipe Factory Functions ───────────────────────────────────────────

def identity_section() -> PipeFn:
    """STATIC — Agent identity and core role."""
    text = (
        "You are a meticulous GIS/remote-sensing research assistant. "
        "Your job is to gather and analyze information using the RIGHT tool for each task, "
        "then produce evidence-backed conclusions with confidence scores."
    )
    return lambda ctx: text


def system_rules() -> PipeFn:
    """STATIC — Universal behavioral rules."""
    text = (
        "IMPORTANT RULES:\n"
        "1. You MUST use a tool to find factual information. Do NOT answer from your own knowledge.\n"
        "2. Choose the RIGHT tool based on the task type. You can use MULTIPLE tools in sequence.\n"
        "3. If search results are too short, try a more specific query.\n"
        "4. You may call tools AT MOST 3 times total. After that you MUST summarize.\n"
        "5. Only after gathering information, provide a concise summary with a confidence score (0-1).\n"
        "6. NEVER greet the user or ask what they want — just execute immediately.\n"
        "7. Write your summary in the same language as the task description."
    )
    return lambda ctx: text


def tool_guide() -> PipeFn:
    """STATIC (per-session) — List available tools. Only shows tools that are registered."""
    _TOOL_DOCS: dict[str, str] = {
        "web_search": "General web search. Use for broad queries when no specialized tool fits.",
        "official_source_search": "Search official GIS/RS documentation (ESA, USGS, NASA, Copernicus). USE for sensor specs, bands, algorithms, data access.",
        "official_doc_fetcher": "Fetch and read an official documentation URL. USE after official_source_search to get page-grounded evidence.",
        "paper_search": "Academic paper search (OpenAlex). USE for peer-reviewed methods, formulas, citation counts.",
        "calculator": "Quick math evaluation. USE for simple calculations.",
        "notepad": "Write/read intermediate notes. USE to record key findings during multi-step research.",
        "file_reader": "Read local files. USE only when the task references a file path.",
        "dataset_registry": "Curated GIS/RS dataset facts (sensors, bands, resolution, limitations).",
        "method_registry": "Curated GIS/RS method facts (formulas, inputs, valid use cases).",
        "geo_plan_validator": "Deterministic GIS/RS compatibility validator. USE to check dataset-method workflow validity.",
    }

    def _build(ctx: PromptContext) -> str | None:
        if not ctx.tool_names:
            return None
        lines = ["AVAILABLE TOOLS:"]
        for name in ctx.tool_names:
            desc = _TOOL_DOCS.get(name)
            if desc:
                lines.append(f"- {name}: {desc}")
        if len(lines) == 1:
            return None
        return "\n".join(lines)

    return _build


def tool_selection_strategy() -> PipeFn:
    """STATIC — How to choose tools based on task type."""
    text = (
        "TOOL SELECTION STRATEGY:\n"
        "- For GIS/RS factual validation: START with official_source_search or a registry tool "
        "(dataset_registry, method_registry, geo_plan_validator). "
        "If you get an official URL, use official_doc_fetcher to read it.\n"
        "- For academic method evidence: use paper_search.\n"
        "- For general/broad queries: START with web_search.\n"
        "- For multi-step research: use notepad to record intermediate findings."
    )
    return lambda ctx: text


def task_context() -> PipeFn:
    """DYNAMIC — Task description, type, expected output, search hints."""
    def _build(ctx: PromptContext) -> str | None:
        task = ctx.task
        if task is None:
            return None
        lines = [
            f"## Task: {task.description}",
            f"Type: {task.task_type.value}",
            f"Expected output: {task.expected_type}",
        ]
        if task.search_hints:
            lines.append(f"Search hints (use as primary keywords): {', '.join(task.search_hints)}")
        return "\n".join(lines)
    return _build


def context_injection() -> PipeFn:
    """DYNAMIC — Inject memory context, prior results, etc."""
    def _build(ctx: PromptContext) -> str | None:
        if not ctx.context:
            return None
        parts = []
        for key, value in ctx.context.items():
            if value:
                parts.append(f"[{key}] {value}")
        if not parts:
            return None
        return "## Context:\n" + "\n".join(parts)
    return _build


def domain_hints() -> PipeFn:
    """DYNAMIC — Domain-specific guidance (only for geo_rs domain)."""
    _GEO_HINTS = (
        "## GIS/RS Domain Hints:\n"
        "- Always verify sensor capabilities against official docs (e.g., Sentinel-2 has NO thermal band).\n"
        "- Check spatial resolution compatibility before combining datasets.\n"
        "- Distinguish LST (land surface temperature) from air temperature.\n"
        "- NDBI can confuse bare soil with built-up areas — validate with NDVI threshold."
    )

    def _build(ctx: PromptContext) -> str | None:
        if ctx.domain == "geo_remote_sensing":
            return _GEO_HINTS
        return None
    return _build


def output_format() -> PipeFn:
    """STATIC — Expected output format."""
    text = (
        "OUTPUT FORMAT:\n"
        "End your response with:\n"
        "1. A concise summary of findings\n"
        "2. A confidence score: Confidence: X.XX (0-1)"
    )
    return lambda ctx: text


# ── Pre-built prompt builders ────────────────────────────────────────

def build_researcher_system_prompt(tool_names: list[str], domain: str = "") -> str:
    """Build the system prompt for ResearcherAgent.

    Layout: static first → dynamic after → better cache hit rate.
    """
    return (PromptBuilder()
        # ── Static (cacheable) ──
        .pipe("identity", identity_section())
        .pipe("rules", system_rules())
        .pipe("tool_guide", tool_guide())
        .pipe("tool_strategy", tool_selection_strategy())
        .pipe("output_format", output_format())
        # ── Dynamic (per-domain) ──
        .pipe("domain_hints", domain_hints())
        .build(PromptContext(tool_names=tool_names, domain=domain))
    )


def build_task_prompt(
    task: SubTask,
    context: dict[str, Any],
    tool_names: list[str],
    domain: str = "",
) -> str:
    """Build the user prompt for one SubTask."""
    return (PromptBuilder()
        # ── Static ──
        .pipe("tool_guide", tool_guide())
        # ── Dynamic ──
        .pipe("task_context", task_context())
        .pipe("context_injection", context_injection())
        .pipe("domain_hints", domain_hints())
        .build(PromptContext(
            task=task,
            context=context,
            tool_names=tool_names,
            domain=domain,
        ))
    )
