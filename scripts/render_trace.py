#!/usr/bin/env python3
"""Render GeoResearch JSONL traces into a local HTML dashboard.

The JSONL file remains the source of truth for machines. This renderer creates
an offline, human-readable view for debugging and demos: run summary, task DAG,
execution timeline, tool calls, evidence, and LLM usage.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens")
EVIDENCE_ORDER = ("verified", "evidence_backed", "speculative", "rejected")


@dataclass
class TraceModel:
    trace_path: Path
    events: list[dict[str, Any]]
    by_event: Counter
    planned_tasks: dict[str, dict[str, Any]]
    task_starts: dict[str, float]
    task_ends: dict[str, dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    compact_events: list[dict[str, Any]]
    run_start: dict[str, Any]
    run_end: dict[str, Any]
    research_end: dict[str, Any]


def load_trace(trace_path: str | Path) -> TraceModel:
    path = Path(trace_path)
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc

    by_event = Counter(str(event.get("event", "")) for event in events)
    planned_tasks = {
        str(event.get("task_id")): event
        for event in events
        if event.get("event") == "task_planned" and event.get("task_id")
    }
    task_starts = {
        str(event.get("task_id")): float(event.get("ts", 0))
        for event in events
        if event.get("event") == "task_start" and event.get("task_id")
    }
    task_ends = {
        str(event.get("task_id")): event
        for event in events
        if event.get("event") == "task_end" and event.get("task_id")
    }
    return TraceModel(
        trace_path=path,
        events=events,
        by_event=by_event,
        planned_tasks=planned_tasks,
        task_starts=task_starts,
        task_ends=task_ends,
        llm_calls=[e for e in events if e.get("event") == "llm_call"],
        tool_calls=[e for e in events if e.get("event") == "tool_call"],
        tool_results=[e for e in events if e.get("event") == "tool_result"],
        evidence_items=[e for e in events if e.get("event") == "evidence_item"],
        compact_events=[e for e in events if e.get("event") == "compact"],
        run_start=last_event(events, "run_start"),
        run_end=last_event(events, "run_end"),
        research_end=last_event(events, "research_end"),
    )


def render_trace_report(trace_path: str | Path, output_path: str | Path | None = None) -> str:
    model = load_trace(trace_path)
    output = Path(output_path) if output_path else model.trace_path.with_name("trace_report.html")
    output.write_text(render_html(model), encoding="utf-8")
    return str(output)


def render_html(model: TraceModel) -> str:
    summary = build_summary(model)
    css = build_css()
    js = build_js()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GeoResearch Trace Viewer</title>
  <style>{css}</style>
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="app-header">
    <div>
      <p class="eyebrow">GeoResearch Agent</p>
      <h1>Trace Viewer</h1>
      <p class="subtitle">{escape(summary["query"])}</p>
    </div>
    <div class="run-meta" aria-label="Run metadata">
      <span>{escape(summary["run_id"])}</span>
      <span>{escape(summary["generated_at"])}</span>
    </div>
  </header>
  <main id="main" class="layout">
    <section class="summary-grid" aria-label="Run summary">
      {metric_card("Status", summary["status"], "Execution result")}
      {metric_card("Elapsed", f'{summary["elapsed_seconds"]:.1f}s', "End-to-end runtime")}
      {metric_card("Tasks", f'{summary["tasks_done"]}/{summary["tasks_total"]}', "Completed subtasks")}
      {metric_card("Tool Calls", str(summary["tool_calls"]), "Executed tool calls")}
      {metric_card("LLM Calls", str(summary["llm_calls"]), "Model invocations")}
      {metric_card("Tokens", format_int(summary["usage"]["total_tokens"]), "Total normalized tokens")}
      {metric_card("Cache Hit", f'{summary["usage"]["cache_hit_rate"]:.1%}', "Read-cache share")}
      {metric_card("Evidence", str(summary["evidence_total"]), "Classified evidence items")}
    </section>

    <section class="tabs" aria-label="Trace sections">
      <div class="tab-list" role="tablist">
        {tab_button("overview", "Overview", True)}
        {tab_button("timeline", "Timeline", False)}
        {tab_button("tools", "Tools", False)}
        {tab_button("evidence", "Evidence", False)}
        {tab_button("usage", "Usage", False)}
        {tab_button("events", "Events", False)}
      </div>

      <section id="panel-overview" class="tab-panel active" role="tabpanel" tabindex="0">
        <div class="split">
          <article class="panel">
            <div class="panel-header">
              <h2>Task DAG</h2>
              <p>Planner dependencies and execution status.</p>
            </div>
            {render_dag(model)}
          </article>
          <article class="panel">
            <div class="panel-header">
              <h2>Evidence Mix</h2>
              <p>Claim-level verification distribution.</p>
            </div>
            {render_evidence_bars(model)}
          </article>
        </div>
      </section>

      <section id="panel-timeline" class="tab-panel" role="tabpanel" tabindex="0">
        <article class="panel">
          <div class="panel-header">
            <h2>Execution Timeline</h2>
            <p>Relative timing of tasks, LLM calls, tools, and synthesis.</p>
          </div>
          {render_timeline(model)}
        </article>
      </section>

      <section id="panel-tools" class="tab-panel" role="tabpanel" tabindex="0">
        <article class="panel">
          <div class="panel-header">
            <h2>Tool Calls</h2>
            <p>Arguments, result source, status, and returned URLs.</p>
          </div>
          {render_tool_table(model)}
        </article>
      </section>

      <section id="panel-evidence" class="tab-panel" role="tabpanel" tabindex="0">
        <article class="panel">
          <div class="panel-header">
            <h2>Evidence Items</h2>
            <p>Claims classified by verification level.</p>
          </div>
          {render_evidence_table(model)}
        </article>
      </section>

      <section id="panel-usage" class="tab-panel" role="tabpanel" tabindex="0">
        <div class="split">
          <article class="panel">
            <div class="panel-header">
              <h2>LLM Usage By Call</h2>
              <p>Input, output, and cache-read tokens per model call.</p>
            </div>
            {render_usage_chart(model)}
          </article>
          <article class="panel">
            <div class="panel-header">
              <h2>Usage By Task</h2>
              <p>Aggregated normalized token usage.</p>
            </div>
            {render_usage_by_task(model)}
          </article>
        </div>
      </section>

      <section id="panel-events" class="tab-panel" role="tabpanel" tabindex="0">
        <article class="panel">
          <div class="panel-header">
            <h2>Event Stream</h2>
            <p>Compact event index for searching and audit.</p>
          </div>
          {render_event_table(model)}
        </article>
      </section>
    </section>
  </main>
  <script>{js}</script>
</body>
</html>
"""


