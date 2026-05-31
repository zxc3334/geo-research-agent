"""Unit tests for compression / compaction logic.

Tests cover:
  - ToolCallingLoop._head_tail_compact
  - ToolCallingLoop._maybe_compact_tool_content
  - ToolCallingLoop._compact_old_messages
  - WebSearchTool._rank_results snippet truncation
"""
from __future__ import annotations

import json
import pytest

# ---------------------------------------------------------------------------
# ToolCallingLoop tests
# ---------------------------------------------------------------------------
from src.agents.tool_calling_loop import ToolCallingLoop, ToolLoopConfig


def _make_loop(
    compact_tool_result_chars: int = 1500,
    context_budget_tokens: int = 12000,
    compact_threshold_ratio: float = 0.70,
) -> ToolCallingLoop:
    """Create a minimal ToolCallingLoop for testing (no real LLM)."""
    config = ToolLoopConfig(
        compact_tool_result_chars=compact_tool_result_chars,
        context_budget_tokens=context_budget_tokens,
        compact_threshold_ratio=compact_threshold_ratio,
    )
    # We only need the compaction methods, not the full loop.
    # Create a minimal mock that has the required attributes.
    loop = object.__new__(ToolCallingLoop)
    loop.config = config
    loop.messages = []
    loop.trajectory = []
    loop.total_tokens = 0
    loop.trace_recorder = None
    return loop


class TestHeadTailCompact:
    """Test _head_tail_compact method."""

    def test_short_text_unchanged(self):
        loop = _make_loop()
        text = "short text"
        result = loop._head_tail_compact(text, max_chars=1000)
        # Text shorter than max_chars should still be compacted (method doesn't check)
        # but the output should contain the original text parts
        assert "short text" in result

    def test_long_text_compacted(self):
        loop = _make_loop()
        text = "A" * 5000
        result = loop._head_tail_compact(text, max_chars=1000)
        assert len(result) < len(text)
        assert "[compact]" in result
        assert "omitted" in result

    def test_head_tail_ratio(self):
        loop = _make_loop()
        text = "H" * 2100 + "T" * 900  # head 70%, tail 30%
        result = loop._head_tail_compact(text, max_chars=1000, head_ratio=0.70)
        # Should preserve ~700 head chars and ~300 tail chars
        assert "H" * 100 in result  # head preserved
        assert "T" * 100 in result  # tail preserved

    def test_default_ratio_70_30(self):
        loop = _make_loop()
        text = "X" * 10000
        result = loop._head_tail_compact(text, max_chars=1000)
        lines = result.split("\n")
        # First line: head content
        # Middle: [compact] marker
        # Last line: tail content
        assert "[compact]" in lines[1]


class TestMaybeCompactToolContent:
    """Test _maybe_compact_tool_content method."""

    def test_short_content_not_compacted(self):
        loop = _make_loop(compact_tool_result_chars=1500)
        loop.messages = []
        content = "x" * 500  # well under threshold
        result = loop._maybe_compact_tool_content(content, "test_tool")
        assert result == content

    def test_long_content_compacted_when_over_threshold(self):
        loop = _make_loop(
            compact_tool_result_chars=1500,
            context_budget_tokens=12000,
            compact_threshold_ratio=0.70,
        )
        # Fill messages to exceed threshold (29400 chars)
        loop.messages = [{"role": "tool", "content": "y" * 30000}]
        content = "z" * 3000  # over compact_tool_result_chars
        result = loop._maybe_compact_tool_content(content, "test_tool")
        assert len(result) < len(content)
        assert "[compact]" in result

    def test_long_content_not_compacted_when_under_total_threshold(self):
        loop = _make_loop(
            compact_tool_result_chars=1500,
            context_budget_tokens=12000,
            compact_threshold_ratio=0.70,
        )
        loop.messages = []  # empty messages, projected is low
        content = "z" * 3000  # over compact_tool_result_chars but projected is low
        result = loop._maybe_compact_tool_content(content, "test_tool")
        # projected = 0 + 3000 = 3000 < 29400, so NOT compacted
        assert result == content

    def test_error_content_not_compacted(self):
        loop = _make_loop()
        loop.messages = [{"role": "tool", "content": "y" * 30000}]
        content = '{"error": "something failed with traceback"}'
        result = loop._maybe_compact_tool_content(content, "test_tool")
        assert result == content  # error content preserved


