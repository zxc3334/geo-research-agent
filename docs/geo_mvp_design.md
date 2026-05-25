# GeoResearch Agent M2 设计草案

## 目标

把当前通用 DeepResearch Agent 改造成面向 GIS / 遥感研究问题的 evidence-aware MVP。M2 阶段只做领域化设计和最小代码改造，不做 GEE 自动执行、深度学习训练、QGIS 插件或完整 STAC 生产化接入。

核心目标：

```text
用户 GIS/遥感研究问题
  -> 生成领域化 DAG
  -> 分别完成文献检索、数据发现、方法设计、可行性验证
  -> 输出带可信度分级的研究方案报告
```

## 当前源码基线

当前关键边界：

- `src/orchestrator/schemas.py`
  - `TaskType` 只有 `SEARCH / ANALYZE / VERIFY`
  - `SubTask` 是 Planner 到 Orchestrator / Agent 的核心任务结构
  - `ResearchReport` 是最终报告结构
- `src/planner/planner.py`
  - `INITIAL_PLAN_PROMPT` 要求 task_type 必须是 `search, analyze, verify`
  - `Planner._parse_plan()` 只验证 JSON、`sub_tasks`、DAG 无环
  - 不验证任务是否符合 GIS/遥感领域逻辑
- `src/orchestrator/orchestrator.py`
  - 状态机流程已经可用：planning -> dispatching -> collecting -> synthesizing
  - `RunConfig.max_sub_questions` 没有对 Planner 输出形成硬约束
  - `collecting` 阶段只按 `AgentStatus` 判断是否 replan
- `src/agents/researcher.py`
  - 已有 tool-calling loop
  - success 标准主要是是否正常输出，不校验方法/数据真实性
- `src/agents/summarizer.py`
  - 输出通用研究报告
  - citation 从 tool trajectory 里提取
  - 没有 Verified / Evidence-backed / Speculative 分级

## MVP 边界

MVP 只覆盖“遥感研究方案设计”，不覆盖完整影像计算。

包含：

- GIS/遥感问题拆解
- 遥感数据源候选推荐
- 遥感方法候选推荐
- 文献/证据检索入口保留
- 方法与数据适配性验证
- 最终报告按可信度分级输出

暂不包含：

- 大规模影像下载
- GEE / openEO 自动执行
- 本地深度学习模型训练
- QGIS 插件
- 复杂空间数据库 / PostGIS
- 完整 STAC 生产级检索

## TaskType 设计

当前：

```python
class TaskType(Enum):
    SEARCH = "search"
    ANALYZE = "analyze"
    VERIFY = "verify"
```

M2 目标：

```python
class TaskType(Enum):
    LITERATURE = "literature"
    DATA_DISCOVERY = "data_discovery"
    METHOD_DESIGN = "method_design"
    GEO_VALIDATION = "geo_validation"
    SYNTHESIS = "synthesis"  # reserved for Orchestrator-managed final synthesis
```

兼容策略：

- 第一轮代码改造保留旧类型，避免一次性破坏 AgentPool 和已有测试。
- 新类型先映射到现有 `ResearcherAgent`，只在 prompt 和工具层区分行为。
- `SYNTHESIS` 暂时作为保留类型，不让 Planner 放入 DAG；当前 orchestrator 仍负责最终合成。

建议中间态：

```python
class TaskType(Enum):
    SEARCH = "search"
    ANALYZE = "analyze"
    VERIFY = "verify"
    LITERATURE = "literature"
    DATA_DISCOVERY = "data_discovery"
    METHOD_DESIGN = "method_design"
    GEO_VALIDATION = "geo_validation"
    SYNTHESIS = "synthesis"
```

原因：

- 降低改造风险
- baseline 还能跑
- AgentPool 可以逐步扩展
- Planner prompt 可以先切到新类型

## Geo Planner 输出要求

GIS/遥感 Planner 不应只拆成普通搜索任务，而应输出覆盖研究设计链路的 DAG。

推荐结构：

```json
{
  "sub_tasks": [
    {
      "task_id": "task_1",
      "task_type": "data_discovery",
      "description": "识别适合该研究问题的数据源、传感器、时间范围和空间分辨率要求",
      "dependencies": [],
      "expected_type": "dataset_candidates",
      "search_hints": ["Landsat", "Sentinel-2", "LST", "NDVI"]
    },
    {
      "task_id": "task_2",
      "task_type": "method_design",
      "description": "设计遥感指标、分析方法和实验流程",
      "dependencies": ["task_1"],
      "expected_type": "method_pipeline"
    },
    {
      "task_id": "task_3",
      "task_type": "geo_validation",
      "description": "检查数据和方法是否匹配，包括波段、分辨率、时间范围、CRS 和验证风险",
      "dependencies": ["task_1", "task_2"],
      "expected_type": "validation_report"
    }
  ]
}
```