def build_summary(model: TraceModel) -> dict[str, Any]:
    usage = sum_usage(model.llm_calls)
    elapsed = float(model.research_end.get("elapsed_seconds") or 0)
    if not elapsed and model.run_start and model.run_end:
        elapsed = float(model.run_end.get("ts", 0)) - float(model.run_start.get("ts", 0))
    status = str(model.run_end.get("status", "unknown"))
    query = str(model.run_start.get("query") or first_event(model.events, "research_start").get("query", ""))
    run_id = str(model.events[0].get("run_id", "")) if model.events else ""
    return {
        "status": status,
        "query": query,
        "run_id": run_id,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": max(0.0, elapsed),
        "tasks_total": len(model.planned_tasks),
        "tasks_done": len(model.task_ends),
        "tool_calls": len(model.tool_calls),
        "llm_calls": len(model.llm_calls),
        "usage": usage,
        "evidence_total": len(model.evidence_items),
    }


def metric_card(label: str, value: str, hint: str) -> str:
    return f"""
      <article class="metric" aria-label="{escape(label)}: {escape(value)}">
        <span>{escape(label)}</span>
        <strong>{escape(value)}</strong>
        <small>{escape(hint)}</small>
      </article>
    """


def tab_button(tab_id: str, label: str, active: bool) -> str:
    selected = "true" if active else "false"
    cls = "tab-button active" if active else "tab-button"
    return (
        f'<button class="{cls}" role="tab" aria-selected="{selected}" '
        f'aria-controls="panel-{tab_id}" data-tab="{tab_id}">{escape(label)}</button>'
    )


