# 03. 模块：Planner、DAG 与 Orchestrator 异步调度

## 1. 模块职责

这个模块是 DeepResearch 的“发动机”。

- `Planner`：把复杂问题拆成结构化子任务。
- `DAG`：保存子任务依赖关系。
- `Orchestrator`：按状态机运行，按 DAG 层级并发调度 Agent。

它解决的问题是：复杂研究任务不是一步完成的，需要先拆解，再按照依赖关系执行，有些任务能并行，有些任务必须等前置任务完成。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `src/planner/planner.py` | 让 LLM 生成 JSON 子任务，并解析成 DAG |
| `src/planner/dag.py` | 有向无环图、拓扑排序、并行层计算 |
| `src/orchestrator/orchestrator.py` | 9 状态状态机、并发调度、收集结果、重规划、合成、对抗 |
| `src/orchestrator/schemas.py` | `SubTask`、`AgentResult`、`ResearchReport`、`RunConfig` |
| `src/orchestrator/agent_pool.py` | 获取和复用 Agent |

## 3. 核心数据结构

`schemas.py` 是读这个模块的钥匙。

| 类 | 读法 |
|---|---|
| `SubTask` | Planner 生成的一个子任务，有 `task_id`、`description`、`dependencies` |
| `AgentResult` | 一个 Agent 执行子任务后的结果，有 `status`、`output`、`trajectory`、`confidence` |
| `ResearchReport` | 最终报告，有正文、sources、confidence、搜索轮数、重规划次数 |
| `RunConfig` | 一次运行的并发数、全局超时、重规划开关 |
| `OrchestratorState` | 状态机枚举 |

你可以这样理解：

```text
SubTask 是待办事项
AgentResult 是完成记录
ResearchReport 是最终交付
RunConfig 是本次任务的运行规则
```

## 4. Planner 如何把问题变成 DAG

`Planner.generate_plan()` 主流程：

```python
prompt = self._build_prompt(query, memory_context)
response = self.policy(messages)
content = response.get("content", "") or ""
return self._parse_plan(content)
```

关键点：

1. 它让 LLM 输出固定 JSON 格式。
2. JSON 里有 `sub_tasks`。
3. 每个 `sub_task` 有 `dependencies`。
4. `_parse_plan()` 把 JSON 变成 `DAG`。
5. 如果 JSON 有 Markdown 代码块、尾逗号、注释，解析器会做兜底清理。

Planner 期望 LLM 输出类似：

```json
{
  "sub_tasks": [
    {
      "task_id": "task_1",
      "task_type": "search",
      "description": "检索成都AI Agent实习岗位要求",
      "dependencies": [],
      "search_hints": ["成都", "AI Agent", "实习"]
    },
    {
      "task_id": "task_2",
      "task_type": "analyze",
      "description": "分析岗位要求中的核心技术关键词",
      "dependencies": ["task_1"]
    }
  ]
}
```

## 5. DAG 如何决定并发

`DAG.add_edge(from_node, to_node)` 的语义是：

```text
from_node -> to_node
表示 to_node 依赖 from_node
```

`get_parallel_groups()` 会返回每一层能同时跑的任务：

```text
task_1, task_2 没依赖，可以并行
task_3 依赖 task_1 和 task_2，必须后跑

=> [["task_1", "task_2"], ["task_3"]]
```

它使用的是 Kahn 拓扑排序思想：

1. 找入度为 0 的节点。
2. 这些节点作为第一层并行执行。
3. 移除这些节点的边。
4. 新的入度为 0 的节点作为下一层。

## 6. Orchestrator 状态机

`OrchestratorState` 包含：

```text
IDLE -> PLANNING -> DISPATCHING -> COLLECTING -> SYNTHESIZING -> ADVERSARIAL -> DONE
失败时可能进入 REPLANNING 或 FAILED
```

`Orchestrator.__init__()` 里把状态映射到处理函数：

```python
self._state_handlers = {
    OrchestratorState.IDLE: self._on_idle,
    OrchestratorState.PLANNING: self._do_planning,
    OrchestratorState.DISPATCHING: self._do_dispatching,
    ...
}
```

`run()` 主循环：

```python
while self._current_state not in (DONE, FAILED):
    handler = self._state_handlers.get(self._current_state)
    next_state = await handler()
    self._current_state = next_state
```

这就是状态机：当前状态决定调用哪个函数，函数返回下一个状态。

## 7. 异步并发调度

`_do_dispatching()` 是这个项目最重要的异步代码。

核心结构：

