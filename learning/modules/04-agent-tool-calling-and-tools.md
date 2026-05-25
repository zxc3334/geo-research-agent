# 04. 模块：ResearcherAgent、Tool Calling 与工具层

## 1. 模块职责

这个模块负责“真正做研究”。

Planner 只负责拆任务，Orchestrator 只负责调度，真正执行子任务的是 `ResearcherAgent`。它会：

1. 根据 `SubTask` 构造 Prompt。
2. 把可用工具注册成 OpenAI function calling schema。
3. 调用 LLM，让模型决定要不要调用工具。
4. 执行工具，把工具结果塞回 messages。
5. 直到模型不再调用工具，返回 `AgentResult`。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `src/agents/base_agent.py` | Agent 抽象基类 |
| `src/agents/researcher.py` | 多轮 tool-calling 执行循环 |
| `src/tools/__init__.py` | 导出所有工具 |
| `src/tools/web_search.py` | 搜索工具，支持 SerpAPI/Bing/Bocha/Metaso/Mock |
| `src/tools/browser.py` | 网页正文读取工具 |
| `src/tools/arxiv_reader.py` | 论文检索工具 |
| `src/tools/calculator.py` | 安全数学计算 |
| `src/tools/code_sandbox.py` | Python 代码执行 |
| `src/tools/file_reader.py` | 本地文件读取 |
| `src/tools/notepad.py` | 中间笔记 |
| `src/core/runner.py` | `_create_tools_factory()` 创建工具列表 |

## 3. Agent 抽象

`BaseAgent` 定义统一接口：

```python
class BaseAgent(ABC):
    @abstractmethod
    async def run(self, task: SubTask, context: dict) -> AgentResult:
        pass
```

这表示所有 Agent 都必须实现：

```text
输入: SubTask + context
输出: AgentResult
```

`ResearcherAgent` 和 `SummarizerAgent` 都继承它，但职责不同：

- `ResearcherAgent`：执行单个子任务，可能多轮调用工具。
- `SummarizerAgent`：整合所有子任务结果，生成最终报告。

## 4. ResearcherAgent 主流程

`ResearcherAgent.run()` 的简化流程：

```text
构造 task prompt
判断是否适合搜索
注册 tools schema
for turn in max_turns:
    调用 LLM policy
    如果无 tool_calls:
        返回 AgentResult(SUCCESS)
    解析 tool_calls
    await 执行工具
    把工具结果追加到 messages
达到 max_turns:
    返回 AgentResult(TIMEOUT)
```

对应代码关键点：

```python
if hasattr(self.policy, "set_tools") and self.tools:
    schemas = [t.get_openai_tool_schema() for t in self.tools]
    self.policy.set_tools(schemas)
```

这一步把工具列表告诉模型。

```python
response = await asyncio.to_thread(self.policy, messages)
```

这一步调用 LLM。因为 `policy` 是同步函数，所以放到线程池。

```python
result = await self._execute_tool(tool_name, args)
```

这一步执行具体工具。

## 5. Tool Calling 数据格式

工具需要提供 OpenAI function calling schema。以 `WebSearchTool` 为例：

```python
def get_openai_tool_schema(self) -> dict:
    return {
        "type": "function",
        "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_n": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    }
```

模型看到这个 schema 后，可以输出类似：

```json
{
  "name": "web_search",
  "arguments": "{\"query\":\"成都 AI Agent 实习 MCP RAG\", \"top_n\":5}"
}
```

`ResearcherAgent` 解析后调用：

```python
await tool.execute(query="成都 AI Agent 实习 MCP RAG", top_n=5)
```

## 6. 工具层设计

| 工具 | 输入 | 输出 | 适合场景 |
|---|---|---|---|
| `web_search` | query、top_n | 标题、URL、摘要列表 | 找资料入口 |
| `browser` | URL、max_chars | 网页正文 | 搜索摘要不够，需要读原文 |
| `arxiv_reader` | query | 论文列表/摘要 | 学术论文任务 |
| `calculator` | expression | 计算结果 | 简单数学 |
| `code_sandbox` | Python code | 执行输出 | 数据处理、复杂计算 |
| `file_reader` | path | 文件内容 | 用户给本地文件 |
| `notepad` | action/content | 笔记读写 | 中间结论记录 |

工具层统一规则：

- 每个工具都有 `name`。
- 每个工具都有 `description`。
- 每个工具有 `execute()`。
- 面向 LLM 的工具有 `get_openai_tool_schema()`。

## 7. 搜索工具如何切后端

`WebSearchTool.__init__()` 读取：

```python
self.backend = get_env("SEARCH_BACKEND", "serpapi")
self.serpapi_key = get_env("SERPAPI_KEY")
self.bocha_key = get_env("BOCHA_API_KEY")
```

`execute()` 分发：

