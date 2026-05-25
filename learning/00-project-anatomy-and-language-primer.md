# 00. Python 项目骨架与语法入门

这份文档先解决一个更基础的问题：你打开 DeepResearch Agent 仓库时，应该先看什么？`pyproject.toml`、`requirements.txt`、`.env.template`、`configs/default.yaml`、`async def`、`await`、`dataclass` 这些到底代表什么？

你现在不需要把每个模块都背下来。第一遍目标是：能说清楚这个 Python Agent 项目怎么安装、怎么配置、怎么启动，以及异步任务是怎么跑起来的。

## 1. 仓库地图

| 路径 | 作用 | 第一遍怎么看 |
|---|---|---|
| `pyproject.toml` | Python 包元信息、依赖、命令行入口、格式化/类型检查配置 | 看 `requires-python`、`dependencies`、`project.scripts` |
| `requirements.txt` | pip 安装依赖清单，注释比 `pyproject.toml` 更适合初学者阅读 | 看每个库为什么需要，例如 `openai`、`aiohttp`、`pyyaml`、`sentence-transformers` |
| `.env.template` | LLM 后端、搜索后端、LangSmith、工具层 API Key 模板 | 看哪些配置必须放环境变量，避免硬编码密钥 |
| `.env.tools.template` | 工具层环境变量模板 | 看搜索、浏览器、文件读取、代码沙箱如何配置 |
| `configs/default.yaml` | 系统总配置：模型分工、并发、Planner、Memory、Adversarial、Tools | 看 `model.backend_mapping`、`orchestrator.max_concurrent`、`tools.web_search.mock_mode` |
| `scripts/` | 命令行入口 | 面试主讲 `run_single.py`，然后知道 `run_repl.py`、`run_eval.py`、`run_ablation.py` |
| `src/core/runner.py` | 项目装配中心 | 看 `load_config()`、`initialize_modules()`、`run_research()` |
| `src/orchestrator/` | 多 Agent 编排器、状态机、DAG 调度、AgentPool | 看 `orchestrator.py`、`schemas.py`、`agent_pool.py` |
| `src/planner/` | 把用户问题拆成 JSON DAG | 看 `planner.py` 和 `dag.py` |
| `src/agents/` | ResearcherAgent 和 SummarizerAgent | 看工具调用循环和最终报告合成 |
| `src/tools/` | web search、browser、arxiv、file reader、calculator 等工具 | 看每个工具都有 `execute()` 和 `get_openai_tool_schema()` |
| `src/models/` | OpenAI-compatible LLM 调用封装和模型路由 | 看 `ModelRouter.create_backend()` 与 `VLLMPolicy.__call__()` |
| `src/memory/` | SQLite + embedding 的共享记忆 | 看 `MemoryEntry`、`LongTermMemory`、`SharedMemoryStore` |
| `src/compressor/` | 长上下文压缩 | 看它为什么用 embedding/TextRank/摘要 |
| `src/adversarial/` | Red-Blue 对抗审查和修复 | 看 `AdversarialLoop.run()` |
| `evaluation/` | 评测、消融实验、benchmark | 面试可作为亮点，但不是第一周主线 |
| `tests/` | 环境验证和工具演示 | 先看 `validate_env.py` 和 `demo.py` |
| `outputs/` | 生成的报告、日志等输出 | 不是源码证据，阅读时不要把生成物当核心实现 |
| `learning_note/` | 旧学习笔记 | 可参考，但本轮以 `learning/` 新文档为准 |

## 2. Python 包与依赖

`pyproject.toml` 说明这是一个 Python 3.10+ 项目：

```toml
[project]
name = "deep-research-agent"
requires-python = ">=3.10"

[project.scripts]
run-research = "scripts.run_single:main"
run-eval = "scripts.run_eval:main"
```

这里有两个重点：

1. `requires-python = ">=3.10"`：所以源码里大量使用 `str | None`、`dict[str, Any]` 这种 Python 3.10 类型写法。
2. `[project.scripts]`：安装成包以后，可以用 `run-research` 这类命令调用 `scripts.run_single:main`。如果没安装，也可以直接运行 `python scripts/run_single.py`。

`requirements.txt` 是更详细的依赖说明。按用途分：

