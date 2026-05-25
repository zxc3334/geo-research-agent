# 面试讲法与 GeoResearch Agent 改造路线

## 1. 30 秒项目介绍

> 我正在做一个面向 GIS、遥感与时空智能研究的 GeoResearch Agent。它基于一个通用 DeepResearch Agent 改造，核心能力是把复杂研究问题拆成 DAG 子任务，自动检索论文、网页和领域数据源，维护证据与引用，最后生成可追溯的研究报告。底层包含模型路由、异步调度、工具调用、共享记忆、报告合成和质量审查等模块。

## 2. 2 分钟架构介绍

> 这个项目的入口是 CLI，runner 负责加载 YAML 配置并初始化模块。模型侧有 ModelRouter，可以给 planner、researcher、summarizer、judge 等角色分配不同 LLM 后端。用户 query 进入 Orchestrator 后，Planner 会先生成 JSON DAG，每个节点是一个 SubTask，包含任务类型、依赖、搜索提示和超时设置。Orchestrator 根据 DAG 拓扑层级并发调度子任务，通过 AgentPool 获取 ResearcherAgent。ResearcherAgent 使用 tool-calling 调用 web search、browser、arxiv reader 等工具，形成 AgentResult。成功结果写入 SharedMemoryStore，失败率过高时触发 replan。最后 SummarizerAgent 汇总所有结果生成 Markdown 报告，如果置信度不足，会进入 Red-Blue adversarial loop 做审查和修复。

## 3. 可以突出哪些后端能力

- **异步并发调度**：`asyncio.gather` + `Semaphore` + `wait_for`。
- **状态机设计**：Orchestrator 用明确状态控制复杂长流程。
- **DAG 任务执行**：不是线性 chain，而是支持依赖和并行。
- **工具抽象**：工具统一暴露 schema 和 `execute()`。
- **模型路由**：不同模块使用不同 LLM 后端和采样参数。
- **共享记忆**：SQLite + embedding index，支持相似召回和去重。
- **质量闭环**：Summarizer + AdversarialLoop + Evaluation。

## 4. 当前项目的不足要主动讲

成熟的面试表达不是只夸项目，也要讲清你看到了什么问题：

- 目前 Planner 是通用研究 Planner，没有 GIS/遥感领域先验。
- 当前 sources 是从 trajectory 中启发式提取，不是真正 claim-level citation。
- 工具层缺少遥感数据源和空间数据目录。
- Memory 存的是中间文本结果，不是严格的 evidence graph。
- 工具调用预算偏保守，深度论文综述可能不够。

可以接着说：

> 所以我的改造重点不是换个名字，而是把它从 generic research agent 变成 domain research agent：补领域 Planner、领域 tools、Evidence Registry 和 Citation Verification。

## 5. GeoResearch Agent v1 改造路线

### Phase 1：领域 Planner

目标：

让 Planner 面向 GIS、遥感与时空智能问题拆任务。

新增任务维度：

- 研究背景与问题定义
- 相关论文与方法谱系
- 数据集、传感器、空间分辨率、时间分辨率
- 模型结构与算法路线
- 实验设置与评价指标
- SOTA 对比
- 应用场景
- 局限与未来方向

可能改动：

- 新增 `configs/planner/geo_planner.yaml`
- 修改或扩展 `INITIAL_PLAN_PROMPT`
- 新增 `GeoPlanner` 或 planner prompt mode

### Phase 2：领域工具层

目标：

让 ResearcherAgent 能检索 GIS/遥感领域真正有用的数据源。

建议工具：

```text
src/tools/
  openalex_paper_search.py
  stac_search.py
  earthdata_search.py
  gee_catalog_search.py
  remote_sensing_dataset_reader.py
```

工具返回结构建议：

```python
{
    "source_id": "openalex:Wxxxx",
    "title": "...",
    "url": "...",
    "authors": [...],
    "year": 2025,
    "abstract": "...",
    "venue": "...",
    "citation_count": 123,
    "evidence_snippets": [
        {
            "text": "...",
            "section": "abstract",
            "supports": "method / dataset / metric"
        }
    ]
}
```

