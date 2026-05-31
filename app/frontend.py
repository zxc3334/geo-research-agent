"""GeoResearch Agent — Minimal Gradio Web UI.

Run standalone:    python app/frontend.py
Run with FastAPI:  mount via gr.mount_gradio_app(app, demo, path="/ui")
"""
from __future__ import annotations

import asyncio
import json

import gradio as gr
import httpx

API_BASE = "http://127.0.0.1:8000/api/v1"


async def submit_research(query: str, domain: str, depth: int):
    """Submit a research task and return task_id."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{API_BASE}/research",
            json={
                "query": query,
                "domain": domain,
                "depth": depth,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["task_id"], data["status"]


async def poll_progress(task_id: str):
    """Poll task status until completed or failed."""
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            resp = await client.get(f"{API_BASE}/tasks/{task_id}")
            data = resp.json()
            phase = data.get("progress", {}).get("phase", "")
            status = data.get("status", "")
            error = data.get("error", "")
            yield status, phase, error
            if status in ("completed", "failed"):
                break
            await asyncio.sleep(2)


async def get_report(task_id: str):
    """Fetch the final report."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_BASE}/reports/{task_id}")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("content_markdown", ""), data.get("evidence_summary", {})
        return f"Report not available (status {resp.status_code})", {}


async def run_research(query: str, domain: str, depth: int):
    """Full research flow: submit → poll → report."""
    if not query.strip():
        yield "❌ 请输入研究问题", "", ""
        return

    # Submit
    try:
        task_id, _ = await submit_research(query, domain, depth)
    except Exception as e:
        yield f"❌ 提交失败: {e}", "", ""
        return

    yield f"✅ 任务已提交: `{task_id}`\n⏳ 研究中...", "", ""

    # Poll progress
    async for status, phase, error in poll_progress(task_id):
        if status == "running":
            yield f"⏳ **{phase}** ... (任务 {task_id[:8]}...)", "", ""
        elif status == "failed":
            yield f"❌ 失败: {error}", "", ""
            return

    # Get report
    try:
        markdown, evidence = await get_report(task_id)
        evidence_text = ""
        if evidence:
            evidence_text = "### 证据分级\n\n"
            for level, count in evidence.items():
                evidence_text += f"- **{level}**: {count}\n"
        yield f"✅ 完成！任务 {task_id[:8]}...", markdown, evidence_text
    except Exception as e:
        yield f"❌ 获取报告失败: {e}", "", ""


# ── Gradio UI ────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="GeoResearch Agent",
    ) as demo:
        gr.Markdown(
            "# 🌍 GeoResearch Agent\n"
            "**AI 深度研究 Agent** — 输入研究问题，自动搜索官方文档、学术论文和领域知识库，生成带证据分级的研究报告。\n"
            "---"
        )

        with gr.Row():
            with gr.Column(scale=1):
                query_input = gr.Textbox(
                    label="研究问题",
                    placeholder="例如：如何利用 Landsat 和 Sentinel-2 数据进行城市热岛效应分析？",
                    lines=3,
                )
                with gr.Row():
                    domain_select = gr.Dropdown(
                        choices=["geo_rs", "general"],
                        value="geo_rs",
                        label="领域",
                    )
                    depth_slider = gr.Slider(
                        minimum=1, maximum=5, value=2, step=1,
                        label="研究深度",
                    )
                submit_btn = gr.Button("🚀 开始研究", variant="primary", size="lg")
                status_output = gr.Markdown(label="状态")

            with gr.Column(scale=2):
                report_output = gr.Markdown(
                    label="研究报告",
                    value="*等待提交研究任务...*",
                )
                evidence_output = gr.Markdown(label="证据统计")

        # Wire up the research flow
        submit_btn.click(
            fn=run_research,
            inputs=[query_input, domain_select, depth_slider],
            outputs=[status_output, report_output, evidence_output],
        )

        # Example queries
        gr.Examples(
            examples=[
                ["如何利用 Landsat 和 Sentinel-2 数据进行城市热岛效应分析？", "geo_rs", 2],
                ["对比 Sentinel-2 和 Landsat 8 在城市绿地监测中的适用性", "geo_rs", 2],
                ["面向山区城镇的应急疏散的 GIS 交通方向有哪些？", "geo_rs", 2],
            ],
            inputs=[query_input, domain_select, depth_slider],
        )

    return demo


if __name__ == "__main__":
    import os
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "false"

    demo = build_ui()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7870,
        share=False,
        theme=gr.themes.Soft(),
        prevent_thread_lock=True,
    )
    # Keep the process alive
    import time
    while True:
        time.sleep(1)
