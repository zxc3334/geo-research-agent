"""
Deep Research Agent — 核心编排器 (M1: Multi-Agent Orchestrator)

9 状态状态机驱动的异步任务编排引擎：
  IDLE → PLANNING → DISPATCHING → COLLECTING → SYNTHESIZING → ADVERSARIAL → DONE
  失败时进入 REPLANNING，最终可进入 FAILED。

设计亮点:
  - 自研 asyncio + DAG executor，不依赖 LangGraph/AutoGen
  - 拓扑排序后按层并发执行，Semaphore 控制最大并发度
  - 三级降级策略：单任务超时→标记继续；>50%失败→re-plan；全局超时→强制合成
  - 状态机用字典映射实现，便于扩展新状态和转换逻辑
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

from .schemas import (
    OrchestratorState,
    SubTask,
    AgentResult,
    AgentStatus,
    ResearchReport,
    RunConfig,
    TaskType,
)
from .agent_pool import AgentPool
from ..planner.dag import DAG
from ..planner.planner import Planner, PlanParseError
from ..planner.budget_tracker import BudgetTracker
from ..evidence import EvidenceStore
from ..utils.tracing import trace_chain

# M4: Memory Store 类型提示（延迟导入避免循环依赖）
SharedMemoryStore = Any


__all__ = ["Orchestrator"]


class Orchestrator:
    """Deep Research Agent 核心编排器。

    Attributes:
        planner: 自适应规划器，负责初始规划和增量重规划。
        agent_pool: Agent 对象池，管理 worker agent 生命周期。
        budget_tracker: Token 预算追踪器。
        memory_store: 全局共享内存，存储所有子任务结果和中间上下文。
        compressor: （预留）上下文压缩器接口。
    """

    def __init__(
        self,
        planner: Planner,
        agent_pool: AgentPool,
        budget_tracker: BudgetTracker | None = None,
        compressor: Any | None = None,
        adversarial_loop: Any | None = None,
        memory_store: Any | None = None,
        summarizer_policy: Any | None = None,
        trace_recorder: Any | None = None,
        context_modifier: Any | None = None,
        wiki_store: Any | None = None,
        progress_callback: Any | None = None,
    ) -> None:
        self.planner = planner
        self.agent_pool = agent_pool
        self.budget_tracker = budget_tracker or BudgetTracker()
        self.compressor = compressor
        self.adversarial_loop = adversarial_loop
        self.memory_store = memory_store
        self.summarizer_policy = summarizer_policy
        self.trace_recorder = trace_recorder
        self.context_modifier = context_modifier  # Optional callback(ctx, task) -> ctx
        self.wiki_store = wiki_store  # M7: Wiki Knowledge Base
        self.progress_callback = progress_callback  # Optional callback(data: dict)
        self.evidence_store = EvidenceStore()

        # 运行时状态（保留 dict 作为快速缓存，M4 提供持久化 + 语义检索）
        self._memory_store: dict[str, Any] = {}
        self._results: list[AgentResult] = []
        self._synthesis_result: AgentResult | None = None
        self._dag: DAG | None = None
        self._task_map: dict[str, SubTask] = {}
        self._current_state = OrchestratorState.IDLE
        self._query: str = ""
        self._config: RunConfig = RunConfig()
        self._start_time: float = 0.0
        self._replan_count: int = 0
        self._adversarial_count: int = 0

    def _emit_progress(self, **kwargs) -> None:
        """通过 progress_callback 发布进度事件到前端。"""
        if self.progress_callback:
            try:
                self.progress_callback(kwargs)
            except Exception:
                pass  # 不让回调错误影响主流程

        # 状态机处理器映射
        self._state_handlers: dict[OrchestratorState, Callable[[], asyncio.Future[OrchestratorState]]] = {
            OrchestratorState.IDLE: self._on_idle,
            OrchestratorState.PLANNING: self._do_planning,
            OrchestratorState.DISPATCHING: self._do_dispatching,
            OrchestratorState.COLLECTING: self._do_collecting,
            OrchestratorState.SYNTHESIZING: self._do_synthesizing,
            OrchestratorState.ADVERSARIAL: self._do_adversarial,
            OrchestratorState.REPLANNING: self._do_replanning,
            OrchestratorState.DONE: self._on_done,
            OrchestratorState.FAILED: self._on_failed,
        }

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @trace_chain(name="orchestrator.run", tags=["m1", "orchestrator"])
    async def run(self, query: str, config: RunConfig | None = None) -> ResearchReport:
        """主入口：执行完整的研究流程。

        Args:
            query: 研究问题。
            config: 运行配置，默认使用 RunConfig()。

        Returns:
            ResearchReport: 最终研究报告。
        """
        self._query = query
        self._config = config or RunConfig()
        self._start_time = time.monotonic()
        self._replan_count = 0
        self._adversarial_count = 0
        self._memory_store.clear()
        self._results.clear()
        self._synthesis_result = None
        self._dag = None
        self._task_map.clear()
        self._current_state = OrchestratorState.IDLE
        if self.trace_recorder:
            self.trace_recorder.record(
                "run_start",
                query=query,
                max_concurrent=self._config.max_concurrent,
                global_timeout_seconds=self._config.global_timeout_seconds,
            )

        # 状态机主循环
        while self._current_state not in (OrchestratorState.DONE, OrchestratorState.FAILED):
            # 全局超时检查
            if self._is_global_timeout():
                if self._current_state in (
                    OrchestratorState.COLLECTING,
                    OrchestratorState.SYNTHESIZING,
                    OrchestratorState.ADVERSARIAL,
                ):
                    # 强制合成：用已有结果生成报告
                    self._current_state = OrchestratorState.SYNTHESIZING
                else:
                    self._current_state = OrchestratorState.FAILED
                break

            handler = self._state_handlers.get(self._current_state)
            if handler is None:
                raise RuntimeError(f"Unknown state: {self._current_state}")

            next_state = await handler()
            self._current_state = next_state

            logger.info(f"[Orchestrator] State transition: {self._current_state.value}")
            if self.trace_recorder:
                self.trace_recorder.record(
                    "state_transition",
                    state=self._current_state.value,
                    elapsed_seconds=round(time.monotonic() - self._start_time, 3),
                )

        # 返回结果
        if self._current_state == OrchestratorState.DONE:
            # 最终报告应在 memory 中
            report = self._memory_store.get("final_report")
            if report is None:
                report = ResearchReport(query=query, content="Report generation failed unexpectedly.")
            report.num_replan = self._replan_count
            report.adversarial_rounds = self._adversarial_count
            if self.trace_recorder:
                self.trace_recorder.record(
                    "run_end",
                    status="done",
                    confidence=report.confidence,
                    num_searches=report.num_searches,
                    num_replan=report.num_replan,
                    adversarial_rounds=report.adversarial_rounds,
                    usage=self._sum_usage(
                        self._results + ([self._synthesis_result] if self._synthesis_result else [])
                    ),
                )

            # M4: 将最终报告存入 SharedMemoryStore
            if self.memory_store is not None:
                try:
                    from src.memory.long_term import MemoryEntry
                    entry = MemoryEntry(
                        entry_id=f"final_report:{int(time.time())}",
                        claim=str(report.content)[:800],
                        source="orchestrator",
                        confidence=report.confidence,
                        agent_id="orchestrator",
                        timestamp=time.time(),
                        evidence_type="primary",
                        embedding=[],
                        topic=query[:50],
                        metadata={
                            "num_searches": report.num_searches,
                            "num_replan": report.num_replan,
                            "adversarial_rounds": report.adversarial_rounds,
                        },
                    )
                    self.memory_store.put(entry)
                    logger.info(f"[M4] Final report stored to memory (confidence={report.confidence:.2f})")
                except Exception as e:
                    logger.warning(f"[M4] Failed to store final report: {e}")

            # Wiki: 质量门槛 → 只有高质量报告才 ingest 到知识库
            # 失败/降级报告不写入 wiki（避免污染知识库）
            # 失败经验已通过 _sync_failure_to_memory_store 记录到 M4
            wiki_quality_gate = (
                report.confidence >= 0.3
                and any(r.status == AgentStatus.SUCCESS for r in self._results)
            )
            if self.wiki_store and report.content and wiki_quality_gate:
                try:
                    self.wiki_store.save_raw(report.content, query)
                    logger.info("[Wiki] Raw report saved")
                    # Trigger ingest (sync, safe for thread-pool context)
                    from src.wiki.ingest import WikiIngest
                    ingest = WikiIngest(self.wiki_store)
                    all_evidence = []
                    for r in self._results:
                        all_evidence.extend(r.evidence_items)
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(ingest.ingest(
                            report.content, query=query, evidence_items=all_evidence
                        ))
                    except RuntimeError:
                        asyncio.run(ingest.ingest(
                            report.content, query=query, evidence_items=all_evidence
                        ))
                    logger.info("[Wiki] Ingest triggered")
                except Exception as e:
                    logger.error(f"[Wiki] Save/ingest failed: {e}", exc_info=True)
            elif self.wiki_store and report.content:
                logger.info(
                    f"[Wiki] 质量门槛未达标 (confidence={report.confidence:.2f}), 跳过 ingest"
                )

            return report

        # FAILED 状态
        return ResearchReport(
            query=query,
            content="Research failed due to persistent errors or global timeout.",
            num_replan=self._replan_count,
            adversarial_rounds=self._adversarial_count,
        )

    # ------------------------------------------------------------------
    # 状态机处理器
    # ------------------------------------------------------------------

    async def _on_idle(self) -> OrchestratorState:
        """从 IDLE 自动进入 PLANNING。"""
        return OrchestratorState.PLANNING

    async def _do_planning(self) -> OrchestratorState:
        """调用 Planner 生成初始 DAG。

        失败时直接转入 FAILED（初始计划失败无法恢复）。
        """
        try:
            # Build context: memory + wiki
            memory_ctx = self._build_memory_context()
            wiki_ctx = ""
            if self.wiki_store:
                try:
                    wiki_index = self.wiki_store.get_index()
                    wiki_pages = self.wiki_store.get_context(self._query)
                    if wiki_index or wiki_pages:
                        parts = []
                        if wiki_index:
                            parts.append(f"Wiki Index:\n{wiki_index}")
                        if wiki_pages:
                            parts.append(f"Relevant Wiki Pages:\n{wiki_pages}")
                        wiki_ctx = "\n\n".join(parts)
                except Exception as e:
                    logger.info(f"[Wiki] Context retrieval failed: {e}")

            combined_ctx = memory_ctx
            if wiki_ctx:
                combined_ctx = f"{memory_ctx}\n\n{wiki_ctx}" if memory_ctx else wiki_ctx

            self._dag = self.planner.generate_plan(self._query, combined_ctx)
            # 从 planner 获取完整的 SubTask 信息（包括 description、search_hints 等）
            self._task_map = self.planner.get_task_map_from_dag(self._dag, self.planner._last_raw_json)
            if not self._task_map:
                # 降级：如果解析失败，使用占位符
                self._task_map = self._rebuild_task_map_from_dag()
        except PlanParseError as e:
            logger.info(f"[Planning] Failed: {e}")
            return OrchestratorState.FAILED
        except Exception as e:
            logger.info(f"[Planning] Unexpected error: {e}")
            return OrchestratorState.FAILED

        n_tasks = len(self._dag)
        n_layers = len(self._dag.get_parallel_groups()) if self._dag else 0
        logger.info(f"[Planning] ✓ DAG 生成完成: {n_tasks} 个子任务, {n_layers} 个执行层")
        # 发布规划结果到前端
        task_list = []
        for tid, task in self._task_map.items():
            logger.info(f"[Planning]   {tid}: {task.description}")
            task_list.append({"id": tid, "description": task.description[:200]})
            if self.trace_recorder:
                self.trace_recorder.record(
                    "task_planned",
                    task_id=tid,
                    task_type=task.task_type.value,
                    description=task.description,
                    dependencies=task.dependencies,
                    timeout_seconds=task.timeout_seconds,
                )
        self._emit_progress(
            phase="planning", status="running",
            total_subtasks=n_tasks, completed_subtasks=0,
            task_list=task_list,
        )
        return OrchestratorState.DISPATCHING

    async def _do_dispatching(self) -> OrchestratorState:
        """拓扑排序 + 并发调度 sub-agents。

        核心逻辑:
          1. 获取并行执行层 (parallel groups)
          2. 每层内用 asyncio.gather + Semaphore 并发执行
          3. 每个 sub-task 设置单独超时 (asyncio.wait_for)
          4. 收集结果到 self._results
        """
        if self._dag is None or len(self._dag) == 0:
            return OrchestratorState.COLLECTING

        semaphore = asyncio.Semaphore(self._config.max_concurrent)
        parallel_groups = self._dag.get_parallel_groups()
        all_results: list[AgentResult] = []

        for layer_idx, group in enumerate(parallel_groups):
            logger.info(f"[Dispatch] ▶ Layer {layer_idx + 1}/{len(parallel_groups)}: {group} (并行执行)")

            # 构建本层的 coroutine 列表
            async def _run_one(task_id: str, _sem=semaphore) -> AgentResult:
                logger.debug(f"[Dispatch]   ▶ _run_one START: {task_id}")
                self._emit_progress(
                    phase="researching", status="running",
                    current_task=task_id,
                    current_task_desc=getattr(self._task_map.get(task_id), "description", "")[:150],
                )
                async with _sem:
                    subtask = self._task_map.get(task_id)
                    if subtask is None:
                        return AgentResult(
                            task_id=task_id,
                            status=AgentStatus.FAILED,
                            output=f"SubTask '{task_id}' not found in task_map",
                        )

                    # 准备上下文：先执行依赖任务的结果
                    context = await self._build_task_context(subtask)
                    if self.trace_recorder:
                        self.trace_recorder.record(
                            "task_start",
                            task_id=subtask.task_id,
                            task_type=subtask.task_type.value,
                            description=subtask.description,
                            dependencies=subtask.dependencies,
                        )

                    # 获取 Agent
                    agent = await self.agent_pool.get_agent(subtask.task_type)
                    try:
                        # 设置单任务超时
                        result = await asyncio.wait_for(
                            agent.run(subtask, context),
                            timeout=subtask.timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        result = AgentResult(
                            task_id=task_id,
                            status=AgentStatus.TIMEOUT,
                            output=f"Task timed out after {subtask.timeout_seconds}s",
                        )
                    except Exception as e:
                        result = AgentResult(
                            task_id=task_id,
                            status=AgentStatus.FAILED,
                            output=f"Exception: {type(e).__name__}: {e}",
                        )
                    finally:
                        await self.agent_pool.release_agent(agent)

                    if self.trace_recorder:
                        self.trace_recorder.record(
                            "task_end",
                            task_id=result.task_id,
                            status=result.status.value,
                            confidence=result.confidence,
                            token_usage=result.token_usage,
                            trajectory_steps=len(result.trajectory),
                        )
                    return result

            # 并发执行本层
            coros = [_run_one(tid) for tid in group]
            logger.debug(f"[Dispatch]   Gathering {len(coros)} coroutines...")
            layer_results = await asyncio.gather(*coros, return_exceptions=True)
            logger.debug(f"[Dispatch]   Gather done: {len(layer_results)} results, types: {[type(r).__name__ for r in layer_results]}")

            for lr in layer_results:
                if isinstance(lr, Exception):
                    all_results.append(AgentResult(
                        task_id="unknown",
                        status=AgentStatus.FAILED,
                        output=f"Dispatch exception: {lr}",
                    ))
                else:
                    all_results.append(lr)

            # 发布层完成进度
            completed = sum(1 for r in all_results if not isinstance(r, Exception))
            total = len(self._task_map)
            self._emit_progress(
                phase="researching", status="running",
                completed_subtasks=completed, total_subtasks=total,
                current_task=f"Layer {layer_idx + 1}/{len(parallel_groups)} 完成",
            )

            # 层间钩子：允许等待用户输入、发布进度等
            if self.context_modifier is not None:
                try:
                    # 注入当前层的结果到 memory_store，供 context_modifier 读取
                    for lr in all_results:
                        if not isinstance(lr, Exception):
                            self._memory_store[f"result:{lr.task_id}"] = lr
                    # 调用钩子（可用于等待用户输入）
                    import inspect
                    if hasattr(self.context_modifier, '__call__'):
                        result = self.context_modifier({}, None)
                        if inspect.isawaitable(result):
                            await result
                except Exception as e:
                    logger.debug(f"[Hook] context_modifier error: {e}")

        self._results = all_results
        return OrchestratorState.COLLECTING

    async def _do_collecting(self) -> OrchestratorState:
        """收集结果，写入 memory，检查是否需要重规划。

        三级降级策略检查点:
          - 单任务超时/失败：已在 dispatch 层处理（标记状态，继续执行）
          - >50% 失败：触发 REPLANNING
          - 全局超时：由外层 run() 的循环检查处理
        """
        # 将结果写入运行时 memory dict
        for r in self._results:
            self.evidence_store.annotate_result(r, task=self._task_map.get(r.task_id))
            self._memory_store[f"result:{r.task_id}"] = r
            if self.trace_recorder:
                for item in r.evidence_items:
                    self.trace_recorder.record(
                        "evidence_item",
                        task_id=r.task_id,
                        level=item.level.value,
                        source_tier=getattr(item, "source_tier", None) and item.source_tier.value,
                        claim=item.claim,
                        source=item.source,
                        rationale=item.rationale,
                        confidence=item.confidence,
                        source_count=getattr(item, "source_count", 0),
                        source_type=item.metadata.get("source_type", ""),
                        metadata=item.metadata,
                    )

        # M4: 将结果同步写入 SharedMemoryStore（成功 + 失败）
        if self.memory_store is not None:
            for r in self._results:
                if r.status == AgentStatus.SUCCESS and r.output:
                    self._sync_result_to_memory_store(r)
                elif r.status in (AgentStatus.FAILED, AgentStatus.TIMEOUT):
                    self._sync_failure_to_memory_store(r)

        success_count = sum(1 for r in self._results if r.status == AgentStatus.SUCCESS)
        total_count = len(self._results)
        fail_count = total_count - success_count
        status_icon = "✓" if success_count == total_count else "⚠"
        if fail_count > 0:
            logger.info(f"[Collect] {status_icon} 子任务完成: {success_count}/{total_count} 成功 ({fail_count} 失败)")
        else:
            logger.info(f"[Collect] {status_icon} 子任务完成: {success_count}/{total_count} 成功")

        # 检查是否需要重规划
        if self._should_replan(self._results):
            if self._replan_count < self._config.max_replan_rounds:
                self._replan_count += 1
                return OrchestratorState.REPLANNING
            else:
                logger.info("[Collect] Max replan rounds reached, proceeding with partial results")
                # 超过最大重规划次数，继续合成（用已有结果）

        return OrchestratorState.SYNTHESIZING

    def _sync_result_to_memory_store(self, result: AgentResult) -> None:
        """将 AgentResult 同步到 M4 SharedMemoryStore。

        提取 output 中的关键 claim 作为记忆条目，支持后续语义检索。
        """
        try:
            # 延迟导入避免循环依赖
            from src.memory.long_term import MemoryEntry
            evidence_items = result.evidence_items or self.evidence_store.build_evidence_items(
                result, task=self._task_map.get(result.task_id)
            )
            result.evidence_items = evidence_items
            primary_evidence = self._select_primary_evidence(evidence_items)
            claim_text = primary_evidence.claim if primary_evidence else str(result.output)[:500]
            memory_evidence_type = self._memory_evidence_type(primary_evidence)
            entry = MemoryEntry(
                entry_id=result.task_id,
                claim=claim_text,
                source=f"task:{result.task_id}",
                confidence=getattr(result, "confidence", 0.5),
                agent_id=result.task_id,
                timestamp=time.time(),
                evidence_type=memory_evidence_type,
                embedding=[],  # SharedMemoryStore.put() 会自动生成 embedding
                topic=self._query[:50],
                metadata={
                    "status": result.status.value,
                    "token_usage": getattr(result, "token_usage", 0),
                    "evidence_items": [item.to_dict() for item in evidence_items],
                    "evidence_level": primary_evidence.level.value if primary_evidence else "",
                },
            )
            self.memory_store.put(entry)
            level = primary_evidence.level.value if primary_evidence else "unknown"
            logger.info(f"[M4] Memory stored: {result.task_id} (evidence={level}, claim={claim_text[:60]}...)")
        except Exception as e:
            logger.warning(f"[M4] Failed to store memory for {result.task_id}: {e}")

    def _classify_error(self, output: str) -> str:
        """从错误输出中提取错误类型（纯代码，无 LLM）。"""
        text = (output or "").lower()
        if "timeout" in text or "timed out" in text:
            return "超时"
        if "rate limit" in text or "429" in text:
            return "API限流"
        if "connection" in text or "network" in text:
            return "网络错误"
        if "traceback" in text or "exception" in text:
            return "代码异常"
        if "api" in text or "500" in text or "503" in text:
            return "API错误"
        return "未知错误"

    def _sync_failure_to_memory_store(self, result: AgentResult) -> None:
        """将失败结果精简写入 M4（~150 chars，零 LLM 调用）。"""
        try:
            from src.memory.long_term import MemoryEntry
            subtask = self._task_map.get(result.task_id)
            task_desc = subtask.description[:100] if subtask else result.task_id
            error_type = self._classify_error(result.output)
            claim = f"任务失败: {task_desc} (原因: {error_type})"
            entry = MemoryEntry(
                entry_id=f"fail:{result.task_id}",
                claim=claim,
                source=f"task:{result.task_id}",
                confidence=0.2,
                agent_id=result.task_id,
                timestamp=time.time(),
                evidence_type="failure",
                embedding=[],
                topic=self._query[:50],
                metadata={
                    "status": "failed",
                    "error_type": error_type,
                    "task_description": task_desc,
                },
            )
            self.memory_store.put(entry)
            logger.info(f"[M4] Failure memory stored: {result.task_id} ({error_type})")
        except Exception as e:
            logger.warning(f"[M4] Failed to store failure memory for {result.task_id}: {e}")

    async def _do_synthesizing(self) -> OrchestratorState:
        """调用 SummarizerAgent 合成研究报告。"""
        # 创建合成任务
        synth_task = SubTask(
            task_id="synthesize_final",
            task_type=TaskType.SYNTHESIS,
            description="Synthesize all sub-task results into a final research report.",
            timeout_seconds=300,
        )

        context = {
            "query": self._query,
            "results": self._results,
            "domain": getattr(self.planner, "domain", "general"),
            "evidence_summary": self.evidence_store.summarize(self._results),
        }

        agent = await self.agent_pool.get_agent(TaskType.SYNTHESIS)
        # 兼容旧池实现：如果外部注入的 AgentPool 未支持 SYNTHESIS，则强制创建 SummarizerAgent。
        from ..agents.summarizer import SummarizerAgent
        if not isinstance(agent, SummarizerAgent):
            # 优先使用配置的 summarizer_policy（更大的 max_tokens），fallback 到 agent.policy
            policy = self.summarizer_policy or agent.policy
            agent = SummarizerAgent(name="summarizer", policy=policy, tools=agent.tools, pool_type_key=TaskType.SYNTHESIS.value)

        try:
            result = await asyncio.wait_for(
                agent.run(synth_task, context),
                timeout=synth_task.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = AgentResult(
                task_id="synthesize_final",
                status=AgentStatus.TIMEOUT,
                output="Synthesis timed out",
            )
        except Exception as e:
            result = AgentResult(
                task_id="synthesize_final",
                status=AgentStatus.FAILED,
                output=f"Synthesis error: {type(e).__name__}: {e}",
            )
        finally:
            await self.agent_pool.release_agent(agent)

        self._synthesis_result = result

        if result.status == AgentStatus.SUCCESS and isinstance(result.output, ResearchReport):
            self._memory_store["final_report"] = result.output
        else:
            # 合成 LLM 失败但已有子任务结果时，生成确定性降级报告，避免把错误字符串当最终产物。
            self._memory_store["final_report"] = self._build_fallback_report(result)
        if self.trace_recorder:
            final_report = self._memory_store.get("final_report")
            self.trace_recorder.record(
                "synthesis_end",
                status=result.status.value,
                confidence=getattr(final_report, "confidence", 0.0),
                fallback_used=result.status != AgentStatus.SUCCESS,
                output_type=type(getattr(result, "output", None)).__name__,
            )

        if self._config.enable_adversarial:
            logger.info("[Synthesize] ✓ 报告合成完成，进入对抗优化")
            self._emit_progress(phase="adversarial", status="running", current_task="对抗优化中")
            return OrchestratorState.ADVERSARIAL
        logger.info("[Synthesize] ✓ 报告合成完成")
        report = self._memory_store.get("final_report")
        self._emit_progress(
            phase="completed", status="completed",
            completed_subtasks=len(self._task_map),
            total_subtasks=len(self._task_map),
            confidence=getattr(report, "confidence", 0.0),
        )
        return OrchestratorState.DONE

    async def _do_adversarial(self) -> OrchestratorState:
        """M5: Red-Blue 对抗降噪循环。

        调用 AdversarialLoop 对报告进行 challenge-verify 迭代优化。
        仅在报告置信度低于阈值时触发，避免资源浪费。
        """
        report = self._memory_store.get("final_report")
        if report is None:
            return OrchestratorState.DONE

        # 置信度足够高时跳过对抗
        if report.confidence >= 0.8:
            logger.info("[Adversarial] ✓ 报告置信度已达标 (≥0.8)，跳过对抗优化")
            return OrchestratorState.DONE

        if self.adversarial_loop is None:
            logger.info("[Adversarial] AdversarialLoop 未配置，跳过")
            return OrchestratorState.DONE

        try:
            logger.info(f"[Adversarial] ▶ 启动 Red-Blue 对抗优化 (当前置信度={report.confidence:.2f})")
            optimized_report, history = await self.adversarial_loop.run(report)
            self._memory_store["final_report"] = optimized_report
            self._adversarial_count += len(history)
            logger.info(f"[Adversarial] ✓ 对抗优化完成: {len(history)} 轮, 最终置信度={optimized_report.confidence:.2f}")
        except Exception as e:
            logger.info(f"[Adversarial] ✗ 对抗优化失败: {e}，使用原始报告")

        return OrchestratorState.DONE

    async def _do_replanning(self) -> OrchestratorState:
        """触发增量重规划。

        保留 confidence≥0.6 的成功结果，修改失败子问题。
        """
        failed_tasks = []
        for r in self._results:
            if r.status != AgentStatus.SUCCESS:
                st = self._task_map.get(r.task_id)
                if st:
                    failed_tasks.append(st)

        reason = self._build_failure_reason(self._results)
        logger.info(f"[Replan] Round {self._replan_count}/{self._config.max_replan_rounds}. Failed tasks: {[t.task_id for t in failed_tasks]}")

        try:
            new_dag = self.planner.replan(
                query=self._query,
                failed_tasks=failed_tasks,
                existing_results=self._results,
                reason=reason,
            )
            self._dag = new_dag
            self._task_map = self.planner.get_task_map_from_dag(self._dag, self.planner._last_raw_json)
            if not self._task_map:
                self._task_map = self._rebuild_task_map_from_dag()
            # 清空上一轮结果（保留在 memory 中，新任务可通过 context_keys 引用）
            self._results = []
        except PlanParseError as e:
            logger.info(f"[Replan] Failed: {e}")
            # 重规划失败，如果已有部分成功结果，尝试直接合成
            if any(r.status == AgentStatus.SUCCESS for r in self._results):
                return OrchestratorState.SYNTHESIZING
            return OrchestratorState.FAILED
        except Exception as e:
            logger.info(f"[Replan] Unexpected error: {e}")
            if any(r.status == AgentStatus.SUCCESS for r in self._results):
                return OrchestratorState.SYNTHESIZING
            return OrchestratorState.FAILED

        return OrchestratorState.DISPATCHING

    async def _on_done(self) -> OrchestratorState:
        """终态，不应再转换。"""
        return OrchestratorState.DONE

    async def _on_failed(self) -> OrchestratorState:
        """终态，不应再转换。"""
        return OrchestratorState.FAILED

    # ------------------------------------------------------------------
    # 决策逻辑
    # ------------------------------------------------------------------

    def _should_replan(self, results: list[AgentResult]) -> bool:
        """判断是否需要重规划。

        策略:
          - 失败率 > 50% 时触发
          - 或存在任何 TIMEOUT 且成功结果不足 30%
        """
        if not results:
            return False
        total = len(results)
        failed = sum(1 for r in results if r.status in (AgentStatus.FAILED, AgentStatus.TIMEOUT))
        success = sum(1 for r in results if r.status == AgentStatus.SUCCESS)

        failure_rate = failed / total
        if failure_rate > 0.5:
            return True
        if success / total < 0.3 and failed > 0:
            return True
        return False

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _is_global_timeout(self) -> bool:
        """检查是否超过全局超时。"""
        elapsed = time.monotonic() - self._start_time
        return elapsed > self._config.global_timeout_seconds

    def _build_memory_context(self) -> str:
        """构建给 planner 的上下文摘要。

        优先使用 M4 SharedMemoryStore 的语义检索（如果已接入），
        否则回退到运行时 dict 遍历。
        """
        # M4: 语义检索相关记忆
        if self.memory_store is not None:
            try:
                # Use session_id from memory store for user-specific filtering
                session_id = getattr(self.memory_store, 'session_id', None)
                ctx = self.memory_store.get_context_for_query(
                    self._query, max_tokens=2000, session_filter=session_id
                )
                if ctx:
                    logger.info(f"[M4] Retrieved {len(ctx)} chars of semantic memory context")
                    return ctx
            except Exception as e:
                logger.info(f"[M4] Semantic memory query failed: {e}, falling back to dict")

        # 回退：运行时 dict 遍历
        parts = []
        for key, value in self._memory_store.items():
            if key.startswith("result:"):
                continue
            parts.append(f"{key}: {str(value)[:200]}")

        # M3: 如果上下文过长，启用压缩
        if self.compressor is not None and parts:
            total_chars = sum(len(p) for p in parts)
            if total_chars > 6000:  # 约 2000 tokens 的启发式阈值
                try:
                    compressed = self.compressor.compress(
                        texts=parts,
                        query=self._query,
                        system_prompt_tokens=0,
                    )
                    logger.info(f"[M3] Context compressed: {total_chars} → {sum(len(c) for c in compressed)} chars")
                    return "\n".join(compressed)
                except Exception as e:
                    logger.info(f"[M3] Compression failed: {e}, using raw context")

        return "\n".join(parts) if parts else ""

    async def _build_task_context(self, subtask: SubTask) -> dict:
        """为单个 SubTask 构建执行上下文。"""
        import inspect as _inspect
        ctx = dict(self._memory_store)
        ctx["query"] = self._query
        # 注入依赖任务的结果
        for dep_id in subtask.dependencies:
            dep_key = f"result:{dep_id}"
            if dep_key in self._memory_store:
                ctx[f"dep:{dep_id}"] = self._memory_store[dep_key]
        # 注入 wiki 上下文
        if self.wiki_store:
            try:
                ctx["wiki_index"] = self.wiki_store.get_index()
                ctx["wiki_pages"] = self.wiki_store.get_context(subtask.description)
            except Exception as e:
                logger.debug(f"[Wiki] Context injection failed: {e}")
        # 外部钩子：允许注入用户输入等动态上下文
        if self.context_modifier is not None:
            try:
                result = self.context_modifier(ctx, subtask)
                if _inspect.isawaitable(result):
                    ctx = await result
                else:
                    ctx = result
            except Exception as e:
                logger.debug(f"[Hook] context_modifier error: {e}")
        return ctx

    def _build_failure_reason(self, results: list[AgentResult]) -> str:
        """分析失败原因，生成给 replanner 的描述。"""
        reasons = []
        timeout_count = sum(1 for r in results if r.status == AgentStatus.TIMEOUT)
        failed_count = sum(1 for r in results if r.status == AgentStatus.FAILED)
        if timeout_count > 0:
            reasons.append(f"{timeout_count} tasks timed out (may need simpler queries or longer timeout)")
        if failed_count > 0:
            reasons.append(f"{failed_count} tasks failed with errors")
        evidence_feedback = self.evidence_store.build_replan_feedback(results)
        if evidence_feedback:
            reasons.append(evidence_feedback)
        return "; ".join(reasons) if reasons else "Unknown failure"

    def _build_fallback_report(self, synthesis_result: AgentResult) -> ResearchReport:
        """Build a deterministic report when the summarizer LLM fails."""
        success_count = sum(1 for r in self._results if r.status == AgentStatus.SUCCESS)
        total_count = len(self._results)
        evidence_summary = self.evidence_store.summarize(self._results)
        counts = evidence_summary.get("counts", {})
        num_searches = sum(
            len([t for t in r.trajectory if t.get("role") == "tool"])
            for r in self._results
        )

        lines = [
            "# 降级研究报告",
            "",
            "最终合成模型调用失败，以下内容由 Orchestrator 根据已完成的子任务结果和 evidence items 自动生成。",
            "",
            "## 研究问题",
            "",
            self._query,
            "",
            "## 执行状态",
            "",
            f"- 子任务成功率：{success_count}/{total_count}",
            f"- 工具调用次数：{num_searches}",
            f"- 合成失败原因：{synthesis_result.output}",
            "",
            "## 证据分级统计",
            "",
        ]
        for key in ("verified", "evidence_backed", "speculative", "rejected"):
            lines.append(f"- {key}: {counts.get(key, 0)}")

        lines.extend(["", "## 关键证据", ""])
        for level in ("rejected", "verified", "evidence_backed", "speculative"):
            claims = evidence_summary.get("claims_by_level", {}).get(level, [])
            if not claims:
                continue
            lines.append(f"### {level}")
            for item in claims[:6]:
                source = f" 来源：{item.get('source')}" if item.get("source") else ""
                lines.append(f"- {item.get('claim', '')}{source}")
            lines.append("")

        lines.extend(["## 子任务结果摘要", ""])
        for result in self._results:
            task = self._task_map.get(result.task_id)
            description = task.description if task else result.task_id
            lines.append(f"### {result.task_id} [{result.status.value}]")
            lines.append("")
            lines.append(description)
            lines.append("")
            lines.append(str(result.output)[:1200])
            lines.append("")

        confidence = round(0.5 * (success_count / max(total_count, 1)), 2)
        return ResearchReport(
            query=self._query,
            content="\n".join(lines),
            confidence=confidence,
            num_searches=num_searches,
            num_replan=self._replan_count,
            adversarial_rounds=self._adversarial_count,
            evidence_summary=evidence_summary,
            tool_trace=self._build_tool_trace(self._results),
        )

    def _build_tool_trace(self, results: list[AgentResult]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for result in results:
            for step in result.trajectory:
                if step.get("role") != "tool":
                    continue
                payload = step.get("result")
                urls = []
                if isinstance(payload, dict):
                    for item in payload.get("results", []) or []:
                        if isinstance(item, dict) and item.get("url"):
                            urls.append(item["url"])
                trace.append({
                    "task_id": result.task_id,
                    "tool": step.get("name", ""),
                    "turn": step.get("turn"),
                    "url_count": len(urls),
                    "urls": urls[:5],
                })
        return trace

    def _sum_usage(self, results: list[AgentResult]) -> dict[str, Any]:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
        }
        for result in results:
            for step in result.trajectory:
                usage = step.get("usage") if isinstance(step, dict) else None
                if not isinstance(usage, dict):
                    continue
                for key in totals:
                    totals[key] += int(usage.get(key, 0) or 0)
        denominator = totals["input_tokens"] + totals["cache_read_tokens"]
        totals["cache_hit_rate"] = round(totals["cache_read_tokens"] / denominator, 4) if denominator else 0.0
        return totals

    def _memory_evidence_type(self, evidence) -> str:
        """Map EvidenceLevel to legacy MemoryEntry.evidence_type values."""
        if evidence is None:
            return "inference"
        level = evidence.level.value
        if level == "verified":
            return "primary"
        if level == "evidence_backed":
            return "secondary"
        return "inference"

    def _select_primary_evidence(self, evidence_items):
        """Choose the best representative claim for memory retrieval."""
        if not evidence_items:
            return None
        priority = {
            "verified": 4,
            "evidence_backed": 3,
            "speculative": 2,
            "rejected": 1,
        }
        return max(
            evidence_items,
            key=lambda item: priority.get(getattr(item.level, "value", ""), 0),
        )

    def _rebuild_task_map_from_dag(self) -> dict[str, SubTask]:
        """从 DAG 重建 task_map（当缺少原始 SubTask 信息时使用占位符）。

        实际场景中，planner 应返回完整的 SubTask 列表；
        这里作为降级：为 DAG 中每个节点创建默认 SubTask。
        """
        if self._dag is None:
            return {}

        task_map: dict[str, SubTask] = {}
        for node_id in self._dag:
            deps = self._dag.get_dependencies(node_id)
            if node_id not in self._task_map:
                # 新建占位 SubTask
                task_map[node_id] = SubTask(
                    task_id=node_id,
                    task_type=TaskType.SEARCH,
                    description=f"Auto-generated task for {node_id}",
                    dependencies=deps,
                )
            else:
                # 保留已有信息，更新依赖
                old = self._task_map[node_id]
                task_map[node_id] = SubTask(
                    task_id=old.task_id,
                    task_type=old.task_type,
                    description=old.description,
                    dependencies=deps,
                    context_keys=old.context_keys,
                    timeout_seconds=old.timeout_seconds,
                    priority=old.priority,
                    expected_type=old.expected_type,
                    search_hints=old.search_hints,
                )
        return task_map
