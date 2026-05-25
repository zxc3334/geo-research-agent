# 99. DeepResearch Agent 面试问题与回答

这份文档用于把源码学习转换成面试表达。注意：如果这些是你学习和复现的开源项目，不要说“我从零原创完整实现”。更稳的说法是：

> 我对这个 DeepResearch Agent 开源项目做了源码级拆解、运行链路复现和面试化整理，重点学习了自研 Planner/Orchestrator、异步 Agent 调度、tool calling、Memory、Red-Blue 对抗修正和评测体系。

## 1. 60 秒项目介绍

这是一个 Python 实现的 DeepResearch Agent 系统，面向复杂研究型 query。它不是单轮问答，而是先由 Planner 把问题拆成 JSON DAG 子任务，然后 Orchestrator 根据 DAG 依赖关系做分层并发调度。每个子任务由 ResearcherAgent 执行，它通过 OpenAI-compatible tool calling 调用网页搜索、浏览器、论文检索、文件读取、计算器等工具。执行结果写入 SharedMemoryStore，最后由 SummarizerAgent 合成为 Markdown 报告。如果报告置信度不足，还会进入 Red-Blue Adversarial Loop，让 Red Agent 找问题、Blue Agent 修复。模型后端通过 ModelRouter 支持 DeepSeek、MiMo、OpenAI 和本地 vLLM。

## 2. 3 分钟项目介绍

这个项目可以分成五层。

第一层是工程入口和配置。`scripts/run_single.py` 负责解析 query，`runner.py` 读取 YAML 配置，初始化模型、工具、Planner、Memory、Adversarial 和 Orchestrator。敏感连接信息放 `.env.local`，模块参数放 `configs/default.yaml`。

第二层是规划。`Planner` 通过 LLM 生成 JSON 格式的 `sub_tasks`，每个任务包含 `task_id`、`task_type`、`description`、`dependencies` 等字段，然后解析成 DAG。

第三层是调度。`Orchestrator` 是状态机，正常流程是 `IDLE -> PLANNING -> DISPATCHING -> COLLECTING -> SYNTHESIZING -> ADVERSARIAL -> DONE`。调度阶段用 `DAG.get_parallel_groups()` 找可并发层，用 `asyncio.Semaphore` 限制最大并发，用 `asyncio.gather` 执行同层任务，用 `asyncio.wait_for` 控制单任务超时。

第四层是执行。`ResearcherAgent` 实现多轮 tool-calling loop，把工具 schema 注册给 LLM，解析 LLM 返回的 tool calls，调用工具的 `execute()`，再把结果追加回上下文。同步 LLM 调用通过 `asyncio.to_thread()` 放到线程池，避免阻塞事件循环。

第五层是质量增强和评测。`SharedMemoryStore` 用 SQLite + embedding 存储记忆，支持去重、冲突检测和语义检索；`SummarizerAgent` 合成最终报告；`AdversarialLoop` 用 Red-Blue 流程做自动审查和修复；`evaluation/` 提供规则指标、Judge 和消融实验。

## 3. 架构问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | 这个项目和普通 RAG 问答有什么区别？ | 普通 RAG 通常是检索文档后直接回答；这个项目先规划成 DAG 子任务，再并发执行工具调用，最后合成报告，并有 Memory、Adversarial、Evaluation 等质量控制模块。 | `src/planner/planner.py`, `src/orchestrator/orchestrator.py`, `src/agents/summarizer.py` |
| Easy | 为什么要有 Runner？ | Runner 负责工程装配，把 CLI、配置、模型、工具和核心模块解耦。CLI 不直接初始化所有类，这样 evaluation 和 scripts 都可以复用同一套 `runner.py`。 | `src/core/runner.py` |
| Medium | 为什么 Planner 输出 DAG？ | 因为深度研究任务的子问题有依赖关系，也有并行机会。DAG 能表达“谁依赖谁”，Orchestrator 可以按拓扑层并发执行，提高效率并保证依赖顺序。 | `src/planner/dag.py`, `src/orchestrator/orchestrator.py` |
| Medium | 为什么不用 LangGraph/AutoGen？ | 项目选择自研调度是为了完全控制状态机、并发、超时、replan 和降级策略。缺点是要自己处理边界情况，优点是面试中能讲清底层调度逻辑。 | `src/orchestrator/orchestrator.py` |
| Hard | 这个系统的最大工程风险是什么？ | 引用 grounding 不够严格。当前 sources 主要从 tool trajectory 中启发式提取，并没有把报告中的每个 claim 和证据强绑定。后续可改成 evidence store：每条 claim 记录 source、quote、retrieval score，并在 Summarizer 强制引用。 | `src/agents/summarizer.py`, `src/memory/memory_store.py` |