def render_dag(model: TraceModel) -> str:
    tasks = list(model.planned_tasks.values())
    if not tasks:
        return '<p class="empty">No planned tasks recorded.</p>'
    layers = dag_layers(model.planned_tasks)
    node_w, node_h, x_gap, y_gap = 220, 88, 70, 44
    width = max(320, len(layers) * (node_w + x_gap) + 40)
    height = max(220, max(len(layer) for layer in layers) * (node_h + y_gap) + 30)
    positions: dict[str, tuple[int, int]] = {}
    for col, layer in enumerate(layers):
        layer_height = len(layer) * node_h + max(0, len(layer) - 1) * y_gap
        start_y = max(20, (height - layer_height) // 2)
        for row, task_id in enumerate(layer):
            positions[task_id] = (20 + col * (node_w + x_gap), start_y + row * (node_h + y_gap))

    edges = []
    nodes = []
    for task_id, task in model.planned_tasks.items():
        x, y = positions.get(task_id, (20, 20))
        for dep in task.get("dependencies", []) or []:
            if dep not in positions:
                continue
            dx, dy = positions[dep]
            x1, y1 = dx + node_w, dy + node_h // 2
            x2, y2 = x, y + node_h // 2
            edges.append(
                f'<path d="M{x1},{y1} C{x1 + 38},{y1} {x2 - 38},{y2} {x2},{y2}" '
                'class="edge" marker-end="url(#arrow)" />'
            )
        status = model.task_ends.get(task_id, {}).get("status", "planned")
        level = "ok" if status == "success" else "warn" if status in ("failed", "timeout") else "idle"
        task_type = str(task.get("task_type", ""))
        label = f"{task_id}"
        status_label = f"{task_type} / {status}"
        desc = truncate_svg_text(str(task.get("description", "")), 42)
        nodes.append(
            f'<g class="node node-{level}">'
            f'<title>{escape(str(task.get("description", "")))}</title>'
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="8" />'
            f'<text x="{x + 14}" y="{y + 24}" class="node-title">{escape(label)}</text>'
            f'<text x="{x + 14}" y="{y + 44}" class="node-status">{escape(status_label)}</text>'
            f'<text x="{x + 14}" y="{y + 64}" class="node-desc">{escape(desc[:21])}</text>'
            f'<text x="{x + 14}" y="{y + 80}" class="node-desc">{escape(desc[21:42])}</text>'
            f'</g>'
        )

    return f"""
    <div class="svg-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="Task DAG">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#708099" />
          </marker>
        </defs>
        {''.join(edges)}
        {''.join(nodes)}
      </svg>
    </div>
    """


def render_timeline(model: TraceModel) -> str:
    if not model.events:
        return '<p class="empty">No events recorded.</p>'
    start_ts = float(model.run_start.get("ts") or model.events[0].get("ts") or 0)
    end_ts = float(model.research_end.get("ts") or model.run_end.get("ts") or model.events[-1].get("ts") or start_ts)
    duration = max(1.0, end_ts - start_ts)
    rows = []
    for task_id in sorted(model.planned_tasks, key=natural_task_key):
        start = model.task_starts.get(task_id)
        end_event = model.task_ends.get(task_id)
        if not start or not end_event:
            continue
        end = float(end_event.get("ts", start))
        left = pct(start - start_ts, duration)
        width = max(1.0, pct(end - start, duration))
        status = str(end_event.get("status", "unknown"))
        calls = [e for e in model.llm_calls + model.tool_calls if e.get("task_id") == task_id]
        markers = []
        for event in sorted(calls, key=lambda e: e.get("ts", 0)):
            m_left = pct(float(event.get("ts", start)) - start, max(1.0, end - start))
            cls = "marker llm" if event.get("event") == "llm_call" else "marker tool"
            title = "LLM" if event.get("event") == "llm_call" else f"Tool: {event.get('tool', '')}"
            markers.append(f'<span class="{cls}" style="left:{m_left:.2f}%" title="{escape(title)}"></span>')
        marker_html = "".join(markers)
        rows.append(
            f'<div class="timeline-row">'
            f'<div class="timeline-label"><strong>{escape(task_id)}</strong><span>{escape(status)}</span></div>'
            f'<div class="timeline-track"><div class="timeline-bar status-{escape(status)}" style="left:{left:.2f}%;width:{width:.2f}%">{marker_html}</div></div>'
            f'<div class="timeline-time">{end - start:.1f}s</div>'
            f'</div>'
        )
    rows_html = "".join(rows)
    return f'<div class="timeline">{rows_html}</div><p class="legend"><span class="dot llm"></span>LLM call <span class="dot tool"></span>Tool call</p>'


def render_tool_table(model: TraceModel) -> str:
    if not model.tool_calls:
        return '<p class="empty">No tool calls recorded.</p>'
    rows = []
    result_lookup = defaultdict(list)
    for result in model.tool_results:
        key = (result.get("task_id"), result.get("turn"), result.get("tool"))
        result_lookup[key].append(result)
    for call in model.tool_calls:
        key = (call.get("task_id"), call.get("turn"), call.get("tool"))
        result = result_lookup.get(key, [{}])[0]
        urls = result.get("urls") or []
        url_links = "<br>".join(link(url) for url in urls[:4]) or '<span class="muted">No URL</span>'
        rows.append(
            "<tr>"
            f"<td>{escape(str(call.get('task_id', '')))}</td>"
            f"<td><span class=\"pill neutral\">{escape(str(call.get('tool', '')))}</span></td>"
            f"<td>{escape(str(call.get('turn', '')))}</td>"
            f"<td><code>{escape(json.dumps(call.get('args', {}), ensure_ascii=False, default=str)[:360])}</code></td>"
            f"<td>{escape(str(result.get('status', 'unknown')))}</td>"
            f"<td>{escape(str(result.get('source', '')))}</td>"
            f"<td>{url_links}</td>"
            "</tr>"
        )
    return table(["Task", "Tool", "Turn", "Args", "Status", "Source", "URLs"], rows)


def render_evidence_table(model: TraceModel) -> str:
    if not model.evidence_items:
        return '<p class="empty">No evidence items recorded.</p>'
    rows = []
    for item in model.evidence_items:
        level = str(item.get("level", ""))
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        source_type = str(item.get("source_type") or metadata.get("source_type") or "")
        source_type_html = escape(source_type) if source_type else '<span class="muted">unknown</span>'
        source_html = link(str(item.get("source", ""))) if item.get("source") else '<span class="muted">No source</span>'
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('task_id', '')))}</td>"
            f"<td><span class=\"pill evidence-{escape(level)}\">{escape(level)}</span></td>"
            f"<td>{source_type_html}</td>"
            f"<td>{escape(truncate(str(item.get('claim', '')), 220))}</td>"
            f"<td>{source_html}</td>"
            f"<td>{escape(truncate(str(item.get('rationale', '')), 180))}</td>"
            f"<td>{escape(str(item.get('confidence', '')))}</td>"
            "</tr>"
        )
    return table(["Task", "Level", "Source Type", "Claim", "Source", "Rationale", "Confidence"], rows)


