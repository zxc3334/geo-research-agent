"""ResearcherAgent executor.

The pooled agent owns reusable components: policy binding, prompt builder,
tool registry, and loop config. Per-run mutable state is created inside
ToolCallingLoop on every run(...) call.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from .base_agent import BaseAgent
from .prompt_builder import (
    build_researcher_system_prompt,
    build_task_prompt,
    PromptContext,
)
from .tool_calling_loop import ToolCallingLoop, ToolLoopConfig
from .tool_registry import ToolRegistry
from ..orchestrator.schemas import AgentResult, AgentStatus, SubTask
from ..utils.tracing import trace_agent


__all__ = ["ResearcherAgent"]


class ResearcherAgent(BaseAgent):
    """Research / analysis / verification agent.

    Reusable state:
      - policy: one LLM provider binding for this pooled executor
      - prompt_builder: task/system prompt construction
      - tool_registry: tool lookup and schema export
      - loop_config: static loop limits

    Per-run state:
      - messages, trajectory, token counters, tool results
      - owned by a fresh ToolCallingLoop instance
    """

    def __init__(
        self,
        name: str,
        policy,
        tools: list | None = None,
        max_turns: int = 10,
        pool_type_key: str | None = None,
        loop_config: ToolLoopConfig | None = None,
        trace_recorder=None,
    ) -> None:
        super().__init__(name, policy, tools, pool_type_key=pool_type_key)
        self.max_turns = max_turns
        self.tool_registry = ToolRegistry(tools)
        self.loop_config = loop_config or ToolLoopConfig(max_turns=max_turns)
        self.trace_recorder = trace_recorder
        self.tool_map: dict[str, Any] = self.tool_registry.tool_map
        # Cache tool names for prompt generation
        self._tool_names: list[str] = list(self.tool_registry.tool_map.keys())
        # Domain is set by the caller (runner/agent_pool) via context
        self._domain: str = ""

    @trace_agent(name="researcher.run", tags=["agent", "researcher"])
    async def run(self, task: SubTask, context: dict) -> AgentResult:
        """Execute one SubTask with a fresh loop runtime."""
        domain = context.get("domain", self._domain)
        system_prompt = build_researcher_system_prompt(self._tool_names, domain)
        task_prompt = build_task_prompt(task, context, self._tool_names, domain)

        if self._is_non_searchable(task, context):
            return await self._run_direct_analysis(task, task_prompt)

        # Pre-fetch external evidence
        task_prompt = await self._inject_external_evidence(task, task_prompt)

        loop = ToolCallingLoop(
            policy=self.policy,
            tool_registry=self.tool_registry,
            config=self.loop_config,
            trace_recorder=self.trace_recorder,
        )
        result = await loop.run(
            task=task,
            system_prompt=system_prompt,
            user_prompt=task_prompt,
        )
        return self.finalize_result(result)

    @staticmethod
    def _is_non_searchable(task: SubTask, context: dict) -> bool:
        """Heuristic: tasks that can't be answered by tool search."""
        desc = (task.description or "").lower()
        # Synthesis tasks are handled by SummarizerAgent, not here
        if task.task_type.value == "synthesis":
            return True
        return False

    async def _inject_external_evidence(self, task: SubTask, task_prompt: str) -> str:
        """Pre-fetch web_search and paper_search results and inject into context."""
        import logging
        logger = logging.getLogger("src.agents.researcher")

        search_query = task.description[:200]
        evidence_parts: list[str] = []
        print(f"[INJECT] >>> START task={task.task_id} query={search_query[:60]}...", flush=True)
        logger.info(f"[inject] >>> START task={task.task_id} query={search_query[:60]}...")

        # 1. Web search
        web_tool = self.tool_registry.tool_map.get("web_search")
        logger.info(f"[inject] task={task.task_id} web_search tool={'found' if web_tool else 'MISSING'} type={type(web_tool).__name__ if web_tool else 'N/A'}")
        if web_tool and hasattr(web_tool, "execute"):
            try:
                web_result = await asyncio.wait_for(
                    web_tool.execute(search_query, top_n=3),
                    timeout=15,
                )
                results = web_result.get("results", []) if isinstance(web_result, dict) else []
                logger.info(f"[inject] task={task.task_id} web_search returned {len(results)} results")
                if results:
                    lines = ["### Web Search Results (auto-fetched):"]
                    for i, r in enumerate(results[:3], 1):
                        title = r.get("title", "")
                        url = r.get("url", "")
                        snippet = r.get("snippet", "")[:200]
                        score = r.get("_quality_score", "")
                        tier = r.get("_source_tier", "")
                        score_str = f" [score={score}, tier={tier}]" if score else ""
                        lines.append(f"{i}. **{title}**{score_str}\n   URL: {url}\n   {snippet}")
                    evidence_parts.append("\n".join(lines))
            except Exception as e:
                logger.warning(f"[inject] task={task.task_id} web_search FAILED: {e}")

        # 2. Paper search
        paper_tool = self.tool_registry.tool_map.get("paper_search")
        logger.info(f"[inject] task={task.task_id} paper_search tool={'found' if paper_tool else 'MISSING'} type={type(paper_tool).__name__ if paper_tool else 'N/A'}")
        if paper_tool and hasattr(paper_tool, "execute"):
            try:
                paper_result = await asyncio.wait_for(
                    paper_tool.execute(search_query, max_results=3),
                    timeout=15,
                )
                papers = paper_result.get("papers", []) if isinstance(paper_result, dict) else []
                logger.info(f"[inject] task={task.task_id} paper_search returned {len(papers)} papers")
                if papers:
                    lines = ["### Academic Papers (auto-fetched):"]
                    for i, p in enumerate(papers[:3], 1):
                        title = p.get("title", "")
                        url = p.get("url", "") or p.get("pdf_url", "")
                        summary = str(p.get("summary", ""))[:200]
                        citations = p.get("citation_count", "")
                        cit_str = f" [citations={citations}]" if citations else ""
                        lines.append(f"{i}. **{title}**{cit_str}\n   URL: {url}\n   {summary}")
                    evidence_parts.append("\n".join(lines))
            except Exception as e:
                logger.warning(f"[inject] task={task.task_id} paper_search FAILED: {e}")

        if evidence_parts:
            logger.info(f"[inject] task={task.task_id} injecting {len(evidence_parts)} evidence parts into prompt")
            task_prompt += (
                "\n\n## PRE-FETCHED EXTERNAL EVIDENCE (auto-generated, use as reference):\n"
                + "\n\n".join(evidence_parts)
                + "\n\nYou should incorporate this evidence into your analysis. "
                "You may still call tools to verify or补充 this information."
            )
        else:
            logger.warning(f"[inject] task={task.task_id} NO evidence collected — prompt unchanged")
            print(f"[INJECT] <<< END task={task.task_id} NO EVIDENCE", flush=True)

        return task_prompt

    async def _run_direct_analysis(self, task: SubTask, task_prompt: str) -> AgentResult:
        """Handle private/subjective tasks without forcing a web search."""
        direct_system = (
            "You are a thoughtful analyst. "
            "The user has asked a question that cannot be answered by web search "
            "(e.g., analyzing a specific private individual, personal advice, or subjective judgment). "
            "Your job is to provide a reasoned analysis based ONLY on the information already provided in the context. "
            "Do NOT make up facts. Clearly state what is known, what can be reasonably inferred, and what remains unknown. "
            "End with a confidence score (0-1)."
        )
        messages = [
            {"role": "system", "content": direct_system},
            {"role": "user", "content": task_prompt},
        ]
        try:
            response = await asyncio.to_thread(self.policy, messages)
            content = response.get("content", "") or ""
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.SUCCESS,
                output=content,
                trajectory=[{"role": "assistant", "content": content}],
                token_usage=len(content) // 3,
                confidence=self._extract_confidence(content),
            ))
        except Exception as e:
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.FAILED,
                output=f"Direct analysis failed: {e}",
                trajectory=[{"error": str(e)}],
                token_usage=0,
                confidence=0.0,
            ))

    def _extract_confidence(self, content: str) -> float:
        """Extract a confidence score from direct-analysis output."""
        patterns = [
            r"[Cc]onfidence[:\s]+(0\.\d+|1\.0|1)",
            r"置信度[:\s]+(0\.\d+|1\.0|1)",
        ]
        for pat in patterns:
            m = re.search(pat, content)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
        return 0.6
