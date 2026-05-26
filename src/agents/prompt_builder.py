"""Prompt builders for executable agents."""
from __future__ import annotations

from ..orchestrator.schemas import SubTask


class ResearchPromptBuilder:
    """Build system and user prompts for ResearcherAgent tasks."""

    def system_prompt(self) -> str:
        return (
            "You are a meticulous research assistant. "
            "Your job is to gather and analyze information using the RIGHT tool for each task. "
            "\n\nAVAILABLE TOOLS:\n"
            "- web_search: General web search for news, market data, industry reports, current events. "
            "  Use this as the FIRST tool for most tasks.\n"
            "- arxiv_reader: Academic paper search (ArXiv / Semantic Scholar). "
            "  USE when the task involves: papers, publications, academic research, citation counts.\n"
            "- browser: Open a URL and extract full webpage text. "
            "  USE after web_search when search results are too short and you need to read the original article in depth.\n"
            "- code_sandbox: Execute Python code for calculations, data processing, simulations. "
            "  USE when the task requires: computing FLOPs, memory usage, statistical analysis, data transformation.\n"
            "- calculator: Quick math evaluation (+, -, *, /, sqrt, log, mean, etc.). "
            "  USE for simple calculations instead of code_sandbox.\n"
            "- notepad: Write/read intermediate notes to avoid forgetting findings during multi-step research. "
            "  USE to record key numbers, conclusions, or next search queries.\n"
            "- file_reader: Read local files (.txt/.md/.pdf/.csv/.json/.docx). "
            "  USE only when the task explicitly references a local file path.\n"
            "- dataset_registry: Curated GIS/remote-sensing dataset facts. "
            "  USE for sensors, bands, spatial/temporal resolution, and dataset limitations.\n"
            "- method_registry: Curated GIS/remote-sensing method facts. "
            "  USE for formulas, required inputs, valid use cases, and limitations.\n"
            "- geo_plan_validator: Deterministic GIS/remote-sensing compatibility validator. "
            "  USE for checking whether a dataset-method workflow is valid.\n"
            "\nIMPORTANT RULES:\n"
            "1. You MUST use a tool to find factual information. Do NOT answer from your own knowledge.\n"
            "2. Choose the RIGHT tool based on the task type. You can use MULTIPLE tools in sequence.\n"
            "3. For most research tasks, START with web_search or arxiv_reader.\n"
            "4. If search results are too short, use browser to read the full article.\n"
            "5. If the task involves numbers/calculations, use calculator or code_sandbox.\n"
            "6. You may call tools AT MOST 2 times total. After that you MUST summarize.\n"
            "7. Only after gathering information, provide a concise summary with a confidence score (0-1).\n"
            "8. NEVER greet the user or ask what they want to search — just execute immediately.\n"
            "9. If you have already performed 2 tool calls, do NOT call more — write the final summary now."
        )

    def direct_analysis_system_prompt(self) -> str:
        return (
            "You are a thoughtful analyst. "
            "The user has asked a question that cannot be answered by web search "
            "(e.g., analyzing a specific private individual, personal advice, or subjective judgment). "
            "Your job is to provide a reasoned analysis based ONLY on the information already provided in the context. "
            "Do NOT make up facts. Clearly state what is known, what can be reasonably inferred, and what remains unknown. "
            "End with a confidence score (0-1)."
        )

    def task_prompt(self, task: SubTask, context: dict) -> str:
        """Build the user prompt for one SubTask."""
        desc_lower = (task.description or "").lower()

        tool_recommendations = []

        academic_keywords = ["论文", "paper", "publication", "学术", "arxiv", "neurips", "icml", "iclr", "scholar", "citation", "文献"]
        if any(kw in desc_lower for kw in academic_keywords):
            tool_recommendations.append("arxiv_reader")

        calc_keywords = ["计算", "flops", "显存", "内存", "参数量", "延迟", "成本", "公式", "数值", "统计", "数学", "公式", "推导"]
        if any(kw in desc_lower for kw in calc_keywords):
            tool_recommendations.append("calculator")
            tool_recommendations.append("code_sandbox")

        browser_keywords = ["详细", "原文", "全文", "深度", "详细内容", "网页内容", "文章正文"]
        if any(kw in desc_lower for kw in browser_keywords):
            tool_recommendations.append("browser")

        file_keywords = ["文件", "文档", "dataset", "数据集", "pdf", "csv", "json"]
        if any(kw in desc_lower for kw in file_keywords):
            tool_recommendations.append("file_reader")

        geo_dataset_keywords = [
            "landsat", "sentinel", "modis", "era5", "数据源", "数据集", "传感器",
            "波段", "分辨率", "lst", "ndvi", "ndbi", "地表温度",
        ]
        if any(kw in desc_lower for kw in geo_dataset_keywords):
            tool_recommendations.append("dataset_registry")

        geo_method_keywords = [
            "方法", "公式", "指数", "反演", "lst", "ndvi", "ndbi", "gwr",
            "地理加权回归", "单窗", "单通道", "split-window",
        ]
        if any(kw in desc_lower for kw in geo_method_keywords):
            tool_recommendations.append("method_registry")

        geo_validation_keywords = [
            "验证", "兼容", "检查", "风险", "限制", "crs", "云", "云掩膜",
            "空间分辨率", "时间一致性", "验证清单",
        ]
        if any(kw in desc_lower for kw in geo_validation_keywords):
            tool_recommendations.append("geo_plan_validator")

        if "geo_plan_validator" in tool_recommendations:
            tool_recommendations = ["geo_plan_validator"] + [t for t in tool_recommendations if t != "geo_plan_validator"]
        elif "dataset_registry" in tool_recommendations:
            tool_recommendations = ["dataset_registry"] + [t for t in tool_recommendations if t != "dataset_registry"]
        elif "method_registry" in tool_recommendations:
            tool_recommendations = ["method_registry"] + [t for t in tool_recommendations if t != "method_registry"]
        elif "arxiv_reader" in tool_recommendations:
            tool_recommendations = ["arxiv_reader"] + [t for t in tool_recommendations if t != "arxiv_reader"]
        elif not tool_recommendations:
            tool_recommendations.insert(0, "web_search")

        deduped = []
        for tool_name in tool_recommendations:
            if tool_name not in deduped:
                deduped.append(tool_name)
        tool_recommendations = deduped

        primary_tool = tool_recommendations[0]
        secondary_tools = tool_recommendations[1:]

        lines = [
            f"## Task: {task.description}",
            f"Type: {task.task_type.value}",
            f"Expected output: {task.expected_type}",
            "",
            f"## RECOMMENDED TOOLS (in priority order): {', '.join(tool_recommendations)}",
        ]

        if secondary_tools:
            lines.append(f"Start with '{primary_tool}'. If the task involves numbers/calculations, also use {', '.join(secondary_tools)}.")
        else:
            lines.append(f"Use '{primary_tool}' to gather information.")

        lines.extend([
            "",
            "## INSTRUCTIONS:",
            f"1. First, call the '{primary_tool}' tool with a relevant query to gather information.",
            "2. Review the results.",
            f"3. If needed, call '{primary_tool}' ONE MORE time with a refined query.",
            "   You may call tools AT MOST 2 times total. After the 2nd call, you MUST write the final summary.",
            "4. If search results are too short, you may use 'browser' to read the full article (counts as 1 tool call).",
            "5. If calculations are needed, use 'calculator' or 'code_sandbox' (counts as 1 tool call).",
            "6. Finally, summarize your findings in Chinese with a confidence score (0-1).",
            "7. DO NOT greet the user or ask clarifying questions — just execute immediately.",
            "8. IMPORTANT: Your query MUST directly address the task description.",
        ])
        if task.search_hints:
            lines.insert(1, f"Search hints (MUST use these as primary keywords): {', '.join(task.search_hints)}")
        if task.context_keys:
            ctx_parts = []
            for key in task.context_keys:
                if key in context:
                    ctx_parts.append(f"- {key}: {context[key]}")
            if ctx_parts:
                lines.append("\n## Context:")
                lines.extend(ctx_parts)
        return "\n".join(lines)

    def is_non_searchable(self, task: SubTask, context: dict) -> bool:
        """Heuristically detect tasks that cannot be answered by web search."""
        desc = (task.description or "").lower()
        query = context.get("query", "").lower()
        combined = desc + " " + query

        if "朋友" in combined or "同学" in combined or "同事" in combined:
            if any(w in combined for w in ["分析", "评价", "是什么样", "性格", "人品"]):
                return True

        if any(w in combined for w in ["建议我", "我该怎么", "适合我吗", "要不要"]):
            if "朋友" in combined or "我" in query:
                return True

        if "叫" in combined and any(w in combined for w in ["分析", "评价", "是什么样"]):
            return True

        return False