def render_evidence_bars(model: TraceModel) -> str:
    counts = Counter(str(item.get("level", "")) for item in model.evidence_items)
    total = max(1, sum(counts.values()))
    bars = []
    for level in EVIDENCE_ORDER:
        count = counts.get(level, 0)
        bars.append(
            f'<div class="bar-row"><span>{escape(level)}</span>'
            f'<div class="bar-bg"><div class="bar evidence-{escape(level)}" style="width:{(count / total) * 100:.2f}%"></div></div>'
            f'<strong>{count}</strong></div>'
        )
    return '<div class="bars">' + "".join(bars) + "</div>"


def render_usage_chart(model: TraceModel) -> str:
    if not model.llm_calls:
        return '<p class="empty">No LLM calls recorded.</p>'
    max_tokens = max(int((call.get("usage") or {}).get("total_tokens", 0) or 0) for call in model.llm_calls) or 1
    rows = []
    for i, call in enumerate(model.llm_calls, 1):
        usage = call.get("usage") or {}
        task = str(call.get("task_id", ""))
        role = str(call.get("role", ""))
        input_w = pct(int(usage.get("input_tokens", 0) or 0), max_tokens)
        output_w = pct(int(usage.get("output_tokens", 0) or 0), max_tokens)
        cache_w = pct(int(usage.get("cache_read_tokens", 0) or 0), max_tokens)
        rows.append(
            f'<div class="usage-row">'
            f'<div class="usage-label"><strong>#{i} {escape(task)}</strong><span>{escape(role)}</span></div>'
            f'<div class="stacked" title="{escape(str(usage))}">'
            f'<span class="seg input" style="width:{input_w:.2f}%"></span>'
            f'<span class="seg output" style="width:{output_w:.2f}%"></span>'
            f'<span class="seg cache" style="width:{cache_w:.2f}%"></span>'
            f'</div>'
            f'<div class="usage-total">{format_int(int(usage.get("total_tokens", 0) or 0))}</div>'
            f'</div>'
        )
    return '<div class="usage-chart">' + "".join(rows) + '</div><p class="legend"><span class="dot input"></span>Input <span class="dot output"></span>Output <span class="dot cache"></span>Cache read</p>'


