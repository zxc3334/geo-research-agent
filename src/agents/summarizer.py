"""
合成 Agent (SummarizerAgent)

将多个 SubTask 的执行结果合成为结构化的研究报告。
区别于 ResearcherAgent 的多轮 tool-calling，Summarizer 是单轮长上下文生成任务：
  - 把所有子结果按置信度排序后拼接为上下文
  - 调用 LLM 一次性生成 Markdown 格式报告
  - 提取引用来源，计算整体置信度
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .base_agent import BaseAgent
from ..orchestrator.schemas import SubTask, AgentResult, AgentStatus, ResearchReport
from ..utils.tracing import trace_agent


__all__ = ["SummarizerAgent"]


class SummarizerAgent(BaseAgent):
    """合成 Agent：将子任务结果合成为最终研究报告。

    Attributes:
        max_output_tokens: 报告生成的最大 token 数（通过 policy.max_tokens 控制）。
    """

    default_context_budget_tokens = 24000
    default_compact_threshold_ratio = 0.70
    default_compact_result_chars = 1800
    default_compact_evidence_items_per_result = 6
    default_chars_per_token = 3.5

    def __init__(
        self,
        name: str,
        policy,
        tools: list | None = None,
        pool_type_key: str | None = None,
        compact_config: dict | None = None,
        trace_recorder=None,
    ) -> None:
        super().__init__(name, policy, tools, pool_type_key=pool_type_key)
        compact_config = compact_config or {}
        self.trace_recorder = trace_recorder
        self.context_budget_tokens = compact_config.get(
            "context_budget_tokens", self.default_context_budget_tokens
        )
        self.compact_threshold_ratio = compact_config.get(
            "compact_threshold_ratio", self.default_compact_threshold_ratio
        )
        self.compact_result_chars = compact_config.get(
            "compact_result_chars", self.default_compact_result_chars
        )
        self.compact_evidence_items_per_result = compact_config.get(
            "compact_evidence_items_per_result", self.default_compact_evidence_items_per_result
        )
        self.chars_per_token = compact_config.get(
            "chars_per_token", self.default_chars_per_token
        )

    @trace_agent(name="summarizer.run", tags=["agent", "summarizer"])
    async def run(self, task: SubTask, context: dict) -> AgentResult:
        """执行合成任务。

        Args:
            task: 通常是一个特殊的 "synthesize" 类型任务。
            context: 全局上下文，必须包含 "results" 和 "query" 键。
                results: list[AgentResult]
                query: str 原始研究问题

        Returns:
            AgentResult，output 字段为 ResearchReport 实例。
        """
        query = context.get("query", "")
        results: list[AgentResult] = context.get("results", [])
        domain = context.get("domain", "general")
        evidence_summary = context.get("evidence_summary", {})

        if not results:
            report = ResearchReport(
                query=query,
                content="No sub-task results available to synthesize.",
                confidence=0.0,
            )
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.FAILED,
                output=report,
                trajectory=[],
                token_usage=0,
                confidence=0.0,
            ))

        # 构建 synthesis prompt
        prompt = self._build_synthesis_prompt(
            query,
            results,
            domain=domain,
            evidence_summary=evidence_summary,
        )
        messages = [
            {"role": "system", "content": self._system_prompt(domain)},
            {"role": "user", "content": prompt},
        ]

        try:
            # 合成任务不需要工具调用，临时禁用 tools 避免模型进入 tool-calling 模式
            old_tools = getattr(self.policy, "tools", None)
            self.policy.tools = None
            response = await asyncio.to_thread(self.policy, messages)
        except RuntimeError as e:
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.FAILED,
                output=str(e),
                trajectory=[{"error": str(e)}],
                token_usage=0,
                confidence=0.0,
            ))
        except Exception as e:
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.FAILED,
                output=f"Synthesis failed: {type(e).__name__}: {e}",
                trajectory=[{"error": str(e)}],
                token_usage=0,
                confidence=0.0,
            ))
        finally:
            self.policy.tools = old_tools

        content = response.get("content", "") or ""
        usage = response.get("usage", {}) or {}
        if self.trace_recorder:
            self.trace_recorder.record(
                "llm_call",
                task_id=task.task_id,
                role="summarizer",
                usage=usage,
                output_chars=len(content),
            )
        if self._is_policy_error_content(content):
            return self.finalize_result(AgentResult(
                task_id=task.task_id,
                status=AgentStatus.FAILED,
                output=f"Synthesis failed: {content}",
                trajectory=[{"role": "assistant", "content": content}],
                token_usage=0,
                confidence=0.0,
            ))

        token_usage = usage.get("total_tokens", 0) or len(content) // 3  # fallback estimate

        # 解析报告内容，提取来源和置信度
        report = self._parse_report(query, content, results)

        return self.finalize_result(AgentResult(
            task_id=task.task_id,
            status=AgentStatus.SUCCESS,
            output=report,
            trajectory=[{"role": "assistant", "content": content, "usage": usage}],
            token_usage=token_usage,
            confidence=report.confidence,
        ))

    def _system_prompt(self, domain: str = "general") -> str:
        if domain == "geo_remote_sensing":
            return (
                "You are an expert GIS and remote-sensing research synthesizer. "
                "Your task is to integrate sub-task findings into a concrete, evidence-aware GIS/remote-sensing research report. "
                "Use Markdown formatting and cite sources explicitly when they are available. "
                "Do not invent datasets, bands, algorithms, platform capabilities, spatial resolution, temporal resolution, or validation data. "
                "When evidence is insufficient, label the item as Speculative or Needs Verification. "
                "The report body MUST be at least 2500 Chinese characters (or 1600 English words) long. "
                "DO NOT describe what you will do; directly output the synthesized report. "
                "At the end, provide Overall Confidence: X.XX and a short source/evidence summary."
            )

        return (
            "You are an expert research synthesizer. "
            "Your task is to integrate multiple research findings into a coherent, well-structured report. "
            "Use Markdown formatting. Cite sources explicitly. "
            "The report body MUST be at least 3000 Chinese characters (or 2000 English words) long. "
            "Write in depth: include background, key findings, detailed analysis, comparisons, and implications. "
            "DO NOT describe what you will do — directly output the synthesized report. "
            "At the end, provide an overall confidence score (0-1) and a summary of key sources."
        )

    def _build_synthesis_prompt(
        self,
        query: str,
        results: list[AgentResult],
        domain: str = "general",
        evidence_summary: dict | None = None,
    ) -> str:
        """构建合成 prompt，按置信度降序排列结果。"""
        prompt = self._build_synthesis_prompt_with_mode(
            query=query,
            results=results,
            domain=domain,
            evidence_summary=evidence_summary,
            compact=False,
        )
        threshold_chars = int(
            self.context_budget_tokens
            * self.chars_per_token
            * self.compact_threshold_ratio
        )
        if len(prompt) <= threshold_chars:
            return prompt

        compact_prompt = self._build_synthesis_prompt_with_mode(
            query=query,
            results=results,
            domain=domain,
            evidence_summary=evidence_summary,
            compact=True,
        )
        if compact_prompt == prompt or "[compact]" not in compact_prompt:
            return prompt
        print(f"[compact] summarizer_prompt chars={len(prompt)}->{len(compact_prompt)} threshold={threshold_chars}")
        if self.trace_recorder:
            self.trace_recorder.record(
                "compact",
                scope="synthesis_prompt",
                before_chars=len(prompt),
                after_chars=len(compact_prompt),
                threshold_chars=threshold_chars,
                strategy="head_tail_70_30",
            )
        return compact_prompt

    def _build_synthesis_prompt_with_mode(
        self,
        query: str,
        results: list[AgentResult],
        domain: str = "general",
        evidence_summary: dict | None = None,
        compact: bool = False,
    ) -> str:
        """Build synthesis prompt; compact mode preserves errors and head/tail evidence."""
        sorted_results = sorted(results, key=lambda r: r.confidence, reverse=True)

        parts = [
            f"# Research Question\n{query}\n",
            f"# Sub-task Results ({len(results)} total)\n",
        ]
        if evidence_summary:
            parts.append(self._format_evidence_summary(evidence_summary))
        for i, r in enumerate(sorted_results, 1):
            status_icon = "✓" if r.status == AgentStatus.SUCCESS else "✗"
            evidence_block = self._format_result_evidence(r, compact=compact)
            output_text = self._format_result_output(r, compact=compact)
            parts.append(
                f"## Result {i} [{status_icon}] (confidence: {r.confidence:.2f})\n"
                f"Task: {r.task_id}\n"
                f"{evidence_block}"
                f"Output:\n{output_text}\n"
            )

        if domain == "geo_remote_sensing":
            parts.append(
                "\n# Instructions\n"
                "1. Directly write the final GIS/remote-sensing research report. Do NOT say 'I will synthesize'.\n"
                "2. The report MUST be comprehensive and detailed (at least 2500 Chinese characters or 1600 English words).\n"
                "3. Use this exact top-level structure:\n"
                "   - 研究问题与核心结论\n"
                "   - 数据候选与适用性\n"
                "   - 方法链路设计\n"
                "   - GIS/遥感验证清单\n"
                "   - 风险、限制与替代方案\n"
                "   - 可执行 MVP 工作流\n"
                "   - 证据与引用\n"
                "   - 可信度分级\n"
                "4. In 数据候选与适用性, explicitly discuss AOI, time range, sensors/datasets, required bands or variables, spatial resolution, temporal consistency, and availability risks when mentioned by sub-task results.\n"
                "5. In 方法链路设计, distinguish candidate methods from verified methods. Do not claim a method is valid unless the sub-task results support it.\n"
                "6. In GIS/遥感验证清单, include checks for CRS, cloud mask, season/month consistency, scale mismatch, missing bands, validation data, and known sensor limitations when relevant.\n"
                "7. In 可信度分级, classify claims as Verified, Evidence-backed, Speculative, or Rejected/Not supported.\n"
                "8. Resolve contradictions between sub-task results and explain which result is more reliable.\n"
                "9. Explicitly list all cited sources. If the run used mock or insufficient sources, say so clearly.\n"
                "10. End with: Overall Confidence: X.XX"
            )
        else:
            parts.append(
                "\n# Instructions\n"
                "1. Directly write the synthesized report based on the findings above. Do NOT say 'I will synthesize'.\n"
                "2. The report MUST be comprehensive and detailed (at least 3000 Chinese characters or 2000 English words).\n"
                "3. Structure: Executive Summary → Background → Key Findings (with details) → Analysis → Comparisons → Implications → Conclusion.\n"
                "4. Resolve any contradictions between sources.\n"
                "5. Explicitly list all sources cited.\n"
                "6. End with: Overall Confidence: X.XX"
            )
        return "\n".join(parts)

    def _format_evidence_summary(self, evidence_summary: dict) -> str:
        """Render evidence summary for the LLM prompt."""
        counts = evidence_summary.get("counts", {})
        if not counts:
            return ""
        lines = ["# Evidence Summary"]
        for key in ("verified", "evidence_backed", "speculative", "rejected"):
            lines.append(f"- {key}: {counts.get(key, 0)}")
        return "\n".join(lines) + "\n"

    def _format_result_evidence(self, result: AgentResult, compact: bool = False) -> str:
        """Render per-result evidence items for synthesis."""
        if not result.evidence_items:
            return ""
        lines = ["Evidence:"]
        items = result.evidence_items
        if compact and result.status == AgentStatus.SUCCESS:
            items = result.evidence_items[:self.compact_evidence_items_per_result]
        for item in items:
            source = f" | source: {item.source}" if item.source else ""
            tier = f" | tier: {item.source_tier.value}" if hasattr(item, "source_tier") else ""
            lines.append(
                f"- level={item.level.value}, confidence={item.confidence:.2f}{tier}{source}; "
                f"rationale={item.rationale}"
            )
        remaining = len(result.evidence_items) - len(items)
        if compact and remaining > 0:
            lines.append(f"- [compact] {remaining} more evidence items omitted from synthesis prompt.")
        return "\n".join(lines) + "\n"

    def _format_result_output(self, result: AgentResult, compact: bool = False) -> str:
        text = str(result.output)
        if not compact or result.status != AgentStatus.SUCCESS or self._is_policy_error_content(text):
            return text
        if len(text) <= self.compact_result_chars:
            return text
        return self._head_tail_compact(text, self.compact_result_chars)

    def _head_tail_compact(self, text: str, max_chars: int, head_ratio: float = 0.70) -> str:
        head_chars = max(1, int(max_chars * head_ratio))
        tail_chars = max(1, max_chars - head_chars)
        omitted = max(0, len(text) - head_chars - tail_chars)
        return (
            f"{text[:head_chars].rstrip()}\n"
            f"[compact] omitted {omitted} chars from middle; preserved head/tail 70/30.\n"
            f"{text[-tail_chars:].lstrip()}"
        )

    def _is_policy_error_content(self, content: str) -> bool:
        """Detect provider errors wrapped as assistant content by policy layer."""
        text = (content or "").strip().lower()
        if not text:
            return True
        error_prefixes = ("error:", "policy error:")
        error_markers = (
            "connection error",
            "request timed out",
            "timeout",
            "maximum context length",
            "context length",
        )
        return text.startswith(error_prefixes) and any(marker in text for marker in error_markers)

    def _parse_report(self, query: str, content: str, results: list[AgentResult]) -> ResearchReport:
        """从 LLM 输出中解析 ResearchReport，并基于子任务成功率校准置信度。"""
        # 1. 从文本中提取 LLM 自评置信度
        llm_confidence = 0.5
        m = re.search(r"[Oo]verall\s+[Cc]onfidence[:\s]+(0\.\d+|1\.0|1)", content)
        if m:
            try:
                llm_confidence = float(m.group(1))
            except ValueError:
                pass

        # 2. 基于子任务成功率计算客观置信度
        total = len(results)
        success = sum(1 for r in results if r.status == AgentStatus.SUCCESS)
        success_rate = success / max(total, 1)

        # 3. 综合置信度 = LLM 自评 × 成功率开根（降低成功率的影响权重）
        confidence = llm_confidence * (success_rate ** 0.5)
        confidence = round(max(0.0, min(1.0, confidence)), 2)

        # 收集来源（从各个子结果的轨迹中提取）
        sources: list[dict] = []
        for r in results:
            if r.status != AgentStatus.SUCCESS:
                continue
            # 简单启发式：从 trajectory 的 tool 结果中提取 url
            for step in r.trajectory:
                if step.get("role") == "tool" and isinstance(step.get("result"), dict):
                    res = step["result"]
                    if "results" in res and isinstance(res["results"], list):
                        for item in res["results"]:
                            if isinstance(item, dict) and "url" in item:
                                sources.append({
                                    "url": item["url"],
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "task_id": r.task_id,
                                })
                    elif "papers" in res and isinstance(res["papers"], list):
                        for paper in res["papers"]:
                            if isinstance(paper, dict) and "pdf_url" in paper:
                                sources.append({
                                    "url": paper["pdf_url"],
                                    "title": paper.get("title", ""),
                                    "snippet": paper.get("summary", "")[:200],
                                    "task_id": r.task_id,
                                })

        # 去重
        seen = set()
        unique_sources = []
        for s in sources:
            key = s["url"]
            if key not in seen:
                seen.add(key)
                unique_sources.append(s)

        # 统计实际工具调用次数（遍历所有子任务的 trajectory）
        num_searches = sum(
            len([t for t in r.trajectory if t.get("role") == "tool"])
            for r in results
        )

        evidence_summary = self._summarize_result_evidence(results)
        return ResearchReport(
            query=query,
            content=content,
            sources=unique_sources,
            confidence=confidence,
            num_searches=num_searches,
            evidence_summary=evidence_summary,
            tool_trace=self._build_tool_trace(results),
        )

    def _summarize_result_evidence(self, results: list[AgentResult]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        claims_by_level: dict[str, list[dict]] = {}
        for result in results:
            for item in result.evidence_items:
                level = item.level.value
                counts[level] = counts.get(level, 0) + 1
                claims_by_level.setdefault(level, []).append(item.to_dict())
        return {"counts": counts, "claims_by_level": claims_by_level}

    def _build_tool_trace(self, results: list[AgentResult]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for result in results:
            for step in result.trajectory:
                if step.get("role") != "tool":
                    continue
                payload = step.get("result")
                urls = []
                if isinstance(payload, dict):
                    for item in payload.get("results", []) or []:
                        if isinstance(item, dict) and item.get("url"):
                            urls.append(item["url"])
                trace.append({
                    "task_id": result.task_id,
                    "tool": step.get("name", ""),
                    "turn": step.get("turn"),
                    "url_count": len(urls),
                    "urls": urls[:5],
                })
        return trace
