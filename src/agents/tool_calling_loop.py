"""Per-run tool-calling loop runtime.

This object is intentionally created fresh for every Agent.run(...) call. It
owns mutable runtime state such as messages, trajectory, and token counters, so
AgentPool can reuse Agent executors without leaking context across tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from ..orchestrator.schemas import AgentResult, AgentStatus, SubTask
from .tool_registry import ToolRegistry


@dataclass(frozen=True)
class ToolLoopConfig:
    """Configuration for one tool-calling loop execution."""

    max_turns: int = 10
    max_tool_calls_before_summary: int = 2
    context_budget_tokens: int = 12000
    compact_threshold_ratio: float = 0.70
    compact_tool_result_chars: int = 1500
    chars_per_token: float = 3.5


class ToolCallingLoop:
    """Run a single task through LLM tool calls and tool execution."""

    def __init__(
        self,
        policy,
        tool_registry: ToolRegistry,
        config: ToolLoopConfig | None = None,
        trace_recorder=None,
    ) -> None:
        self.policy = policy
        self.tool_registry = tool_registry
        self.config = config or ToolLoopConfig()
        self.trace_recorder = trace_recorder
        self.messages: list[dict] = []
        self.trajectory: list[dict] = []
        self.total_tokens: int = 0

    async def run(self, task: SubTask, system_prompt: str, user_prompt: str) -> AgentResult:
        """Execute the tool-calling loop for one task."""
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if hasattr(self.policy, "set_tools") and self.tool_registry.tools:
            self.policy.set_tools(self.tool_registry.schemas())

        fallback_tool = self._fallback_tool(task)

        for turn in range(self.config.max_turns):
            self._maybe_force_tool_use(turn, fallback_tool)

            try:
                response = await asyncio.to_thread(self.policy, self.messages)
            except RuntimeError as e:
                self.trajectory.append({"turn": turn, "error": str(e)})
                return AgentResult(
                    task_id=task.task_id,
                    status=AgentStatus.FAILED,
                    output=str(e),
                    trajectory=self.trajectory,
                    token_usage=self.total_tokens,
                    confidence=0.0,
                )
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                self.trajectory.append({"turn": turn, "error": error_msg})
                return AgentResult(
                    task_id=task.task_id,
                    status=AgentStatus.FAILED,
                    output=f"Policy call failed: {error_msg}",
                    trajectory=self.trajectory,
                    token_usage=self.total_tokens,
                    confidence=0.0,
                )

            content = response.get("content", "") or ""
            tool_calls = response.get("tool_calls", []) or []
            usage = response.get("usage", {}) or {}

            self.trajectory.append({
                "turn": turn,
                "role": "assistant",
                "content": content,
                "tool_calls": [dict(tc) for tc in tool_calls],
                "usage": usage,
            })
            if self.trace_recorder:
                self.trace_recorder.record(
                    "llm_call",
                    task_id=task.task_id,
                    turn=turn,
                    role="researcher",
                    usage=usage,
                    tool_call_count=len(tool_calls),
                    output_chars=len(content),
                )

            self.total_tokens += usage.get("total_tokens", 0) or len(json.dumps(self.messages, ensure_ascii=False)) // 3

            if not tool_calls:
                if self._is_tool_failure_explanation(content):
                    return AgentResult(
                        task_id=task.task_id,
                        status=AgentStatus.FAILED,
                        output=content,
                        trajectory=self.trajectory,
                        token_usage=self.total_tokens,
                        confidence=0.0,
                    )
                return AgentResult(
                    task_id=task.task_id,
                    status=AgentStatus.SUCCESS,
                    output=content,
                    trajectory=self.trajectory,
                    token_usage=self.total_tokens,
                    confidence=self._extract_confidence(content),
                )

            tool_results = await self._execute_tool_calls(task, turn, tool_calls)
            if isinstance(tool_results, AgentResult):
                return tool_results

            force_summary = self._should_force_summary(tool_results)
            self._append_assistant_and_tool_messages(response, content, tool_calls, tool_results, force_summary)
            self._compact_old_messages()

        return AgentResult(
            task_id=task.task_id,
            status=AgentStatus.TIMEOUT,
            output="Reached max_turns without final answer.",
            trajectory=self.trajectory,
            token_usage=self.total_tokens,
            confidence=0.0,
        )

    async def _execute_tool_calls(self, task: SubTask, turn: int, tool_calls: list) -> list[dict] | AgentResult:
        tool_results = []
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            result = await self.tool_registry.execute(tool_name, args)
            if self.trace_recorder:
                self.trace_recorder.record(
                    "tool_call",
                    task_id=task.task_id,
                    turn=turn,
                    tool=tool_name,
                    args=args,
                )

            if isinstance(result, dict) and result.get("error"):
                error_msg = result["error"]
                if self.trace_recorder:
                    self.trace_recorder.record(
                        "tool_result",
                        task_id=task.task_id,
                        turn=turn,
                        tool=tool_name,
                        status="error",
                        error=error_msg,
                    )
                self.trajectory.append({
                    "turn": turn,
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                    "error": error_msg,
                })
                return AgentResult(
                    task_id=task.task_id,
                    status=AgentStatus.FAILED,
                    output=f"Tool '{tool_name}' failed: {error_msg}",
                    trajectory=self.trajectory,
                    token_usage=self.total_tokens,
                    confidence=0.0,
                )

            tool_result = {
                "tool_call_id": tc.get("id", ""),
                "name": tool_name,
                "result": result,
            }
            self._log_tool_result(task, turn, tool_name, args, result)
            self._trace_tool_result(task, turn, tool_name, result)
            tool_results.append(tool_result)
            self.trajectory.append({
                "turn": turn,
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": tool_name,
                "result": result,
            })

        return tool_results

    def _trace_tool_result(self, task: SubTask, turn: int, tool_name: str, result) -> None:
        if not self.trace_recorder:
            return
        if not isinstance(result, dict):
            self.trace_recorder.record(
                "tool_result",
                task_id=task.task_id,
                turn=turn,
                tool=tool_name,
                status="success",
                result_type=type(result).__name__,
            )
            return

        urls = []
        for item in result.get("results", []) or []:
            if isinstance(item, dict) and item.get("url"):
                urls.append(item["url"])
        self.trace_recorder.record(
            "tool_result",
            task_id=task.task_id,
            turn=turn,
            tool=tool_name,
            status="success",
            source=result.get("source", ""),
            total=result.get("total"),
            url_count=len(urls),
            urls=urls[:10],
        )

    def _log_tool_result(self, task: SubTask, turn: int, tool_name: str, args: dict, result) -> None:
        """Log compact tool observability for logs and demo debugging."""
        if not isinstance(result, dict):
            logger.info(f"[ToolCall] task={task.task_id} turn={turn} tool={tool_name} result_type={type(result).__name__}")
            return

        urls = []
        for item in result.get("results", []) or []:
            if isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
        if result.get("error"):
            logger.warning(f"[ToolCall] task={task.task_id} turn={turn} tool={tool_name} error={result['error']}")
            return
        url_preview = ", ".join(urls[:3]) if urls else "no-url"
        logger.info(
            f"[ToolCall] task={task.task_id} turn={turn} tool={tool_name} "
            f"total={result.get('total', 'n/a')} source={result.get('source', '')} urls={url_preview}"
        )

    def _append_assistant_and_tool_messages(
        self,
        response: dict,
        content: str,
        tool_calls: list,
        tool_results: list[dict],
        force_summary: bool,
    ) -> None:
        assistant_msg = {
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            assistant_msg["tool_calls"] = [dict(tc) for tc in tool_calls]
        if response.get("reasoning_content"):
            assistant_msg["reasoning_content"] = response["reasoning_content"]
        self.messages.append(assistant_msg)

        for tr in tool_results:
            msg_content = json.dumps(tr["result"], ensure_ascii=False, default=str)
            msg_content = self._maybe_compact_tool_content(msg_content, tr["name"])
            if force_summary:
                msg_content += "\n\n[SYSTEM NOTICE] You have already searched enough. Write your final summary NOW. Do NOT call any more tools."
            self.messages.append({
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": msg_content,
            })

    def _maybe_compact_tool_content(self, content: str, tool_name: str) -> str:
        """Compact tool result only when projected context exceeds the threshold.

        Error text is never compacted; preserving failure details is more important
        than saving context.
        """
        if self._looks_like_error_content(content):
            return content

        projected_chars = self._messages_chars(self.messages) + len(content)
        threshold_chars = int(
            self.config.context_budget_tokens
            * self.config.chars_per_token
            * self.config.compact_threshold_ratio
        )
        if projected_chars <= threshold_chars:
            return content
        if len(content) <= self.config.compact_tool_result_chars:
            return content

        compacted = self._head_tail_compact(content, self.config.compact_tool_result_chars)
        logger.info(
            f"[compact] tool={tool_name} chars={len(content)}->{len(compacted)} "
            f"projected={projected_chars}/{threshold_chars}"
        )
        if self.trace_recorder:
            self.trace_recorder.record(
                "compact",
                scope="tool_result",
                tool=tool_name,
                before_chars=len(content),
                after_chars=len(compacted),
                threshold_chars=threshold_chars,
                strategy="head_tail_70_30",
            )
        return compacted

    def _messages_chars(self, messages: list[dict]) -> int:
        total = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            total += len(str(message.get("content", "")))
            if message.get("tool_calls"):
                total += len(json.dumps(message.get("tool_calls"), ensure_ascii=False, default=str))
        return total

    def _head_tail_compact(self, text: str, max_chars: int, head_ratio: float = 0.70) -> str:
        head_chars = max(1, int(max_chars * head_ratio))
        tail_chars = max(1, max_chars - head_chars)
        omitted = max(0, len(text) - head_chars - tail_chars)
        return (
            f"{text[:head_chars].rstrip()}\n"
            f"[compact] omitted {omitted} chars from middle of tool result; preserved head/tail 70/30.\n"
            f"{text[-tail_chars:].lstrip()}"
        )

    def _looks_like_error_content(self, content: str) -> bool:
        text = (content or "").lower()
        return any(marker in text for marker in (
            '"error"',
            "error:",
            "failed",
            "traceback",
            "exception",
            "connection error",
            "request timed out",
            "api key",
        ))

    def _compact_old_messages(self) -> None:
        """每轮结束后检查总量，压缩旧的 tool 消息。

        当 messages 总字符数超过 budget 的 1.5 倍时，
        从旧到新扫描 tool 消息，将 > compact_tool_result_chars 的压缩。
        只压缩前 N-3 条 tool 消息（保留最近 3 轮交互完整）。
        """
        total_chars = self._messages_chars(self.messages)
        threshold = int(
            self.config.context_budget_tokens
            * self.config.chars_per_token
            * 1.5  # 1.5x compact_threshold_ratio
        )
        if total_chars <= threshold:
            return

        # 收集所有 tool 消息的索引
        tool_indices = [
            i for i, m in enumerate(self.messages)
            if isinstance(m, dict) and m.get("role") == "tool"
        ]
        # 保留最近 3 条 tool 消息不压缩
        compressible = tool_indices[:-3] if len(tool_indices) > 3 else []
        compressed_count = 0
        for idx in compressible:
            msg = self.messages[idx]
            content = msg.get("content", "")
            if len(content) > self.config.compact_tool_result_chars:
                msg["content"] = self._head_tail_compact(
                    content, self.config.compact_tool_result_chars
                )
                compressed_count += 1

        if compressed_count > 0:
            new_total = self._messages_chars(self.messages)
            logger.debug(
                f"[compact] 压缩旧消息: {compressed_count} 条, "
                f"{total_chars} → {new_total} chars"
            )

    def _maybe_force_tool_use(self, turn: int, fallback_tool: str) -> None:
        if turn > 0 and self.messages and self.messages[-1].get("role") == "assistant":
            last_tool_calls = self.messages[-1].get("tool_calls", [])
            if not last_tool_calls:
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"You did not use any tools. "
                        f"You MUST call the '{fallback_tool}' tool now to search for information. "
                        f"Do not write a summary without searching first."
                    ),
                })

    def _should_force_summary(self, tool_results: list[dict]) -> bool:
        all_empty = True
        for tr in tool_results:
            if tr["name"] == "web_search":
                res = tr["result"]
                if isinstance(res, dict) and res.get("results"):
                    for r in res["results"]:
                        if r.get("snippet", "").strip():
                            all_empty = False
                            break

        search_count = sum(
            1 for t in self.trajectory
            if t.get("role") == "tool" and t.get("name") == "web_search"
        )
        if search_count >= self.config.max_tool_calls_before_summary:
            return True
        if all_empty and tool_results:
            return True
        return False

    def _fallback_tool(self, task: SubTask) -> str:
        desc_lower = (task.description or "").lower()
        academic_keywords = ["论文", "paper", "publication", "学术", "arxiv", "neurips", "icml", "iclr", "scholar", "citation", "文献"]
        return "arxiv_reader" if any(kw in desc_lower for kw in academic_keywords) else "web_search"

    def _is_tool_failure_explanation(self, content: str) -> bool:
        if not content:
            return False
        c = content.lower()
        failure_keywords = [
            "无法通过", "无法执行", "无法使用", "无法获取", "无法访问",
            "额度已用尽", "配额已用完", "额度已用完", "搜索配额",
            "cannot search", "unable to search", "quota exceeded",
            "api key", "额度不足", "余额不足", "余额为", "余额：0",
            "网络错误", "连接失败", "无法连接到",
            "error: connection error", "error: request timed out",
            "connection error", "request timed out",
        ]
        return any(kw in c for kw in failure_keywords)

    def _extract_confidence(self, content: str) -> float:
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