```python
if self.backend == "bing":
    return await self._bing_execute(query, top_n)
if self.backend == "bocha":
    return await self._bocha_execute(query, top_n)
if self.backend == "metaso":
    return await self._metaso_execute(query, top_n)
return await self._serpapi_execute(query, top_n)
```

这就是“零源码切换搜索后端”：改 `.env.local` 的 `SEARCH_BACKEND` 和对应 Key 即可。

## 8. BrowserTool 如何读网页

`BrowserTool.execute()`：

```python
html = await self._fetch(url)
text = self._extract_text(html)
text = self._clean_text(text)
```

`_fetch()` 用 `aiohttp`：

```python
async with aiohttp.ClientSession(...) as session:
    async with session.get(url) as resp:
        return await resp.text()
```

`_extract_text()` 用 `BeautifulSoup` 移除 `script/style/nav/header/footer` 等噪声，再优先找 `article` 或 `main`。

## 9. 配置如何影响工具

### Mock 模式

`configs/default.yaml`：

```yaml
tools:
  web_search:
    mock_mode: false
```

在 `runner.py::_create_tools_factory()`：

```python
if mock_mode:
    tools["web_search"] = MockWebSearchTool()
else:
    tools["web_search"] = WebSearchTool()
```

如果你只是学习主流程，建议先用 Mock 模式，避免 API Key 和网络问题干扰。

### 工具后端

`.env.template`：

```env
SEARCH_BACKEND=bocha
BOCHA_API_KEY=...
BROWSER_TIMEOUT=15
ARXIV_READER_BACKEND=openalex
```

这些值被工具构造函数读取。

## 10. 需要掌握的 Python 语法

### 抽象基类

```python
class BaseWebSearchTool(ABC):
    @abstractmethod
    async def execute(...):
        pass
```

含义：子类必须实现 `execute()`。

### `await tool.execute(**args)`

```python
return await tool.execute(**args)
```

`**args` 是字典解包。例如：

```python
args = {"query": "AI Agent", "top_n": 5}
tool.execute(**args)
```

等价于：

```python
tool.execute(query="AI Agent", top_n=5)
```

### `asyncio.to_thread`

```python
response = await asyncio.to_thread(self.policy, messages)
```

把同步阻塞函数放进线程池，避免阻塞异步事件循环。

### `async with aiohttp.ClientSession`

这是异步上下文管理器，用来确保 HTTP session 正确关闭。

## 11. 第一遍、第二遍、面试读法

第一遍：

- 看 `BaseAgent.run()` 的接口。
- 看 `ResearcherAgent.run()` 的主循环。
- 看 `_execute_tool()`。
- 看 `WebSearchTool.execute()` 如何分发后端。

第二遍：

- 看 `_build_task_prompt()` 如何根据关键词推荐工具。
- 看 `VLLMPolicy.__call__()` 如何解析 tool calls。
- 看 `BrowserTool._extract_text()` 如何清理网页。

面试读法：

- 重点讲“工具统一接口 + function calling schema + Agent 多轮循环”。
- 说明为什么要限制工具最多 2 次：控制成本和避免无限工具调用。
- 说明同步 LLM 调用为什么用 `to_thread`。

## 12. 小练习

如果 Planner 给出子任务：

```text
description = "检索近两年关于多智能体 Deep Research 的论文"
```

`ResearcherAgent._build_task_prompt()` 会更倾向推荐哪个工具？

参考答案：

因为描述里有“论文”，命中 academic keywords，首选工具会偏向 `arxiv_reader`，而不是普通 `web_search`。

## 13. 常见面试问法

Q：这个项目的 tool calling 是怎么实现的？

A：工具对象提供 `get_openai_tool_schema()`，ResearcherAgent 在运行前调用 `policy.set_tools(schemas)` 把工具 schema 注册给 LLM。LLM 返回 `tool_calls` 后，Agent 解析工具名和 JSON 参数，调用对应工具的 `execute()`，再把工具结果作为 tool message 加回上下文，让模型继续推理或总结。

Q：真实搜索失败怎么办？

A：工具会返回 error 字段，ResearcherAgent 检测到 error 后把任务标记为 failed。系统还支持 Mock 工具，适合无网络或无 API Key 调试。

Q：为什么 web_search 和 browser 都需要？

A：web_search 用来找候选链接和摘要，browser 用来打开具体 URL 读正文。前者是“找资料”，后者是“读资料”。

## 14. 证据

- Agent 基类：`src/agents/base_agent.py`
- Tool-calling 主循环：`src/agents/researcher.py`
- 工具导出：`src/tools/__init__.py`
- 搜索工具：`src/tools/web_search.py`
- 浏览器工具：`src/tools/browser.py`
- 工具创建：`src/core/runner.py::_create_tools_factory`
- 工具配置：`.env.template`, `configs/default.yaml`

