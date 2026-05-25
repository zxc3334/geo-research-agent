# 05. 模块：模型路由、Memory 与上下文压缩

## 1. 模块职责

这个模块解决三个问题：

1. 不同 Agent 用哪个 LLM 后端？
2. 多个子任务产生的信息怎么共享和持久化？
3. 上下文太长时怎么压缩，避免超过模型上下文窗口？

对应模块：

- `ModelRouter` / `VLLMPolicy`：模型后端统一封装。
- `SharedMemoryStore` / `LongTermMemory`：SQLite + embedding 记忆。
- `ContextCompressor`：长上下文压缩。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `src/models/model_router.py` | 根据后端名创建 LLM policy |
| `src/models/vllm_policy.py` | OpenAI-compatible API 封装，支持工具、截断、错误处理 |
| `src/utils/env_config.py` | 加载 `.env` 和 `.env.local` |
| `src/memory/long_term.py` | SQLite 表结构和 `MemoryEntry` |
| `src/memory/memory_store.py` | 去重、矛盾检测、向量检索、上下文组装 |
| `src/memory/embedder.py` | sentence-transformers 向量化 |
| `src/compressor/compressor.py` | 上下文压缩主入口 |
| `src/compressor/extractive.py` | TextRank/句子抽取 |
| `configs/default.yaml` | 模型映射、memory、compressor 配置 |

## 3. 模型路由

`ModelRouter.create_backend()` 的职责：

1. 调用 `ensure_env_loaded()`。
2. 从 `.env` 读取 `{BACKEND}_API_KEY`、`{BACKEND}_BASE_URL`、`{BACKEND}_MODEL`。
3. 合并 `configs/default.yaml` 传入的 temperature、max_tokens。
4. 创建 `VLLMPolicy`。
5. 用 `_BACKEND_CACHE` 缓存，避免重复创建 client。

简化流程：

```python
name = backend_name or get_env("DEFAULT_LLM_BACKEND", "vllm")
config = ModelRouter._load_backend_config(name)
config.update(override_kwargs)
policy = VLLMPolicy(**config)
```

## 4. VLLMPolicy 做了什么

虽然叫 `VLLMPolicy`，但它实际是 OpenAI-compatible API 封装，不只服务 vLLM。

核心能力：

| 能力 | 代码位置 | 用途 |
|---|---|---|
| OpenAI client | `OpenAI(base_url=..., api_key=...)` | 兼容 DeepSeek、MiMo、OpenAI、vLLM |
| tool schema | `set_tools()` | 注册 function calling 工具 |
| 消息清洗 | `__call__()` 开头 | 避免 role/content 格式错误 |
| 主动截断 | `_truncate_messages()` | 上下文过长时保留 system + 最近交互 |
| 工具调用解析 | `raw_msg.tool_calls` + 正则回退 | 解析模型返回的 function call |
| 错误分类 | `except Exception` | context length 直接抛 RuntimeError，其他错误返回假 assistant |

## 5. Memory 数据结构

`MemoryEntry` 是长期记忆的基本单位：

```python
@dataclass
class MemoryEntry:
    entry_id: str
    claim: str
    source: str
    confidence: float
    agent_id: str
    timestamp: float
    evidence_type: str
    embedding: list[float]
    topic: str
    metadata: dict[str, Any]
    session_id: str = ""
```

读法：

- `claim`：要保存的一句话信息。
- `source`：来自哪个任务/URL/模块。
- `confidence`：置信度。
- `embedding`：向量表示，用来相似度检索。
- `session_id`：不同会话隔离。

SQLite 表在 `LongTermMemory._ensure_tables()` 里创建：

```sql
CREATE TABLE IF NOT EXISTS entries (...)
CREATE TABLE IF NOT EXISTS conflicts (...)
```

## 6. SharedMemoryStore 的写入流程

`SharedMemoryStore.put(entry)`：

```text
质量过滤
如果没有 embedding，自动生成
查重 cosine > 0.92
如果重复，合并或保留旧 entry
写入 SQLite
加入内存向量索引
检测潜在矛盾
```

关键思想：

- SQLite 负责持久化。
- numpy 矩阵负责快速相似度。
- `threading.RLock` 保护内存索引。

## 7. Memory 如何被 Orchestrator 使用

在 `Orchestrator._do_collecting()` 中：

```python
if r.status == AgentStatus.SUCCESS and r.output:
    self._sync_result_to_memory_store(r)
```

在 `_sync_result_to_memory_store()` 中，会把 `AgentResult.output` 的前 500 字包装成 `MemoryEntry`。

在下一次规划前：

```python
ctx = self.memory_store.get_context_for_query(self._query, max_tokens=2000)
```

这表示 Planner 可以拿到与 query 相关的历史记忆。

## 8. 上下文压缩

