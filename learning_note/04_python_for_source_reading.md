# 读懂本项目需要的 Python 知识

这不是 Python 入门大全，而是为了读懂当前 Agent 项目而设计的补课笔记。每节先给简单 demo，再说明源码中对应位置。

## 1. 类型标注

Demo：

```python
from typing import Any

def load_config(path: str | None = None) -> dict[str, Any]:
    if path is None:
        return {}
    return {"path": path}
```

重点：

- `str | None` 表示可以是字符串，也可以是 `None`。
- `dict[str, Any]` 表示 key 是字符串，value 可以是任意类型。
- 类型标注主要帮助阅读和 IDE 提示，运行时通常不强制检查。

源码位置：

- `src/core/runner.py`
- `src/orchestrator/schemas.py`
- `src/planner/planner.py`

## 2. dataclass：数据容器

Demo：

```python
from dataclasses import dataclass, field

@dataclass
class SubTask:
    task_id: str
    description: str
    dependencies: list[str] = field(default_factory=list)

task = SubTask(task_id="task_1", description="Search papers")
print(task.dependencies)  # []
```

为什么用 `field(default_factory=list)`：

```python
# 不推荐：多个对象可能共享同一个默认 list
dependencies: list[str] = []
```

源码位置：

- `src/orchestrator/schemas.py`

项目中的几个核心 dataclass：

- `SubTask`
- `AgentResult`
- `ResearchReport`
- `RunConfig`

## 3. Enum：有限状态和值

Demo：

```python
from enum import Enum

class AgentStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"

status = AgentStatus.SUCCESS
print(status.value)  # success
```

为什么不用普通字符串：

- 避免拼写错误。
- 让状态集合更清楚。
- IDE 可以自动补全。

源码位置：

- `src/orchestrator/schemas.py`

## 4. JSON 解析与健壮处理

Demo：

```python
import json
import re

raw = """
```json
{"sub_tasks": [{"task_id": "task_1"}]}
```
"""

text = raw.strip()
match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
if match:
    text = match.group(1)

data = json.loads(text)
print(data["sub_tasks"])
```

源码位置：

- `src/planner/planner.py`

为什么重要：

LLM 经常不严格输出纯 JSON，可能包在 markdown code block 里。Planner 的 `_parse_plan()` 做了容错解析：去 code block、提取最外层 JSON、删除 trailing comma、再 `json.loads()`。

## 5. 自定义异常

Demo：

```python
class PlanParseError(Exception):
    pass

def parse_plan(text: str):
    if "sub_tasks" not in text:
        raise PlanParseError("missing sub_tasks")
```

源码位置：

- `src/planner/planner.py`

作用：

把“规划解析失败”这类业务错误和普通 Python 错误区分开，Orchestrator 可以捕获它并进入失败或重规划逻辑。

## 6. 面向对象与继承

Demo：

```python
class BaseAgent:
    def __init__(self, name: str):
        self.name = name

class ResearcherAgent(BaseAgent):
    async def run(self, task, context):
        return f"{self.name} runs {task}"
```

源码位置：

- `src/agents/base_agent.py`
- `src/agents/researcher.py`
- `src/agents/summarizer.py`

阅读方法：

先看 `__init__()` 保存了哪些属性，再看核心 public 方法，比如 `run()`。

## 7. 字典分发：tool_map

Demo：

```python
class Calculator:
    name = "calculator"
    async def execute(self, expression: str):
        return eval(expression)

tools = [Calculator()]
tool_map = {tool.name: tool for tool in tools}

tool_name = "calculator"
result = await tool_map[tool_name].execute(expression="1 + 2")
```

源码位置：

- `src/agents/researcher.py`

项目里 `ResearcherAgent` 会从模型返回的 `tool_calls` 中解析工具名，然后通过 `self.tool_map.get(tool_name)` 找到工具对象并执行。

## 8. async / await：异步函数

Demo：

