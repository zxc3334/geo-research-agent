"""Research API endpoint — wraps the existing orchestrator as an HTTP service.

Uses SQLAlchemy ORM for task/report persistence (replaces in-memory dict).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.progress_bus import progress_bus
from app.core.interactive_bus import interactive_bus
from app.core.security import get_current_user
from app.models.user import ResearchTask, Report, User
from app.schemas.research import (
    EvidenceSummary,
    ReportDetail,
    ReportMeta,
    ResearchRequest,
    ResearchResponse,
    TaskDetail,
    TaskProgress,
    TaskStatus,
)

router = APIRouter()


# ── Submit research task ─────────────────────────────────────────────

@router.post("/research", response_model=ResearchResponse, status_code=202)
async def submit_research(
    req: ResearchRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Submit a deep research task. Returns immediately with task_id."""
    task = ResearchTask(
        user_id=user.id if user else None,
        query=req.query,
        domain=req.domain,
        depth=req.depth,
        breadth=req.breadth,
        config_path=req.config_path,
        status="running",
        phase="planning",
    )
    db.add(task)
    await db.flush()  # Get task.id

    # Run research in background thread (Phase 2: replace with Celery)
    user_id = user.id if user else ""
    asyncio.get_event_loop().run_in_executor(
        None, _run_research_sync, task.id, req, user_id
    )

    return ResearchResponse(
        task_id=task.id,
        status=TaskStatus.RUNNING,
        message="Research task submitted. Poll GET /api/v1/tasks/{task_id} for status.",
    )


# ── Get task status ──────────────────────────────────────────────────