## 4. Python 工程与配置问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | `pyproject.toml` 和 `requirements.txt` 有什么区别？ | `pyproject.toml` 定义包元信息、Python 版本、可选依赖和命令行入口；`requirements.txt` 更像直接安装清单，注释详细说明每个依赖用途。 | `pyproject.toml`, `requirements.txt` |
| Easy | `.env` 和 `configs/default.yaml` 分别放什么？ | `.env` 放 API Key、Base URL、模型名等连接信息；YAML 放行为参数，如模型分工、temperature、并发数、Memory 阈值、工具开关。 | `.env.template`, `configs/default.yaml`, `src/utils/env_config.py` |
| Medium | `.env.local` 为什么优先级更高？ | `env_config.py` 先加载 `.env`，再用 `override=True` 加载 `.env.local`。这样团队默认配置和个人本地密钥可以分离，本地配置不会提交 Git。 | `src/utils/env_config.py` |
| Medium | 如何切换搜索后端？ | 改 `.env.local` 的 `SEARCH_BACKEND`，例如 `bocha`、`bing`、`serpapi`，并配置对应 Key。`WebSearchTool.execute()` 根据 backend 分发到不同实现。 | `.env.template`, `src/tools/web_search.py` |
| Hard | 如果没有 API Key，如何验证主流程？ | 把 `configs/default.yaml` 里的 `tools.web_search.mock_mode` 改成 `true`，Runner 会创建 `MockWebSearchTool` 和 `MockBrowserTool`，可以先验证 Agent/Orchestrator 主流程。 | `src/core/runner.py`, `src/tools/web_search.py`, `src/tools/browser.py` |

## 5. 异步与调度问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | `asyncio.run()` 在项目里做什么？ | CLI 的 `main()` 是同步函数，而 `run_research()` 是异步函数，所以用 `asyncio.run()` 创建事件循环并执行完整异步流程。 | `scripts/run_single.py`, `src/core/runner.py` |
| Medium | `Semaphore`、`gather`、`wait_for` 分别解决什么？ | `Semaphore` 控制最大并发；`gather` 并发等待同一 DAG 层任务；`wait_for` 给单个子任务设置超时。 | `src/orchestrator/orchestrator.py` |
| Medium | 为什么 `ResearcherAgent` 用 `asyncio.to_thread()` 调 LLM？ | `self.policy(messages)` 是同步阻塞调用。为了不阻塞 asyncio 事件循环，放到线程池里执行；工具调用仍然使用 `await tool.execute()`。 | `src/agents/researcher.py` |
| Hard | 如果一个子任务超时，系统会怎样？ | `_do_dispatching()` 捕获 `asyncio.TimeoutError`，包装成 `AgentResult(status=TIMEOUT)`。收集阶段统计失败率，如果超过阈值可能进入 REPLANNING，否则继续合成部分结果。 | `src/orchestrator/orchestrator.py` |

## 6. Agent 与工具调用问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | ResearcherAgent 做什么？ | 它执行单个 SubTask，通过 LLM 多轮 tool calling 调用搜索、浏览器、论文、计算等工具，最终返回 AgentResult。 | `src/agents/researcher.py` |
| Medium | 工具如何暴露给 LLM？ | 每个工具实现 `get_openai_tool_schema()`，ResearcherAgent 收集这些 schema 后调用 `policy.set_tools(schemas)`。LLM 返回 tool_calls，Agent 解析工具名和 arguments 后调用工具。 | `src/agents/researcher.py`, `src/tools/web_search.py` |
| Medium | web_search 和 browser 的区别？ | web_search 找候选网页和摘要；browser 打开具体 URL 提取正文。一个负责找，一个负责读。 | `src/tools/web_search.py`, `src/tools/browser.py` |
| Hard | 如何防止 Agent 无限调用工具？ | ResearcherAgent 有 `max_turns`，系统 prompt 也限制最多工具调用次数；代码里搜索达到一定次数或结果为空时，会提示模型必须总结。 | `src/agents/researcher.py` |

## 7. Memory、Compressor 与模型路由问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | ModelRouter 解决什么问题？ | 集中管理不同 LLM 后端，让 Planner、Solver、Judge、Red/Blue Agent 可以用不同模型和采样参数。 | `src/models/model_router.py`, `configs/default.yaml` |
| Medium | Memory 为什么用 SQLite + embedding？ | SQLite 零运维，适合本地持久化；embedding 支持语义相似度检索，让 Planner 可以读取相关历史上下文。 | `src/memory/long_term.py`, `src/memory/memory_store.py` |
| Medium | Memory 写入时做了哪些质量控制？ | 先过滤低质量内容；如果没有 embedding 自动生成；cosine > 0.92 视为重复；相关但语义对立会记录 conflict。 | `src/memory/memory_store.py` |
| Hard | VLLMPolicy 如何处理上下文超长？ | `_truncate_messages()` 主动截断，保留 system 和最近交互；如果 API 报 context length，则抛 `RuntimeError`，上层把任务标记失败。 | `src/models/vllm_policy.py` |