| 依赖 | 项目里负责什么 |
|---|---|
| `openai` | 调用 DeepSeek、MiMo、OpenAI、vLLM 等 OpenAI 兼容接口 |
| `aiohttp` | 异步 HTTP 请求，主要用于搜索和网页读取工具 |
| `pyyaml` | 读取 `configs/default.yaml` |
| `python-dotenv` | 加载 `.env` / `.env.local` |
| `sentence-transformers` | Memory 和 Compressor 的 embedding |
| `numpy`、`scikit-learn` | 向量相似度、评测指标 |
| `networkx` | TextRank 类图算法 |
| `datasets`、`pandas`、`matplotlib` | 评测数据、实验统计、图表 |
| `beautifulsoup4` | `BrowserTool` 提取网页正文 |

第一遍学习不要纠结安装所有可选依赖。你需要先明白：这个项目是 **LLM API + 异步工具 + YAML 配置 + SQLite/embedding memory + 评测脚本** 的组合。

## 3. 环境变量和配置

这个项目把配置分成两层：

| 层 | 文件 | 放什么 |
|---|---|---|
| 连接信息 | `.env` / `.env.local`，模板是 `.env.template` | API Key、Base URL、Model 名、搜索后端 Key |
| 行为参数 | `configs/default.yaml` | 哪个模块用哪个模型、temperature、并发数、超时、Memory、Adversarial、Tools 开关 |

`src/utils/env_config.py` 的加载顺序是：

```python
load_dotenv(".env")
load_dotenv(".env.local", override=True)
```

意思是：先读项目默认 `.env`，再读本地 `.env.local`，本地配置优先级更高。真实 API Key 应该放 `.env.local`，不要提交到 Git。

`configs/default.yaml` 里最值得先看这些字段：

```yaml
model:
  backend: "deepseek"
  backend_mapping:
    solver: "deepseek"
    planner: "deepseek"
    summarizer: "deepseek"
    judge: "mimo"
    red_agent: "mimo"
    blue_agent: "mimo"

orchestrator:
  max_concurrent: 5
  global_timeout_seconds: 600
  max_replan_rounds: 3

tools:
  web_search:
    enabled: true
    mock_mode: false
```

面试里可以说：

> 这个项目把敏感连接信息放在 `.env`，把模块行为参数放在 YAML。`runner.py` 读取 YAML 后创建不同模块，`ModelRouter` 再从 `.env` 读取后端连接信息，实现不同 Agent 使用不同模型。

## 4. 启动路径

单条研究任务的入口是：

```bash
python scripts/run_single.py --query "2024-2025年大模型Agent技术趋势与落地案例研究"
```

真实调用链是：

```text
scripts/run_single.py
  -> load_config()
  -> initialize_modules()
  -> asyncio.run(run_research())
  -> orchestrator.run()
  -> save_report()
```

对应代码：

- `scripts/run_single.py`：解析命令行参数、设置日志、调用主流程。
- `src/core/runner.py::load_config()`：读取 YAML。
- `src/core/runner.py::initialize_modules()`：创建 ModelRouter、Planner、Compressor、Memory、Tools、AdversarialLoop、AgentPool、Orchestrator。
- `src/core/runner.py::run_research()`：构造 `RunConfig`，`await orchestrator.run(...)`。
- `src/orchestrator/orchestrator.py::run()`：状态机开始执行。

这里最重要的语法是：

```python
report = asyncio.run(run_research(args.query, config, modules))
```

`run_research()` 是 `async def`，普通同步函数 `main()` 不能直接 `await`，所以用 `asyncio.run()` 创建事件循环，把异步主流程跑完。

## 5. Python 语法第一课：类型注解

项目里经常写：

```python
def load_config(config_path: str | None = None) -> dict:
```

读法：

- `config_path: str | None`：参数可以是字符串，也可以是 `None`。
- `= None`：默认值是 `None`。
- `-> dict`：函数返回一个字典。

项目里也常见：

```python
modules: dict[str, Any] = {}
```

读法：

- `dict[str, Any]`：字典的 key 是字符串，value 可以是任意类型。
- `Any` 来自 `typing`，表示这里不强制限定类型。

你现在不用写得很熟，但读代码时要知道这些是“给人和类型检查器看的标注”，不是运行时的主要逻辑。

## 6. Python 语法第二课：dataclass

`src/orchestrator/schemas.py` 里定义了核心数据结构：

```python
@dataclass
class SubTask:
    task_id: str
    task_type: TaskType
    description: str
    dependencies: list[str] = field(default_factory=list)
```

`@dataclass` 会自动生成初始化函数。你可以把它理解成“轻量级数据对象”。比如：

```python
task = SubTask(
    task_id="task_1",
    task_type=TaskType.SEARCH,
    description="搜索成都AI Agent实习岗位要求",
)
```

