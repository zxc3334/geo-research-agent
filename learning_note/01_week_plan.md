# 一周学习计划

## Day 1：建立全局地图

目标：先知道项目怎么启动、每个目录负责什么、一次研究任务经过哪些模块。

阅读文件：

- `README.md`
- `pyproject.toml`
- `configs/default.yaml`
- `scripts/run_single.py`
- `src/core/runner.py`
- `src/orchestrator/schemas.py`

Python 重点：

- 项目包结构与 import
- `dict` 配置对象
- `dataclass`
- `Enum`
- 类型标注：`str | None`、`dict[str, Any]`

产出：

- 画出“用户 query 到 Markdown 报告”的流程图。
- 用 3 分钟讲清 `runner.py` 做了什么。

## Day 2：Planner 与 DAG

目标：理解研究问题如何被拆成结构化子任务。

阅读文件：

- `src/planner/planner.py`
- `src/planner/dag.py`
- `src/planner/budget_tracker.py`
- `src/orchestrator/schemas.py`

Python 重点：

- JSON 解析：`json.loads`
- 正则：`re.search`、`re.sub`
- 自定义异常
- 图结构：节点、边、拓扑排序

产出：

- 手写一个 3 个任务的 JSON DAG。
- 讲清 `generate_plan()` 和 `replan()` 的区别。

## Day 3：Orchestrator 状态机与并发调度

目标：理解这个项目最核心的自研调度器。

阅读文件：

- `src/orchestrator/orchestrator.py`
- `src/orchestrator/agent_pool.py`
- `src/orchestrator/schemas.py`

Python 重点：

- `async def`
- `await`
- `asyncio.gather`
- `asyncio.Semaphore`
- `asyncio.wait_for`
- 状态机设计

产出：

- 画出 Orchestrator 状态机。
- 讲清为什么 DAG 分层可以并发执行。

## Day 4：ResearcherAgent 与工具调用

目标：理解子 Agent 如何通过 tool-calling 完成搜索、浏览、论文检索和分析。

阅读文件：

- `src/agents/base_agent.py`
- `src/agents/researcher.py`
- `src/tools/web_search.py`
- `src/tools/arxiv_reader.py`
- `src/tools/browser.py`
- `src/tools/calculator.py`

Python 重点：

- 面向对象：继承、实例属性、方法
- 抽象基类：`ABC`、`abstractmethod`
- 动态分发：`tool_map[tool_name]`
- 错误包装与失败降级

产出：

- 讲清一次 tool-calling loop 的消息结构。
- 设计一个 `stac_search` 工具的输入输出 schema。

## Day 5：报告合成、引用与记忆

目标：理解项目如何从多个子任务结果生成报告，以及当前引用机制的不足。

阅读文件：

- `src/agents/summarizer.py`
- `src/memory/memory_store.py`
- `src/memory/long_term.py`
- `src/memory/embedder.py`
- `src/compressor/compressor.py`

Python 重点：

- 排序：`sorted(..., key=...)`
- SQLite 持久化
- embedding 向量与 cosine similarity
- 线程锁：`threading.RLock`
- 启发式去重和冲突检测

产出：

- 讲清当前 sources 是如何从 trajectory 中提取的。
- 写出你认为 GeoResearch Agent 应该新增的 evidence 数据结构。

## Day 6：对抗修正、评测与工程包装

目标：理解 Red-Blue loop 和 evaluation，这些是项目简历亮点。

阅读文件：

- `src/adversarial/loop.py`
- `src/adversarial/red_agent.py`
- `src/adversarial/blue_agent.py`
- `src/adversarial/verdict.py`
- `evaluation/metrics.py`
- `evaluation/benchmarks/research_bench.py`
- `evaluation/run_baseline.py`

Python 重点：

- 多对象协作
- 评分结构
- 统计指标
- pipeline 脚本组织

产出：

- 讲清 RedAgent、BlueAgent、Judge 的关系。
- 说明对抗修正和 citation verification 的区别。

## Day 7：面试复盘与 GeoResearch 改造设计

目标：把项目理解转化为面试表达和后续开发路线。

阅读文件：

- `learning_note/02_project_architecture.md`
- `learning_note/03_deepresearch_architectures.md`
- `learning_note/06_interview_story_and_georesearch_plan.md`

Python 重点：

- 回顾本周所有 demo。
- 找 2 个模块做小改动，例如加日志、加一个 mock tool、加一个配置项。

产出：

- 一段 2 分钟项目介绍。
- 一段 5 分钟架构深挖。
- GeoResearch Agent v1 技术方案。