### Phase 3：Evidence Registry

目标：

不要等报告写完才抓引用，而是在研究阶段就登记证据。

建议数据结构：

```python
from dataclasses import dataclass, field

@dataclass
class Source:
    source_id: str
    title: str
    url: str
    source_type: str  # paper | web | dataset | documentation
    metadata: dict = field(default_factory=dict)

@dataclass
class Evidence:
    evidence_id: str
    source_id: str
    text: str
    claim_type: str  # fact | method | dataset | metric | comparison
    confidence: float
    locator: str = ""  # section/page/paragraph
```

这会让报告生成变成：

```text
SubTask -> Tool Result -> Source/Evidence -> Claim -> Report Citation
```

### Phase 4：Citation Verification

目标：

最终报告中的引用必须可回溯。

检查规则：

- 报告中每个 citation id 必须存在于 registry。
- 每个关键 claim 至少有一个 evidence 支持。
- citation 的 source URL 不能空。
- dataset claim 必须包含数据集名称、传感器或来源。
- paper claim 最好包含年份、作者或 venue。

失败处理：

- 删除 unsupported claim。
- 标记“证据不足”。
- 触发 verify subtask 重新检索。

### Phase 5：领域评测

目标：

把项目从 demo 变成可展示的后端工程。

评测指标：

- Citation validity：引用是否可访问、是否存在。
- Claim support rate：关键结论有证据支持的比例。
- Paper coverage：是否覆盖核心论文。
- Dataset metadata accuracy：数据集名称、传感器、空间/时间分辨率是否准确。
- Report structure score：是否包含背景、方法、数据、指标、对比、局限。
- Latency / cost：单次报告耗时和调用次数。

## 6. 和另一个 MCP server 项目的组合故事

你可以把两个项目串起来讲：

> 我的 MCP server 项目偏工具基础设施，解决 Agent 如何标准化访问外部能力的问题；GeoResearch Agent 偏上层智能编排，解决复杂研究问题如何规划、检索、验证和生成报告的问题。两者结合后，MCP server 可以给 GeoResearch Agent 暴露 STAC、Earthdata、论文检索、地图数据处理等工具，而 GeoResearch Agent 负责任务规划、证据管理和报告合成。

## 7. 面试常见追问准备

### 为什么不用 LangGraph？

可以答：

> LangGraph 很适合生产级 graph workflow，但这个项目的价值在于调度逻辑完全透明。我能直接控制 DAG 拓扑排序、并发层级、单任务超时、失败率重规划和降级合成。对学习和展示 agent 后端能力来说，自研 Orchestrator 更能体现我对调度、状态和工具调用的理解。后续如果要生产化，也可以把当前状态机迁移到 LangGraph。

### DAG 相比普通 chain 有什么好处？

可以答：

> 普通 chain 是线性的，但研究任务天然有并行和依赖关系。比如“检索方法论文”和“检索数据集”可以并行，而“方法对比分析”必须依赖前两个结果。DAG 可以表达这种结构，并允许同一层任务并发执行，提高效率。

### 对抗修正能解决幻觉吗？

可以答：

> 只能部分缓解，不能根治。Red-Blue loop 更适合发现逻辑漏洞、覆盖不足和明显 unsupported statements。真正降低幻觉需要 evidence registry 和 citation verification，让关键 claim 必须绑定可追溯证据。这也是我改造 GeoResearch Agent 的重点。

### 你会先改哪里？

可以答：

> 我会先做领域工具和 Evidence Registry。因为 Planner 再聪明，如果工具拿不到可靠领域证据，报告仍然不可信。第一版我会接 OpenAlex/Semantic Scholar 增强论文检索，再接 STAC 或 GEE catalog 做遥感数据集检索，然后让 Summarizer 基于 evidence id 写报告。