不需要手写 `__init__`，但可以直接访问：

```python
task.description
task.dependencies
```

这个项目中最重要的 dataclass：

| 类 | 代表什么 |
|---|---|
| `SubTask` | Planner 拆出来的一个子任务 |
| `AgentResult` | Agent 执行一个子任务后的结果 |
| `ResearchReport` | 最终报告对象 |
| `RunConfig` | 一次运行的并发、超时、重规划、对抗开关 |
| `MemoryEntry` | 写入 SQLite/向量索引的一条记忆 |
| `Issue`、`RedVerdict`、`FixOperation` | Red-Blue 对抗流程里的问题、评分、修复操作 |

## 7. Python 语法第三课：Enum

`schemas.py` 里有：

```python
class OrchestratorState(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    DISPATCHING = "dispatching"
```

`Enum` 是枚举。它用来限制状态只能取固定值，避免到处写魔法字符串。

项目里三个核心枚举：

| 枚举 | 用途 |
|---|---|
| `OrchestratorState` | 编排器状态机：planning、dispatching、collecting 等 |
| `TaskType` | 子任务类型：search、analyze、verify |
| `AgentStatus` | 子任务结果：success、failed、timeout |

面试讲法：

> 我会把运行时状态建模为枚举和 dataclass。枚举保证状态值有限，dataclass 保证跨模块传递的数据结构清晰。

## 8. Python 语法第四课：async / await

这个项目的核心难点是异步。

你先记一个公式：

> `async def` 定义协程函数，调用后不会立刻跑完；`await` 表示“等这个异步任务完成，期间事件循环可以去跑别的任务”。

项目里的典型例子：

```python
async def run_research(query: str, config: dict, modules: dict[str, Any]) -> str:
    report = await orchestrator.run(query, config=run_cfg)
```

`orchestrator.run()` 也是异步的，因为它里面要并发执行多个子任务、等待工具、等待 LLM、等待超时。

再看 `Orchestrator._do_dispatching()`：

```python
semaphore = asyncio.Semaphore(self._config.max_concurrent)

async def _run_one(task_id: str) -> AgentResult:
    async with semaphore:
        agent = await self.agent_pool.get_agent(subtask.task_type)
        result = await asyncio.wait_for(
            agent.run(subtask, context),
            timeout=subtask.timeout_seconds,
        )

layer_results = await asyncio.gather(*coros, return_exceptions=True)
```

逐句理解：

- `Semaphore(max_concurrent)`：限制最多同时跑几个任务。
- `async with semaphore`：进入这个块前要拿到一个并发名额。
- `await self.agent_pool.get_agent(...)`：从对象池拿 Agent。
- `asyncio.wait_for(..., timeout=...)`：给单个任务设置超时。
- `asyncio.gather(*coros)`：同一层 DAG 任务并发跑。

所以这个项目的并发不是多线程主导，而是 `asyncio` 主导。

## 9. Python 语法第五课：同步 LLM 调用如何塞进异步流程

`ResearcherAgent.run()` 是异步函数，但 `self.policy(messages)` 是同步调用。项目用了：

```python
response = await asyncio.to_thread(self.policy, messages)
```

意思是：把同步阻塞的 LLM 调用放到线程池里跑，避免卡住整个 asyncio 事件循环。

这句很适合面试讲：

> 因为 OpenAI-compatible client 调用是同步的，为了不阻塞整个异步编排器，ResearcherAgent 用 `asyncio.to_thread()` 把 policy 调用放到线程池里执行；工具执行仍然保持 `await tool.execute(...)`。

## 10. Python 语法第六课：抽象基类和工具接口

`src/agents/base_agent.py`：

```python
class BaseAgent(ABC):
    @abstractmethod
    async def run(self, task: SubTask, context: dict) -> AgentResult:
        pass
```

`ABC` + `@abstractmethod` 表示“子类必须实现这个方法”。所以：

- `ResearcherAgent` 实现 `run()`：多轮工具调用。
- `SummarizerAgent` 实现 `run()`：合成最终报告。

工具也是类似思想。比如 `BaseWebSearchTool`：

```python
@abstractmethod
async def execute(self, query: str, top_n: int = 5) -> dict[str, Any]:
    pass
```

这说明工具统一通过 `execute()` 被调用。`ResearcherAgent` 不需要关心你底层是 SerpAPI、Bing、Bocha 还是 Mock，只要工具对象有 `name` 和 `execute()`。

## 11. Python 语法第七课：装饰器

项目里常见：