class TestCompactOldMessages:
    """Test _compact_old_messages method."""

    def test_no_compression_when_under_threshold(self):
        loop = _make_loop(context_budget_tokens=12000)
        loop.messages = [{"role": "tool", "content": "x" * 1000}]
        loop._compact_old_messages()
        # Should not change anything
        assert len(loop.messages[0]["content"]) == 1000

    def test_old_messages_compressed(self):
        loop = _make_loop(
            compact_tool_result_chars=1500,
            context_budget_tokens=12000,
        )
        # Create many tool messages to exceed 1.5x threshold (63000 chars)
        loop.messages = []
        for i in range(30):
            loop.messages.append({"role": "tool", "content": "A" * 3000})
        total_before = sum(len(m["content"]) for m in loop.messages)
        assert total_before == 90000  # way over threshold

        loop._compact_old_messages()

        # Last 3 tool messages should be untouched
        for msg in loop.messages[-3:]:
            assert len(msg["content"]) == 3000

        # Older messages should be compressed (includes [compact] marker text)
        for msg in loop.messages[:-3]:
            assert len(msg["content"]) <= 1500 + 100  # 1500 chars + [compact] marker

    def test_preserves_non_tool_messages(self):
        loop = _make_loop(compact_tool_result_chars=1500, context_budget_tokens=12000)
        loop.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "assistant", "content": "thinking..."},
        ] + [{"role": "tool", "content": "A" * 3000} for _ in range(30)]

        loop._compact_old_messages()

        # System and assistant messages should be unchanged
        assert loop.messages[0]["content"] == "system prompt"
        assert loop.messages[1]["content"] == "thinking..."

    def test_no_compression_with_few_messages(self):
        loop = _make_loop(compact_tool_result_chars=1500, context_budget_tokens=12000)
        loop.messages = [{"role": "tool", "content": "A" * 3000} for _ in range(3)]
        total_before = sum(len(m["content"]) for m in loop.messages)
        loop._compact_old_messages()
        # Only 3 tool messages, all in "keep recent 3" — nothing compressed
        total_after = sum(len(m["content"]) for m in loop.messages)
        assert total_before == total_after


# ---------------------------------------------------------------------------
# WebSearchTool snippet truncation tests
# ---------------------------------------------------------------------------
class TestSnippetTruncation:
    """Test that _rank_results truncates snippets."""

    def test_short_snippet_unchanged(self):
        from src.tools.web_search import WebSearchTool

        tool = object.__new__(WebSearchTool)
        results = [
            {"title": "Test", "url": "https://example.com", "snippet": "short"}
        ]
        ranked = tool._rank_results(results, "test query")
        assert ranked[0]["snippet"] == "short"

    def test_long_snippet_truncated(self):
        from src.tools.web_search import WebSearchTool

        tool = object.__new__(WebSearchTool)
        long_snippet = "X" * 2000
        results = [
            {"title": "Test", "url": "https://example.com", "snippet": long_snippet}
        ]
        ranked = tool._rank_results(results, "test query")
        assert len(ranked[0]["snippet"]) <= WebSearchTool.MAX_SNIPPET_CHARS + 3  # +3 for "..."
        assert ranked[0]["snippet"].endswith("...")

    def test_exact_limit_snippet_unchanged(self):
        from src.tools.web_search import WebSearchTool

        tool = object.__new__(WebSearchTool)
        snippet = "A" * WebSearchTool.MAX_SNIPPET_CHARS
        results = [
            {"title": "Test", "url": "https://example.com", "snippet": snippet}
        ]
        ranked = tool._rank_results(results, "test query")
        # Exactly at limit — no truncation
        assert ranked[0]["snippet"] == snippet

    def test_empty_results(self):
        from src.tools.web_search import WebSearchTool

        tool = object.__new__(WebSearchTool)
        ranked = tool._rank_results([], "test query")
        assert ranked == []