def render_usage_by_task(model: TraceModel) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in model.llm_calls:
        grouped[str(call.get("task_id") or "unknown")].append(call)
    rows = []
    for task, calls in sorted(grouped.items(), key=lambda kv: natural_task_key(kv[0])):
        usage = sum_usage(calls)
        rows.append(
            "<tr>"
            f"<td>{escape(task)}</td>"
            f"<td>{format_int(usage['input_tokens'])}</td>"
            f"<td>{format_int(usage['output_tokens'])}</td>"
            f"<td>{format_int(usage['cache_read_tokens'])}</td>"
            f"<td>{format_int(usage['total_tokens'])}</td>"
            f"<td>{usage['cache_hit_rate']:.1%}</td>"
            "</tr>"
        )
    return table(["Task", "Input", "Output", "Cache Read", "Total", "Cache Hit"], rows)


def render_event_table(model: TraceModel) -> str:
    rows = []
    base = float(model.events[0].get("ts", 0)) if model.events else 0
    for event in model.events:
        event_name = str(event.get("event", ""))
        detail = compact_event_detail(event)
        rows.append(
            "<tr>"
            f"<td>{float(event.get('ts', 0)) - base:.2f}s</td>"
            f"<td><span class=\"pill neutral\">{escape(event_name)}</span></td>"
            f"<td>{escape(str(event.get('task_id', '')))}</td>"
            f"<td>{escape(detail)}</td>"
            "</tr>"
        )
    return table(["Time", "Event", "Task", "Detail"], rows)


def compact_event_detail(event: dict[str, Any]) -> str:
    name = event.get("event")
    if name == "llm_call":
        usage = event.get("usage") or {}
        return f"role={event.get('role')} total_tokens={usage.get('total_tokens')} tool_calls={event.get('tool_call_count', 0)}"
    if name == "tool_call":
        return f"tool={event.get('tool')} args={json.dumps(event.get('args', {}), ensure_ascii=False, default=str)[:180]}"
    if name == "tool_result":
        return f"tool={event.get('tool')} status={event.get('status')} urls={event.get('url_count', 0)}"
    if name == "evidence_item":
        return f"{event.get('level')}: {truncate(str(event.get('claim', '')), 160)}"
    if name == "state_transition":
        return f"state={event.get('state')}"
    return truncate(json.dumps({k: v for k, v in event.items() if k not in ('ts', 'run_id', 'event')}, ensure_ascii=False, default=str), 220)