```python
@trace_chain(name="orchestrator.run", tags=["m1", "orchestrator"])
async def run(...):
```

`@trace_chain` 是装饰器。你可以先把它理解成“给函数外面包一层追踪逻辑”。函数业务逻辑仍然在 `run()` 里面，装饰器负责可观测性。

相关文件：

- `src/utils/tracing.py`
- `src/orchestrator/orchestrator.py`
- `src/agents/researcher.py`
- `src/memory/memory_store.py`

第一遍读源码时，如果装饰器看不懂，可以先跳过，只记住它是追踪/日志增强。

## 12. Python 语法第八课：包导入

项目里有两种导入：

```python
from src.core.runner import initialize_modules
from ..planner.dag import DAG
```

区别：

- `from src...` 是从项目根包开始的绝对导入。
- `from ..planner...` 是相对导入，表示从当前包往上一级再找 `planner`。

`runner.py` 里还手动把项目根目录加入 `sys.path`：

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
```

这是为了保证直接运行脚本时，`src` 包也能被正确导入。

## 13. 第一次读代码的顺序

不要从 `README.md` 一路读到所有文件，那样会散。

按这个顺序：

1. `pyproject.toml`：确认这是 Python 包，入口命令是什么。
2. `requirements.txt`：确认依赖分层。
3. `.env.template`：确认外部服务有哪些。
4. `configs/default.yaml`：确认哪些模块开启，模型怎么分工。
5. `scripts/run_single.py`：确认命令行如何进入主流程。
6. `src/core/runner.py`：确认模块如何被创建。
7. `src/orchestrator/schemas.py`：确认数据结构。
8. `src/orchestrator/orchestrator.py`：确认状态机和并发。
9. `src/planner/planner.py`：确认 query 如何变成 DAG。
10. `src/agents/researcher.py`：确认工具调用循环。
11. `src/agents/summarizer.py`：确认最终报告怎么生成。

## 14. 第二次读代码的问题清单

读每个文件时都回答这几个问题：

| 问题 | 例子 |
|---|---|
| 输入是什么？ | `run_single.py` 输入 `--query`；`Planner` 输入 query；`ResearcherAgent` 输入 SubTask |
| 输出是什么？ | `Planner` 输出 DAG；`Agent` 输出 AgentResult；`Summarizer` 输出 ResearchReport |
| 状态存在哪里？ | `Orchestrator._memory_store`、`SharedMemoryStore`、SQLite |
| 配置从哪里来？ | YAML、`.env`、函数参数 |
| 异步在哪里？ | `asyncio.run`、`await orchestrator.run`、`gather`、`wait_for`、`aiohttp` |
| 失败怎么处理？ | timeout、failed、replan、fallback report、mock tool |

## 15. 初学者词汇表

| 词 | 第一遍理解 | 以后深入 |
|---|---|---|
| Agent | 能接收任务、调用工具、返回结果的对象 | tool-calling loop、policy、trajectory |
| Planner | 把大问题拆成小任务的人 | JSON DAG、replan、约束 Prompt |
| Orchestrator | 调度任务执行顺序和并发的人 | 状态机、Semaphore、DAG layer |
| DAG | 有向无环图，表示任务依赖 | Kahn 拓扑排序、并行层 |
| Tool Calling | LLM 输出要调用哪个工具和参数 | OpenAI function calling schema |
| Policy | 对 LLM 后端的一层封装 | OpenAI-compatible API、截断、工具调用解析 |
| Memory Store | 跨 Agent 共享记忆 | SQLite、embedding、去重、冲突检测 |
| Compressor | 长上下文压缩 | embedding 过滤、TextRank、摘要 |
| Red-Blue Loop | Red 找报告问题，Blue 修复 | verdict、fix operation、收敛/震荡检测 |
| Evaluation | 评估报告质量 | 规则指标、LLM-as-Judge、消融实验 |

## 16. 本文证据

- Python 包与脚本入口：`pyproject.toml`
- 依赖用途：`requirements.txt`
- 环境变量模板：`.env.template`, `.env.tools.template`
- 配置分层：`configs/default.yaml`, `src/utils/env_config.py`
- CLI 启动：`scripts/run_single.py`
- 模块装配：`src/core/runner.py`
- 核心数据结构：`src/orchestrator/schemas.py`, `src/memory/long_term.py`
- 异步并发：`src/orchestrator/orchestrator.py`, `src/agents/researcher.py`, `src/tools/web_search.py`, `src/tools/browser.py`
- 对抗流程：`src/adversarial/loop.py`