@router.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get task status and progress."""
    result = await db.execute(select(ResearchTask).where(ResearchTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _task_to_detail(task)


# ── Get report ───────────────────────────────────────────────────────

@router.get("/reports/{task_id}", response_model=ReportDetail)
async def get_report(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get the final research report."""
    result = await db.execute(select(Report).where(Report.task_id == task_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report for task {task_id} not found")

    task_result = await db.execute(select(ResearchTask).where(ResearchTask.id == task_id))
    task = task_result.scalar_one_or_none()

    return ReportDetail(
        task_id=task_id,
        title=report.title,
        content_markdown=report.content_markdown,
        evidence_summary=EvidenceSummary(
            verified=task.evidence_verified if task else 0,
            evidence_backed=task.evidence_backed if task else 0,
            speculative=task.evidence_speculative if task else 0,
            rejected=task.evidence_rejected if task else 0,
        ),
        metadata=ReportMeta(
            model_used=report.model_used,
            confidence=task.confidence if task else 0.0,
            execution_time_seconds=(task.execution_time_ms / 1000) if task else 0.0,
            search_rounds=task.search_rounds if task else 0,
        ),
    )


# ── List tasks ───────────────────────────────────────────────────────

@router.get("/tasks", response_model=list[TaskDetail])
async def list_tasks(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """List recent tasks (optionally filtered by user)."""
    query = select(ResearchTask).order_by(ResearchTask.created_at.desc())
    if user:
        query = query.where(ResearchTask.user_id == user.id)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    tasks = result.scalars().all()
    return [_task_to_detail(t) for t in tasks]


# ── Helpers ──────────────────────────────────────────────────────────

def _make_context_modifier(run_id: str):
    """Create a context modifier that injects user input between subtasks.

    This function is called by the Orchestrator:
    - Before each subtask: injects pending user inputs into context
    - Between layers: waits briefly for user input (non-blocking)
    """
    def modifier(ctx: dict, task) -> dict:
        # Inject pending user inputs into context
        inputs = interactive_bus.get_pending_inputs(run_id)
        if inputs:
            user_notes = "\n".join(f"- {inp.text}" for inp in inputs)
            ctx["user_instructions"] = (
                f"User has provided additional instructions during research:\n{user_notes}\n"
                "Please incorporate these into your analysis."
            )
        return ctx

    # Also expose an async version for between-layer waits
    async def async_modifier(ctx: dict, task) -> dict:
        return modifier(ctx, task)

    # The modifier needs to work both sync (context build) and async (between layers)
    modifier.__call__ = modifier  # Make it callable as sync
    return async_modifier


def _task_to_detail(task: ResearchTask) -> TaskDetail:
    """Convert ORM model to response schema."""
    return TaskDetail(
        task_id=task.id,
        status=TaskStatus(task.status),
        query=task.query,
        domain=task.domain,
        progress=TaskProgress(
            phase=task.phase,
            completed_subtasks=task.completed_subtasks,
            total_subtasks=task.total_subtasks,
        ),
        report_id=task.id if task.status == "completed" else None,
        created_at=task.created_at,
        updated_at=task.completed_at or task.started_at or task.created_at,
        error=task.error,
    )


def _run_research_sync(task_id: str, req: ResearchRequest, user_id: str = ""):
    """Execute the research pipeline synchronously (runs in thread pool).

    Uses a new event loop for DB operations since we're in a thread.
    """
    import logging
    logger = logging.getLogger("app.research")

    async def _update_task(**kwargs):
        from app.core.database import async_session
        async with async_session() as session:
            result = await session.execute(
                select(ResearchTask).where(ResearchTask.id == task_id)
            )
            task = result.scalar_one_or_none()
            if task:
                for k, v in kwargs.items():
                    setattr(task, k, v)
                await session.commit()

    async def _save_report(title: str, content: str, model_used: str = ""):
        from app.core.database import async_session
        async with async_session() as session:
            report = Report(
                task_id=task_id,
                title=title,
                content_markdown=content,
                word_count=len(content),
                model_used=model_used,
            )
            session.add(report)
            await session.commit()

    try:
        from src.core.runner import initialize_modules, load_config, run_research

        now = dt.datetime.now(dt.timezone.utc)
        asyncio.run(_update_task(started_at=now, phase="initializing"))
        progress_bus.publish(task_id, {"phase": "initializing", "status": "running"})

        config_path = req.config_path or settings.default_config_path
        config = load_config(config_path)

        asyncio.run(_update_task(phase="loading_modules"))
        progress_bus.publish(task_id, {"phase": "loading_modules", "status": "running"})

        # Pass user_id as session_id so SharedMemoryStore isolates per user
        session_id = f"user:{user_id}" if user_id else f"anon:{task_id}"
        modules = initialize_modules(config, session_id=session_id)

        # Wire up interactive context modifier
        orchestrator = modules.get("orchestrator")
        if orchestrator:
            orchestrator.context_modifier = _make_context_modifier(task_id)

        asyncio.run(_update_task(phase="researching"))
        progress_bus.publish(task_id, {"phase": "researching", "status": "running"})

        # run_research is async — run it in a new event loop
        report_text = asyncio.run(run_research(
            query=req.query,
            config=config,
            modules=modules,
        ))

        evidence = _parse_evidence_summary(report_text)
        confidence = _parse_confidence(report_text)

        asyncio.run(_save_report(
            title=f"研究: {req.query[:80]}",
            content=report_text,
            model_used=config.get("model", {}).get("default_profile", ""),
        ))

        completed_at = dt.datetime.now(dt.timezone.utc)
        asyncio.run(_update_task(
            status="completed",
            phase="completed",
            completed_at=completed_at,
            confidence=confidence,
            evidence_verified=evidence.verified,
            evidence_backed=evidence.evidence_backed,
            evidence_speculative=evidence.speculative,
            evidence_rejected=evidence.rejected,
            execution_time_ms=int((completed_at - now).total_seconds() * 1000),
        ))

        progress_bus.publish(task_id, {
            "status": "completed",
            "phase": "completed",
            "confidence": confidence,
            "evidence_summary": {
                "verified": evidence.verified,
                "evidence_backed": evidence.evidence_backed,
                "speculative": evidence.speculative,
                "rejected": evidence.rejected,
            },
        })

        logger.info(f"Task {task_id} completed.")

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        asyncio.run(_update_task(
            status="failed",
            error=str(e),
            completed_at=dt.datetime.now(dt.timezone.utc),
        ))
        progress_bus.publish(task_id, {"status": "failed", "error": str(e)})


def _parse_evidence_summary(text: str) -> EvidenceSummary:
    counts = {}
    for level in ("verified", "evidence_backed", "speculative", "rejected"):
        m = re.search(rf"\*\*{level}\*\*:\s*(\d+)", text)
        if m:
            counts[level] = int(m.group(1))
    return EvidenceSummary(**counts)


def _parse_confidence(text: str) -> float:
    m = re.search(r"置信度[:\s]*(0\.\d+|1\.0|1)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0