def table(headers: list[str], rows: list[str]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join(rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def dag_layers(tasks: dict[str, dict[str, Any]]) -> list[list[str]]:
    pending = set(tasks)
    done: set[str] = set()
    layers: list[list[str]] = []
    while pending:
        ready = sorted(
            [
                task_id for task_id in pending
                if all(dep in done or dep not in tasks for dep in tasks[task_id].get("dependencies", []) or [])
            ],
            key=natural_task_key,
        )
        if not ready:
            ready = [sorted(pending, key=natural_task_key)[0]]
        layers.append(ready)
        done.update(ready)
        pending.difference_update(ready)
    return layers


def sum_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in USAGE_KEYS}
    for event in events:
        usage = event.get("usage") or {}
        for key in USAGE_KEYS:
            totals[key] += int(usage.get(key, 0) or 0)
    denominator = totals["input_tokens"] + totals["cache_read_tokens"]
    totals["cache_hit_rate"] = (totals["cache_read_tokens"] / denominator) if denominator else 0.0
    return totals


def first_event(events: list[dict[str, Any]], event_name: str) -> dict[str, Any]:
    return next((event for event in events if event.get("event") == event_name), {})


def last_event(events: list[dict[str, Any]], event_name: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event") == event_name:
            return event
    return {}


def natural_task_key(value: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return (int(digits) if digits else 9999, str(value))


def pct(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (value / total) * 100.0))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def truncate_svg_text(text: str, limit: int) -> str:
    """Shorten SVG labels aggressively because SVG text does not auto-wrap."""
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def format_int(value: int | float) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def link(url: str) -> str:
    if not url:
        return '<span class="muted">No URL</span>'
    safe = escape(url)
    if url.startswith("http://") or url.startswith("https://"):
        return f'<a href="{safe}" target="_blank" rel="noreferrer">{safe}</a>'
    return f"<code>{safe}</code>"


def build_css() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --surface: #ffffff;
  --surface-2: #eef3f8;
  --text: #17202f;
  --muted: #5f6c7d;
  --line: #dbe3ee;
  --primary: #165d7d;
  --primary-2: #1f7a8c;
  --green: #247a4b;
  --amber: #946200;
  --red: #b13a3a;
  --blue: #2866b2;
  --violet: #6554a5;
  --shadow: 0 1px 2px rgba(18, 31, 47, 0.08), 0 8px 24px rgba(18, 31, 47, 0.06);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}
.skip-link { position: absolute; left: -999px; top: 8px; background: var(--text); color: #fff; padding: 8px 12px; z-index: 10; }
.skip-link:focus { left: 8px; }
.app-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 32px 18px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.eyebrow { margin: 0 0 4px; color: var(--primary); font-weight: 700; font-size: 13px; }
h1 { margin: 0; font-size: 32px; line-height: 1.15; letter-spacing: 0; }
h2 { margin: 0; font-size: 18px; line-height: 1.3; letter-spacing: 0; }
.subtitle { max-width: 980px; margin: 10px 0 0; color: var(--muted); }
.run-meta { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; min-width: 180px; color: var(--muted); font-size: 13px; }
.layout { padding: 24px 32px 40px; }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(8, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.metric {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  box-shadow: var(--shadow);
  min-height: 116px;
}
.metric span, .metric small { color: var(--muted); display: block; font-size: 13px; }
.metric strong { display: block; margin: 8px 0 6px; font-size: 24px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.tabs { background: transparent; }
.tab-list { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.tab-button {
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-weight: 650;
}
.tab-button:hover, .tab-button:focus-visible { border-color: var(--primary); outline: 3px solid rgba(31, 122, 140, 0.18); }
.tab-button.active { background: var(--primary); border-color: var(--primary); color: #fff; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.split { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr); gap: 16px; }
.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.panel-header { padding: 18px 20px; border-bottom: 1px solid var(--line); }
.panel-header p { margin: 4px 0 0; color: var(--muted); font-size: 14px; }
.svg-wrap { overflow: auto; padding: 12px; }
svg { width: 100%; min-width: 640px; height: auto; }
.edge { fill: none; stroke: #708099; stroke-width: 1.8; }
.node rect { fill: #f8fbfd; stroke: #c9d6e5; }
.node-ok rect { fill: #f0f8f3; stroke: #87b99d; }
.node-warn rect { fill: #fff5f5; stroke: #d68d8d; }
.node-title { font-size: 13px; font-weight: 700; fill: var(--text); }
.node-status { font-size: 11px; font-weight: 700; fill: var(--primary); }
.node-desc { font-size: 11px; fill: var(--muted); }
.timeline { padding: 14px 18px 20px; }
.timeline-row { display: grid; grid-template-columns: 160px minmax(240px, 1fr) 70px; gap: 12px; align-items: center; min-height: 48px; }
.timeline-label strong, .timeline-label span { display: block; }
.timeline-label span { color: var(--muted); font-size: 13px; }
.timeline-track { position: relative; height: 22px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.timeline-bar { position: absolute; top: 0; height: 22px; border-radius: 999px; background: var(--primary-2); }
.status-failed, .status-timeout { background: var(--red); }
.timeline-time { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.marker { position: absolute; top: 5px; width: 11px; height: 11px; border-radius: 50%; border: 2px solid #fff; }
.marker.llm, .dot.llm { background: var(--violet); }
.marker.tool, .dot.tool { background: var(--amber); }
.legend { padding: 0 18px 16px; color: var(--muted); font-size: 13px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 5px 0 16px; }
.dot:first-child { margin-left: 0; }
.dot.input, .seg.input { background: var(--blue); }
.dot.output, .seg.output { background: var(--green); }
.dot.cache, .seg.cache { background: var(--violet); }
.table-wrap { overflow: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { position: sticky; top: 0; background: #f8fbfd; text-align: left; color: var(--muted); font-weight: 700; border-bottom: 1px solid var(--line); }
th, td { padding: 11px 12px; vertical-align: top; border-bottom: 1px solid var(--line); }
code { font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
a { color: var(--primary); text-decoration-thickness: 1px; text-underline-offset: 3px; }
.muted { color: var(--muted); }
.pill { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.neutral { background: #eef3f8; color: #36516f; }
.evidence-verified { background: #eaf7ef; color: var(--green); }
.evidence-evidence_backed { background: #eaf3ff; color: var(--blue); }
.evidence-speculative { background: #fff5dd; color: var(--amber); }
.evidence-rejected { background: #fff0f0; color: var(--red); }
.bars { padding: 18px 20px 24px; display: grid; gap: 14px; }
.bar-row { display: grid; grid-template-columns: 140px 1fr 44px; align-items: center; gap: 12px; }
.bar-bg { height: 14px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.bar { height: 14px; border-radius: 999px; }
.usage-chart { padding: 16px 18px; display: grid; gap: 10px; }
.usage-row { display: grid; grid-template-columns: 170px 1fr 80px; gap: 12px; align-items: center; }
.usage-label strong, .usage-label span { display: block; }
.usage-label span { color: var(--muted); font-size: 13px; }
.stacked { display: flex; height: 18px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.seg { display: block; height: 18px; min-width: 1px; }
.usage-total { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
.empty { padding: 22px; color: var(--muted); }
@media (max-width: 1180px) {
  .summary-grid { grid-template-columns: repeat(4, minmax(140px, 1fr)); }
  .split { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .app-header { flex-direction: column; padding: 22px 18px 16px; }
  .run-meta { align-items: flex-start; }
  .layout { padding: 18px; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timeline-row, .usage-row { grid-template-columns: 1fr; gap: 6px; padding-bottom: 12px; }
  .timeline-time, .usage-total { text-align: left; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; transition: none !important; }
}
"""


def build_js() -> str:
    return """
document.querySelectorAll('[data-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    const tab = button.dataset.tab;
    document.querySelectorAll('[data-tab]').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === tab);
      b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.id === `panel-${tab}`);
    });
  });
});
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render GeoResearch trace.jsonl into an HTML dashboard.")
    parser.add_argument("--trace", required=True, help="Path to trace.jsonl")
    parser.add_argument("--output", default=None, help="Output HTML path. Defaults to trace_report.html next to trace.")
    args = parser.parse_args()
    output = render_trace_report(args.trace, args.output)
    print(output)


if __name__ == "__main__":
    main()