配置：

```yaml
compressor:
  max_context_length: 16000
  output_reserve_tokens: 2048
  l1_threshold: 0.6
  l2_threshold: 0.8
  l3_threshold: 0.95
  embedding_model: "all-MiniLM-L6-v2"
```

Orchestrator 中的使用方式：

```python
if total_chars > 6000:
    compressed = self.compressor.compress(texts=parts, query=self._query)
```

第一遍只要理解：上下文太长时，不是简单截断，而是根据 query 相关性压缩。

## 9. 配置如何影响这个模块

### 模型分工

```yaml
backend_mapping:
  solver: "deepseek"
  planner: "deepseek"
  judge: "mimo"
  red_agent: "mimo"
```

这让不同模块用不同模型，体现“模型路由”。

### Memory

```yaml
memory:
  db_path: "data/memory.db"
  max_entries: 10000
  similarity_threshold_dup: 0.92
```

当前代码中 `SharedMemoryStore` 内部也定义了 `_DEDUP_THRESHOLD = 0.92`。你面试可以说：配置文件表达了设计意图，当前实现部分阈值仍在代码常量中，有进一步统一配置的空间。

### Compressor

`max_context_length` 和 `output_reserve_tokens` 会影响压缩预算。

## 10. 需要掌握的 Python 语法

### 静态方法

`ModelRouter.create_backend()` 是 `@staticmethod`，表示不需要实例化 `ModelRouter`。

```python
policy = ModelRouter.create_backend("deepseek")
```

### 类级缓存

```python
_BACKEND_CACHE: dict[str, VLLMPolicy] = {}
```

这是模块级全局缓存，用来复用 policy。

### SQLite Row 转对象

`MemoryEntry.from_row()` 是类方法：

```python
@classmethod
def from_row(cls, row: sqlite3.Row) -> MemoryEntry:
```

`cls(...)` 表示创建当前类实例。

### 线程锁

```python
self._lock = threading.RLock()
with self._lock:
    ...
```

虽然主流程用 asyncio，但 SQLite/向量索引是同步结构，使用线程锁保护共享数据。

## 11. 第一遍、第二遍、面试读法

第一遍：

- 看 `model_router.py::create_backend()`。
- 看 `env_config.py::ensure_env_loaded()`。
- 看 `MemoryEntry` 字段。
- 看 `SharedMemoryStore.put()` 的注释和流程。

第二遍：

- 看 `VLLMPolicy.__call__()` 如何清洗 messages、注册 tools、解析 tool calls。
- 看 `LongTermMemory._ensure_tables()` 建了哪些表。
- 看 `SharedMemoryStore.query_by_similarity()` 怎么检索。

面试读法：

- 强调模型路由：不同模块不同模型、不同采样参数。
- 强调 Memory：SQLite 持久化 + embedding 检索 + 去重/冲突。
- 强调 Compressor：不是简单截断，而是语义相关性保留。

## 12. 小练习

如果 `.env.local` 中配置：

```env
DEFAULT_LLM_BACKEND=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

而 `configs/default.yaml` 中：

```yaml
backend_mapping:
  judge: "mimo"
```

请问：

1. 默认模块使用哪个后端？
2. judge 使用哪个后端？
3. 如果 `MIMO_API_KEY` 没配，初始化 judge policy 会发生什么？

参考答案：

1. 默认使用 deepseek。
2. judge 使用 mimo。
3. `ModelRouter._load_backend_config("mimo")` 找不到 API Key/Base URL 时会抛 `ValueError`，初始化失败。

## 13. 常见面试问法

Q：为什么要做 ModelRouter？

A：不同模块对模型能力和成本要求不同。Planner 需要稳定 JSON 输出，solver 需要强推理，judge/red/blue 可以用更稳定或更便宜的模型。ModelRouter 把后端切换和 API Key 配置集中化，避免在业务代码里到处写模型连接逻辑。

Q：Memory 为什么不用纯内存 dict？

A：纯内存进程退出就没了，也不能跨 session 复用。这里用 SQLite 持久化，用 embedding 做语义检索，适合本地 Agent 的轻量长期记忆。

Q：上下文压缩为什么重要？

A：DeepResearch 会产生多个子任务结果，直接塞给 LLM 容易超过上下文窗口，也会让关键信息被噪声淹没。压缩模块可以保留与 query 更相关的内容。

## 14. 证据

- 模型路由：`src/models/model_router.py`
- LLM 封装：`src/models/vllm_policy.py`
- 环境变量加载：`src/utils/env_config.py`
- Memory 数据结构：`src/memory/long_term.py`
- Memory 逻辑：`src/memory/memory_store.py`
- 压缩配置：`configs/default.yaml`
- Orchestrator 使用 memory/compressor：`src/orchestrator/orchestrator.py`