## 8. Summarizer、Adversarial 与评测问题

| 难度 | 问题 | 强回答 | 文件 |
|---|---|---|---|
| Easy | SummarizerAgent 和 ResearcherAgent 有什么区别？ | ResearcherAgent 是多轮工具调用执行单个子任务；SummarizerAgent 是单轮长上下文生成，把多个子任务结果合成最终报告。 | `src/agents/researcher.py`, `src/agents/summarizer.py` |
| Medium | 为什么 Summarizer 要临时禁用 tools？ | 合成阶段不希望模型再调用工具，而是基于已有结果生成报告，所以保存旧 tools，设置 `policy.tools=None`，调用后恢复。 | `src/agents/summarizer.py` |
| Medium | Red-Blue Loop 什么时候触发？ | Orchestrator 合成报告后，如果 `enable_adversarial` 开启，并且报告置信度低于 0.8，就调用 AdversarialLoop。 | `src/orchestrator/orchestrator.py` |
| Hard | Red-Blue 如何避免无限修复？ | 有最大轮数、score threshold、delta convergence 和 oscillation detection。已修复 issue 如果重新出现，会判定震荡并停止。 | `src/adversarial/loop.py` |
| Hard | 评测体系有什么价值？ | 规则指标便宜可复现，适合批量和消融；Judge 更灵活，适合评价复杂文本质量。项目同时提供 rule-based metrics、ResearchBench、HotpotQA 和统计显著性工具。 | `evaluation/metrics/rule_based.py`, `evaluation/metrics/stats.py`, `evaluation/benchmarks/research_bench.py` |

## 9. 项目缺陷与改进计划

| 缺陷 | 为什么重要 | 改进方案 |
|---|---|---|
| 引用 grounding 不严格 | 面试官可能质疑报告真实性 | 建 evidence store，把每个 claim 绑定 source/quote/retrieval score |
| 部分阈值在配置和代码中重复 | 配置漂移，调参不统一 | 把 Memory dedup/conflict 阈值全部从 YAML 注入 |
| Planner 依赖 LLM JSON 输出 | JSON 不稳定会导致规划失败 | 加 schema validation、repair prompt、最小 fallback plan |
| Tool calling 轮数控制偏硬 | 复杂任务可能需要更多工具，简单任务又浪费 | 按任务类型动态设置 max_turns 和工具预算 |
| Evolution 模块偏预留 | 简历上讲太多容易被追问训练细节 | 主讲 Orchestrator/Tools/Memory/Adversarial，把 Evolution 作为扩展 |

## 10. 你的学习优先级

月底前投简历，建议这样准备：

1. 必须讲熟：`run_single.py -> runner.py -> orchestrator.run()` 主链路。
2. 必须讲熟：`Planner` 生成 DAG，`Orchestrator` 用 `Semaphore/gather/wait_for` 并发。
3. 必须讲熟：`ResearcherAgent` 的 tool-calling loop。
4. 必须讲熟：`.env` + YAML + `ModelRouter` 的配置分层。
5. 加分掌握：Memory 的 SQLite + embedding。
6. 加分掌握：Red-Blue 对抗和评测。
7. 谨慎讲：Evolution/GRPO，除非你后面真的细读训练代码。

## 11. 最推荐的简历表述

项目标题：

> DeepResearch 多智能体研究报告系统｜源码复现与架构拆解

项目 bullet：

- 源码级拆解 DeepResearch Agent 主链路，梳理 `run_single.py -> runner.py -> Orchestrator` 执行路径，理解 YAML/.env 配置、模型路由和模块装配机制。
- 分析自研 Planner + DAG Orchestrator：Planner 将复杂 query 拆解为 JSON DAG，Orchestrator 基于状态机、`asyncio.Semaphore`、`gather`、`wait_for` 实现分层并发调度和失败重规划。
- 深入理解 ResearcherAgent tool-calling loop：将 web_search、browser、arxiv_reader、calculator 等工具封装为 OpenAI function schema，支持多轮工具调用、结果回填和置信度输出。
- 梳理 Memory、Summarizer、Red-Blue Adversarial 与 Evaluation 模块，理解 SQLite + embedding 记忆、报告合成、自动审查修复和规则/Judge 评测体系。

注意：如果你没有做代码改造，简历标题里保留“源码复现与架构拆解”更诚实，也更抗问。