Planner prompt 必须加入约束：

- 必须显式识别 AOI、time_range、sensor/dataset、method、validation risk
- 数据发现任务必须先于方法验证任务
- 验证任务必须依赖数据发现和方法设计
- 不允许把未验证的数据源或方法写成确定事实
- 任务数控制在 4-6 个，适合 demo

## Evidence-aware 数据结构

M3 会正式实现。M2 先确定结构方向。

```python
@dataclass
class EvidenceItem:
    evidence_id: str
    claim: str
    source_type: str      # registry | rag | web | stac | llm
    source_name: str
    source_url: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
@dataclass
class ValidationFinding:
    code: str
    severity: str         # error | warning | info
    message: str
    related_evidence_ids: list[str] = field(default_factory=list)
```

```python
@dataclass
class GeoValidationResult:
    status: str           # verified | evidence_backed | speculative | rejected
    findings: list[ValidationFinding]
    evidence_ids: list[str]
    confidence: float
```

可信度语义：

- `verified`：本地 registry / 工具 / 数据目录明确验证
- `evidence_backed`：RAG 或文献检索找到证据，但还需要人工确认细节
- `speculative`：LLM 提出的探索性想法，证据不足
- `rejected`：与数据、波段、尺度、时间或方法规则冲突

## GIS/遥感工具最小集

M4 再实现，M2 先确定接口。

### dataset_registry_tool

职责：

- 从本地数据源知识库中查询适合任务的数据集
- 返回结构化 dataset candidates

第一批数据源：

- Landsat 5/7/8/9
- Sentinel-1 SAR
- Sentinel-2 MSI
- MODIS LST
- ESA WorldCover
- ERA5 / ERA5-Land

### method_registry_tool

职责：

- 查询遥感方法、公式、需要的波段、适用数据源和限制

第一批方法：

- NDVI
- NDBI
- NDWI / MNDWI
- LST retrieval
- urban heat island intensity
- change detection
- random forest classification

### geo_plan_validator_tool

职责：

- 检查候选数据和方法是否匹配
- 输出 `GeoValidationResult`

第一批规则：

- Sentinel-2 不能直接反演 LST，因为没有热红外波段
- Landsat LST 与 Sentinel-2 指数存在分辨率差异，需要说明重采样策略
- 时间序列研究必须说明季节一致性和云量过滤
- SAR 方法必须说明极化、轨道方向、事件前后影像要求
- 分类/回归研究必须说明验证数据或精度评估方式

## Orchestrator 集成策略

M2 不重写状态机。保留当前流程：

```text
PLANNING -> DISPATCHING -> COLLECTING -> SYNTHESIZING
```

短期改造：

- Planner 生成领域化 task types
- AgentPool 将新 task types 暂时映射到 `ResearcherAgent`
- Tools 增加 registry / validator
- Summarizer 输出 evidence-aware 报告

中期改造：

- `COLLECTING` 阶段不只判断 `AgentStatus`
- 若 `geo_validation` 输出 rejected，则触发 `REPLANNING`
- `SYNTHESIZING` 只允许将 verified/evidence_backed 写为确定结论

## Demo Query

第一条稳定 demo：

```text
如何研究 2018-2024 年武汉城市扩张对地表热环境的影响？
```

预期输出：

- AOI：武汉
- 时间：2018-2024
- 数据源：Landsat 8/9 L2、Sentinel-2 L2A、ESA WorldCover，可选 MODIS LST
- 方法：LST、NDVI、NDBI、城市热岛强度、空间相关/回归分析
- 验证风险：
  - Sentinel-2 不能直接做 LST
  - Landsat 与 Sentinel-2 分辨率不一致
  - 夏季影像需要云量过滤和季节一致性
  - 城市扩张和热环境存在尺度匹配问题

## M2 代码改造顺序

1. 扩展 `TaskType`，保留旧类型兼容。
2. 新增 `configs/geo_mvp.yaml`，沿用 baseline 的稳定配置。
3. 改造 Planner prompt，生成 GIS/遥感任务类型。
4. 改造 AgentPool，让新 task types 可执行。
5. 改造 Summarizer prompt，输出 GIS/遥感报告结构和可信度分级占位。
6. 跑 demo query，确认不会破坏 baseline。

## 第一阶段验收标准

- `configs/baseline.yaml` 仍可跑通。
- `configs/geo_mvp.yaml` 可生成 GIS/遥感 DAG。
- DAG 中出现 `data_discovery`、`method_design`、`geo_validation`。
- 最终报告至少包含：
  - 研究问题拆解
  - 推荐数据源
  - 推荐方法流程
  - 验证风险
  - 可信度说明