```python
semaphore = asyncio.Semaphore(self._config.max_concurrent)
parallel_groups = self._dag.get_parallel_groups()

for group in parallel_groups:
    async def _run_one(task_id: str) -> AgentResult:
        async with semaphore:
            agent = await self.agent_pool.get_agent(subtask.task_type)
            result = await asyncio.wait_for(
                agent.run(subtask, context),
                timeout=subtask.timeout_seconds,
            )
            await self.agent_pool.release_agent(agent)
            return result

    coros = [_run_one(tid) for tid in group]
    layer_results = await asyncio.gather(*coros, return_exceptions=True)
```

逐句理解：

- `Semaphore`：最多允许 `max_concurrent` 个任务同时跑。
- `parallel_groups`：DAG 同一层的任务可以并发。
- `_run_one()`：执行一个子任务。
- `wait_for()`：单任务超时控制。
- `gather()`：等待这一层所有任务完成。

## 8. 失败和重规划

单个任务失败不会立刻让全局失败，而是变成 `AgentResult`：

```python
AgentResult(status=AgentStatus.FAILED, output="Exception: ...")
```

收集阶段 `_do_collecting()` 会统计：

```python
success_count = ...
fail_count = ...
if self._should_replan(self._results):
    return OrchestratorState.REPLANNING
```

重规划策略：

- 失败率 > 50% 时触发。
- 成功率 < 30% 且有失败时触发。
- 未超过 `max_replan_rounds` 才重规划。

`_do_replanning()` 会调用：

```python
new_dag = self.planner.replan(...)
```

## 9. 配置如何影响这个模块

`configs/default.yaml`：

```yaml
orchestrator:
  max_concurrent: 5
  global_timeout_seconds: 600
  max_replan_rounds: 3
  max_sub_questions: 10
```

影响：

- `max_concurrent` 控制 `Semaphore` 并发上限。
- `global_timeout_seconds` 控制全局硬超时。
- `max_replan_rounds` 控制最多重规划几次。
- `max_sub_questions` 会进入 `RunConfig`，但当前 Planner prompt 里主要由 prompt 规则约束子任务数量。

## 10. 需要掌握的 Python 语法

### 嵌套异步函数

`_run_one()` 定义在 `_do_dispatching()` 里面，因为它只服务于这一段调度逻辑。

### `async with`

```python
async with semaphore:
```

表示异步上下文管理器。进入时获取并发名额，退出时释放。

### `asyncio.gather`

```python
await asyncio.gather(*coros, return_exceptions=True)
```

并发等待多个协程。`return_exceptions=True` 表示某个任务出异常时，不让整个 gather 直接抛出，而是把异常作为结果返回，方便统一包装成失败结果。

### `Enum`

状态机不用字符串，而用 `OrchestratorState.PLANNING` 这种枚举，减少状态拼写错误。

## 11. 第一遍、第二遍、面试读法

第一遍：

- 先读 `schemas.py`。
- 再读 `planner.py` 的 `generate_plan()` 和 `_parse_plan()`。
- 再读 `orchestrator.py` 的 `run()`、`_do_planning()`、`_do_dispatching()`。

第二遍：

- 读 `dag.py` 的 `get_parallel_groups()`。
- 读 `_do_collecting()` 和 `_do_replanning()`。
- 跟踪失败时状态怎么变化。

面试读法：

- 能画出状态机。
- 能讲清 DAG 为什么能提升并发。
- 能讲清 `Semaphore + gather + wait_for` 三者的作用。
- 能说明为什么不用 LangGraph：自研调度更可控，但需要自己处理状态和异常。

## 12. 小练习

给定 DAG：

```text
task_1: dependencies=[]
task_2: dependencies=[]
task_3: dependencies=["task_1"]
task_4: dependencies=["task_1", "task_2"]
```

请问 `get_parallel_groups()` 大概会分成几层？

参考答案：

```text
第 1 层: task_1, task_2
第 2 层: task_3, task_4
```

原因：`task_3` 和 `task_4` 都要等前置任务完成，但它们之间没有互相依赖。

## 13. 常见面试问法

Q：为什么要让 Planner 输出 DAG，而不是直接输出线性步骤？

A：因为深度研究任务里很多子问题相互独立，可以并行执行。DAG 能表达依赖关系，Orchestrator 可以按层并发调度，提高效率，同时保证依赖任务先完成。

Q：`asyncio.Semaphore` 解决什么问题？

A：限制最大并发，避免一次性启动太多子任务导致 API 限流、内存压力或工具调用过载。

Q：重规划什么时候触发？

A：当前实现中，失败率超过 50%，或成功率低于 30% 且存在失败任务，会触发 replan，但受 `max_replan_rounds` 限制。

## 14. 证据

- 数据结构：`src/orchestrator/schemas.py`
- Planner：`src/planner/planner.py`
- DAG：`src/planner/dag.py`
- 编排状态机和异步调度：`src/orchestrator/orchestrator.py`
- Agent 对象池：`src/orchestrator/agent_pool.py`
- 配置：`configs/default.yaml`

