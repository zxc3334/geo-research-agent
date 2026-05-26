#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
src/core/runner.py
================================================================================
DeepResearch Agent 核心运行逻辑。

本模块包含初始化所有模块和执行完整研究流程的核心函数，
供 scripts/ 和 evaluation/ 统一调用，避免 evaluation/ 反向依赖 scripts/。

对外接口:
    - load_config(config_path) -> dict
    - initialize_modules(config) -> dict
    - run_research(query, config, modules) -> str
    - save_report(report, query, output_dir) -> str
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# 将项目根目录加入 sys.path，确保 src 包可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
def setup_logging(log_level: str = "INFO") -> None:
    """配置全局日志格式与级别。"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def load_config(config_path: str | None = None) -> dict:
    """
    加载 YAML 配置文件。

    若未指定路径，默认加载 configs/default.yaml。
    """
    if config_path is None:
        config_path = os.path.join(PROJECT_ROOT, "configs", "default.yaml")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件未找到: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


# ---------------------------------------------------------------------------
# 工具工厂
# ---------------------------------------------------------------------------
def _create_tools_factory(config: dict):
    """创建工具工厂函数，返回 Agent 可用的工具列表。"""
    tools_cfg = config.get("tools", {})
    mock_mode = tools_cfg.get("web_search", {}).get("mock_mode", True)

    from src.tools import (
        WebSearchTool,
        MockWebSearchTool,
        OfficialSourceSearchTool,
        ArxivReaderTool,
        BrowserTool,
        MockBrowserTool,
        FileReaderTool,
        CodeSandboxTool,
        CalculatorTool,
        NotepadTool,
        DatasetRegistryTool,
        MethodRegistryTool,
        GeoPlanValidatorTool,
    )

    tools = {}

    # 1. web_search
    if mock_mode:
        tools["web_search"] = MockWebSearchTool()
    else:
        tools["web_search"] = WebSearchTool()
        tools["official_source_search"] = OfficialSourceSearchTool()

    # 2. browser
    if mock_mode:
        tools["browser"] = MockBrowserTool()
    else:
        tools["browser"] = BrowserTool()

    # 3. arxiv_reader
    tools["arxiv_reader"] = ArxivReaderTool(use_mock=mock_mode)

    # 4. file_reader（不限制目录）
    tools["file_reader"] = FileReaderTool(allowed_base_dir=None)

    # 5. code_sandbox
    tools["code_sandbox"] = CodeSandboxTool(use_mock=mock_mode)

    # 6. calculator
    tools["calculator"] = CalculatorTool()

    # 7. notepad
    tools["notepad"] = NotepadTool()

    # 8. GIS / remote-sensing structured registry tools
    geo_tools_cfg = tools_cfg.get("geo_registry", {})
    if geo_tools_cfg.get("enabled", False):
        tools["dataset_registry"] = DatasetRegistryTool()
        tools["method_registry"] = MethodRegistryTool()
        tools["geo_plan_validator"] = GeoPlanValidatorTool()

    # 返回列表形式（AgentPool 和 Agent 构造函数需要 list）
    return list(tools.values())


# ---------------------------------------------------------------------------
# 模块初始化
# ---------------------------------------------------------------------------
def initialize_modules(config: dict, session_id: str = "") -> dict[str, Any]:
    """
    根据配置初始化所有核心模块。

    Args:
        config: 全局配置字典。
        session_id: 会话 ID，用于 memory store 的 session 隔离。

    返回一个包含各模块实例的字典。
    """
    logger = logging.getLogger("runner")
    logger.info("正在初始化核心模块...")

    modules: dict[str, Any] = {}
    trace_path = config.get("_trace_path")
    trace_recorder = None
    if trace_path:
        from src.observability import TraceRecorder
        trace_recorder = TraceRecorder(trace_path, run_id=session_id or None)
        trace_recorder.record("modules_init_start", session_id=session_id)
    modules["trace_recorder"] = trace_recorder

    # ------------------------------------------------------------------
    # 多后端 LLM 初始化（从 .env + configs/default.yaml 读取配置）
    # ------------------------------------------------------------------
    from src.models.model_factory import LLMModelFactory

    model_cfg = config.get("model", {})
    model_factory = LLMModelFactory(model_cfg)
    modules["model_factory"] = model_factory
    backend_mapping = model_factory.module_profiles

    def _create_policy(module_name: str, use_cache: bool = True):
        """Create a policy for one module using provider/profile routing."""
        return model_factory.create_policy(module_name, use_cache=use_cache)

    # 默认后端（所有模块共用）
    default_kwargs = model_factory.describe_module("default")
    default_policy = model_factory.create_policy("default")
    modules["default_policy"] = default_policy
    logger.info(f"[LLM] default resolved: {default_kwargs}")

    # Module-level model routing: planner/solver/summarizer may use different profiles.
    known_policy_modules = {
        "planner",
        "solver",
        "summarizer",
        "compressor",
        "red_agent",
        "blue_agent",
        "judge",
    }
    for module_name in sorted(known_policy_modules | set(backend_mapping)):
        kwargs = model_factory.describe_module(module_name)
        modules[f"{module_name}_policy"] = model_factory.create_policy(module_name)
        logger.info(f"[LLM] {module_name} resolved: {kwargs}")

    # 若未配置分工，所有模块回退到 default_policy
    # ------------------------------------------------------------------

    # M2: Adaptive Planner（Orchestrator 依赖 Planner，先初始化）
    from src.planner.planner import Planner
    from src.planner.budget_tracker import BudgetTracker

    planner_policy = modules.get("planner_policy", default_policy)
    budget_tracker = BudgetTracker()
    planner_cfg = config.get("planner", {})
    planner = Planner(
        policy=planner_policy,
        budget_tracker=budget_tracker,
        domain=planner_cfg.get("domain", "general"),
    )
    modules["planner"] = planner
    logger.info("[M2] Planner 模块已初始化")

    # M3: Context Compressor
    from src.compressor.compressor import ContextCompressor

    compressor_policy = modules.get("compressor_policy", default_policy)
    compressor_cfg = config.get("compressor", {})
    compressor = ContextCompressor(
        llm_policy=compressor_policy,
        budget=compressor_cfg.get("max_context_length", 16000),
        output_reserve=compressor_cfg.get("output_reserve_tokens", 2048),
    )
    modules["compressor"] = compressor
    logger.info("[M3] Compressor 模块已初始化")

    # M4: Shared Memory Store
    from src.memory.memory_store import SharedMemoryStore

    memory_cfg = config.get("memory", {})
    memory_store = SharedMemoryStore(
        db_path=memory_cfg.get("db_path", "data/memory.db"),
        session_id=session_id,
    )
    modules["memory_store"] = memory_store
    logger.info(f"[M4] Memory Store 模块已初始化 (session={session_id})")

    # Tools（真实工具或 Mock 工具）
    tools_list = _create_tools_factory(config)
    modules["tools"] = tools_list
    logger.info(f"Tools 模块已初始化（共 {len(tools_list)} 个工具）")

    # M5: Red-Blue Adversarial Loop（先创建，再注入 Orchestrator）
    from src.adversarial.loop import AdversarialLoop
    from src.adversarial.red_agent import RedAgent
    from src.adversarial.blue_agent import BlueAgent

    red_policy = modules.get("red_agent_policy", default_policy)
    blue_policy = modules.get("blue_agent_policy", default_policy)
    adversarial_cfg = config.get("adversarial", {})

    red_agent = RedAgent(policy=red_policy)
    blue_agent = BlueAgent(policy=blue_policy, tools=tools_list)
    adversarial_loop = AdversarialLoop(
        red_agent=red_agent,
        blue_agent=blue_agent,
        policy=modules.get("judge_policy", default_policy),
        max_rounds=adversarial_cfg.get("max_rounds", 3),
        score_threshold=adversarial_cfg.get("score_threshold", 8.0),
        delta_threshold=adversarial_cfg.get("delta_threshold", 0.3),
    )
    modules["adversarial"] = adversarial_loop
    logger.info("[M5] Adversarial 模块已初始化")

    # M1: Multi-Agent Orchestrator
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.agent_pool import AgentPool

    agent_pool = AgentPool(
        policy_factory=lambda: _create_policy("solver", use_cache=False),
        tools_factory=lambda: list(modules["tools"]),
        max_idle=3,
        policy_factory_by_type={
            "synthesis": lambda: _create_policy("summarizer", use_cache=False),
        },
        agent_config=config.get("agents", {}),
        trace_recorder=trace_recorder,
    )
    modules["agent_pool"] = agent_pool

    orchestrator = Orchestrator(
        planner=planner,
        agent_pool=agent_pool,
        budget_tracker=budget_tracker,
        compressor=compressor,
        adversarial_loop=adversarial_loop,
        memory_store=memory_store,
        summarizer_policy=modules.get("summarizer_policy", default_policy),
        trace_recorder=trace_recorder,
    )
    modules["orchestrator"] = orchestrator
    logger.info("[M1] Orchestrator 模块已初始化")

    # M6: Self-Evolution Engine（预留，默认禁用）
    if config.get("evolution", {}).get("enabled", False):
        logger.info("[M6] Evolution 模块已启用（预留接口）")
    else:
        logger.info("[M6] Evolution 模块已禁用")
    if trace_recorder:
        trace_recorder.record(
            "modules_init_end",
            tools=[getattr(tool, "name", type(tool).__name__) for tool in tools_list],
        )

    return modules


# ---------------------------------------------------------------------------
# 研究流程主函数
# ---------------------------------------------------------------------------
async def run_research(query: str, config: dict, modules: dict[str, Any]) -> str:
    """
    执行完整的研究流程。

    流程：
        1. Orchestrator 调用 Planner 拆解问题为子任务 DAG
        2. Orchestrator 调度 AgentPool 中的子 Agent 并行/串行执行
        3. 子 Agent 调用 Tools 检索信息并生成子报告
        4. Compressor 管理长上下文
        5. Memory 存储中间结果
        6. Adversarial Loop 对报告进行多轮对抗优化（若启用）
        7. 输出最终研究报告

    Args:
        query: 用户输入的研究问题。
        config: 全局配置字典。
        modules: 已初始化的模块实例字典。

    Returns:
        最终研究报告文本（Markdown 格式）。
    """
    import asyncio

    logger = logging.getLogger("runner")
    logger.info(f"开始研究，查询: {query[:80]}...")
    trace_recorder = modules.get("trace_recorder")
    if trace_recorder:
        trace_recorder.record("research_start", query=query)

    start_time = time.time()

    # Step 1-3: Orchestrator 内部完成规划、调度、收集、合成
    orchestrator = modules["orchestrator"]
    from src.orchestrator.schemas import RunConfig

    run_cfg = RunConfig(
        max_concurrent=config.get("orchestrator", {}).get("max_concurrent", 5),
        global_timeout_seconds=config.get("orchestrator", {}).get("global_timeout_seconds", 600),
        max_replan_rounds=config.get("orchestrator", {}).get("max_replan_rounds", 3),
        max_sub_questions=config.get("orchestrator", {}).get("max_sub_questions", 8),
        enable_adversarial=config.get("adversarial", {}).get("enabled", True),
        enable_evolution=config.get("evolution", {}).get("enabled", False),
    )

    report = await orchestrator.run(query, config=run_cfg)
    if trace_recorder:
        trace_recorder.record(
            "research_report_ready",
            confidence=report.confidence,
            num_searches=report.num_searches,
            evidence_counts=getattr(report, "evidence_summary", {}).get("counts", {}),
        )
    logger.info(
        f"[Orchestrator] 报告生成完成 | 置信度={report.confidence:.2f} | "
        f"搜索轮数={report.num_searches} | 重规划={report.num_replan} | 对抗轮数={report.adversarial_rounds}"
    )

    # Step 4/5: 进化优化（如启用且已训练）
    if run_cfg.enable_evolution:
        logger.info("[Evolution] 进化优化已启用（预留接口）")
    else:
        logger.info("[Evolution] 进化优化已跳过")

    # 关闭 WebSearchTool 连接池
    from src.tools.web_search import WebSearchTool
    await WebSearchTool.close_session()

    elapsed = time.time() - start_time
    logger.info(f"研究完成，耗时: {elapsed:.2f} 秒")
    if trace_recorder:
        trace_recorder.record("research_end", elapsed_seconds=round(elapsed, 3))

    # 组装最终输出
    final_report = _format_report(report, elapsed)
    return final_report


def _format_report(report, elapsed: float) -> str:
    """将 ResearchReport 格式化为 Markdown 文本。"""
    content = report.content or ""

    # 统一置信度：如果正文中有 LLM 自评的"整体置信度"，替换为实际计算值，避免不一致
    content = re.sub(
        r"(整体置信度|Overall Confidence|置信度)[:：]\s*0?\.\d+",
        f"\\1: {report.confidence:.2f}",
        content,
        flags=re.I,
    )

    lines = [
        f"# 研究报告：{report.query}",
        "",
        "---",
        "",
        content,
        "",
        "---",
        "",
        "## 元信息",
        "",
        f"- **置信度**: {report.confidence:.2f}",
        f"- **搜索轮数**: {report.num_searches}",
        f"- **重规划次数**: {report.num_replan}",
        f"- **对抗轮数**: {report.adversarial_rounds}",
        f"- **总耗时**: {elapsed:.2f} 秒",
        "",
    ]

    evidence_counts = getattr(report, "evidence_summary", {}).get("counts", {})
    if evidence_counts:
        lines.append("## 证据分级统计")
        lines.append("")
        for key in ("verified", "evidence_backed", "speculative", "rejected"):
            lines.append(f"- **{key}**: {evidence_counts.get(key, 0)}")
        lines.append("")

    tool_trace = getattr(report, "tool_trace", [])
    if tool_trace:
        lines.append("## 工具调用摘要")
        lines.append("")
        for i, item in enumerate(tool_trace[:20], 1):
            urls = item.get("urls", []) or []
            url_text = ", ".join(urls[:3]) if urls else "无 URL"
            lines.append(
                f"{i}. `{item.get('task_id', '')}` -> `{item.get('tool', '')}` "
                f"(URLs: {item.get('url_count', 0)}) — {url_text}"
            )
        lines.append("")

    if report.sources:
        lines.append("## 参考来源")
        lines.append("")
        for i, src in enumerate(report.sources, 1):
            title = src.get("title", "未知标题")
            url = src.get("url", "")
            snippet = src.get("snippet", "")
            lines.append(f"{i}. [{title}]({url}) — {snippet}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 报告保存
# ---------------------------------------------------------------------------
def save_report(report: str, query: str, output_dir: str = "outputs/reports") -> str:
    """
    将研究报告保存到文件。

    文件名格式：report_YYYYMMDD_HHMMSS_<query前20字>.md
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = "".join(c if c.isalnum() or c in "_-" else "_" for c in query[:20])
    filename = f"report_{timestamp}_{safe_query}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)

    return filepath