```python
import asyncio

async def search(query: str):
    await asyncio.sleep(1)
    return f"result for {query}"

async def main():
    result = await search("remote sensing")
    print(result)

asyncio.run(main())
```

重点：

- `async def` 定义协程函数。
- `await` 等待协程完成。
- `asyncio.run()` 启动事件循环。

源码位置：

- `scripts/run_single.py`
- `src/core/runner.py`
- `src/orchestrator/orchestrator.py`
- `src/agents/researcher.py`
- `src/tools/*.py`

## 9. asyncio.gather：并发执行

Demo：

```python
import asyncio

async def worker(i: int):
    await asyncio.sleep(1)
    return f"task {i} done"

async def main():
    results = await asyncio.gather(
        worker(1),
        worker(2),
        worker(3),
    )
    print(results)

asyncio.run(main())
```

这三个任务会并发等待，总耗时约 1 秒，而不是 3 秒。

源码位置：

- `src/orchestrator/orchestrator.py`

## 10. asyncio.Semaphore：限制并发

Demo：

```python
import asyncio

sem = asyncio.Semaphore(2)

async def worker(i: int):
    async with sem:
        print("start", i)
        await asyncio.sleep(1)
        print("end", i)

asyncio.run(asyncio.gather(*(worker(i) for i in range(5))))
```

注意：上面最后一行在脚本里更稳妥写法是：

```python
async def main():
    await asyncio.gather(*(worker(i) for i in range(5)))

asyncio.run(main())
```

作用：

限制同时运行的子 Agent 数量，避免 API、网络、CPU 被打爆。

源码位置：

- `src/orchestrator/orchestrator.py`

## 11. asyncio.wait_for：超时控制

Demo：

```python
import asyncio

async def slow_task():
    await asyncio.sleep(5)
    return "done"

async def main():
    try:
        result = await asyncio.wait_for(slow_task(), timeout=1)
    except asyncio.TimeoutError:
        result = "timeout"
    print(result)

asyncio.run(main())
```

源码位置：

- `src/orchestrator/orchestrator.py`

项目中每个 `SubTask` 有自己的 `timeout_seconds`，Orchestrator 用 `wait_for` 控制单任务超时。

## 12. asyncio.to_thread：把同步函数放到线程里

Demo：

```python
import asyncio
import time

def blocking_call():
    time.sleep(2)
    return "done"

async def main():
    result = await asyncio.to_thread(blocking_call)
    print(result)

asyncio.run(main())
```

源码位置：

- `src/agents/researcher.py`

为什么需要：

LLM policy 调用是同步函数，如果直接在 async 函数里调用，会阻塞事件循环。`asyncio.to_thread()` 可以把它丢到线程池里执行。

## 13. 抽象基类 ABC

Demo：

```python
from abc import ABC, abstractmethod

class BaseTool(ABC):
    @abstractmethod
    async def execute(self, **kwargs):
        pass

class WebSearchTool(BaseTool):
    async def execute(self, query: str):
        return {"query": query}
```

源码位置：

- `src/tools/web_search.py`
- `src/tools/browser.py`

作用：

规定所有工具都必须实现 `execute()`，这样 Agent 可以用统一方式调用不同工具。

## 14. 线程锁 RLock

Demo：

```python
import threading

lock = threading.RLock()
data = []

def add_item(x):
    with lock:
        data.append(x)
```

源码位置：

- `src/memory/memory_store.py`

为什么需要：

MemoryStore 同时维护 SQLite、缓存和向量索引。锁可以避免多个并发任务同时改内存索引导致状态不一致。

## 15. 读源码的顺序建议

不要从最复杂的 `orchestrator.py` 第一行啃到最后一行。推荐顺序：

1. `schemas.py`：先知道数据长什么样。
2. `runner.py`：知道模块怎么装配。
3. `planner.py`：知道任务怎么拆。
4. `agent_pool.py`：知道 Agent 怎么创建和复用。
5. `researcher.py`：知道工具怎么调用。
6. `orchestrator.py`：最后看完整状态机。

