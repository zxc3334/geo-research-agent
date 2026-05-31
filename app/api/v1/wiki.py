"""Wiki Knowledge Base API endpoints.

Provides CRUD operations for user wiki pages, plus ingest/search/export.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Any

from app.core.config import settings
from app.core.security import get_current_user, require_user
from app.models.user import User
from src.wiki.wiki_store import WikiStore

router = APIRouter()


def _get_wiki(user_id: str) -> WikiStore:
    """Get WikiStore instance for a user."""
    base = getattr(settings, "wiki_base_path", "data/wiki")
    return WikiStore(base, user_id)


# ── Schemas ─────────────────────────────────────────────────────────

class WikiPageRequest(BaseModel):
    content: str = Field(..., description="Page content (markdown)")
    category: str = Field(default="notes", description="Page category")


class WikiPageUpdate(BaseModel):
    content: str = Field(..., description="Updated page content (markdown)")


class WikiSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


# ── Preset wiki (read-only) ─────────────────────────────────────────

@router.get("/wiki/preset")
async def get_preset_wiki():
    """Get the preset knowledge base directory tree (read-only)."""
    # For now, return empty — preset wiki will be added in future
    return {"pages": [], "message": "Preset knowledge base not yet available"}


# ── User wiki (read-write) ──────────────────────────────────────────

@router.get("/wiki/me")
async def get_my_wiki(user: User = Depends(require_user)):
    """Get the current user's wiki directory tree."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()
    return {
        "user_id": user.id,
        "pages": wiki.list_pages(),
        "raws": wiki.list_raws(),
        "index": wiki.get_index(),
    }


@router.get("/wiki/me/index")
async def get_my_wiki_index(user: User = Depends(require_user)):
    """Get the user's wiki index.md."""
    wiki = _get_wiki(user.id)
    return {"index": wiki.get_index()}


@router.get("/wiki/me/pages")
async def list_my_pages(
    category: str | None = None,
    user: User = Depends(require_user),
):
    """List wiki pages, optionally filtered by category."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()
    return {"pages": wiki.list_pages(category=category)}


@router.get("/wiki/me/pages/{path:path}")
async def get_wiki_page(path: str, user: User = Depends(require_user)):
    """Read a wiki page by path (e.g. 'sensors/landsat-8-9.md')."""
    wiki = _get_wiki(user.id)
    page = wiki.read_page(path)
    if not page:
        raise HTTPException(status_code=404, detail=f"Page not found: {path}")
    return page


@router.post("/wiki/me/pages/{path:path}", status_code=201)
async def create_wiki_page(
    path: str,
    body: WikiPageRequest,
    user: User = Depends(require_user),
):
    """Create a new wiki page."""
    wiki = _get_wiki(user.id)
    try:
        result = wiki.create_page(path, body.content, body.category)
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/wiki/me/pages/{path:path}")
async def update_wiki_page(
    path: str,
    body: WikiPageUpdate,
    user: User = Depends(require_user),
):
    """Update an existing wiki page."""
    wiki = _get_wiki(user.id)
    try:
        result = wiki.update_page(path, body.content)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/wiki/me/pages/{path:path}")
async def delete_wiki_page(path: str, user: User = Depends(require_user)):
    """Delete a wiki page."""
    wiki = _get_wiki(user.id)
    success = wiki.delete_page(path)
    if not success:
        raise HTTPException(status_code=404, detail=f"Page not found: {path}")
    return {"status": "deleted", "path": path}


# ── Search ──────────────────────────────────────────────────────────

@router.post("/wiki/me/search")
async def search_wiki(
    body: WikiSearchRequest,
    user: User = Depends(require_user),
):
    """Search the user's wiki."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()
    results = wiki.search(body.query, limit=body.limit)
    return {"query": body.query, "results": results, "total": len(results)}


# ── Ingest ──────────────────────────────────────────────────────────

@router.post("/wiki/me/ingest")
async def trigger_ingest(user: User = Depends(require_user)):
    """Manually trigger ingest from the latest raw report."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()

    latest_raw = wiki.get_latest_raw()
    if not latest_raw:
        raise HTTPException(status_code=404, detail="No raw reports found")

    from src.wiki.ingest import WikiIngest
    ingest = WikiIngest(wiki)

    content = wiki._read_file(wiki.wiki_path / latest_raw)
    result = await ingest.ingest(content, query="manual ingest")

    return {
        "status": "completed",
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "confirmation_bumps": result.confirmation_bumps,
    }


# ── Lint ────────────────────────────────────────────────────────────

@router.post("/wiki/me/lint")
async def lint_wiki(
    fix_links: bool = False,
    user: User = Depends(require_user),
):
    """Run lint checks on the user's wiki."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()

    from src.wiki.lint import WikiLinter
    linter = WikiLinter(wiki)
    report = linter.lint()

    result = report.to_dict()

    # Optionally fix broken links
    if fix_links:
        fixed = linter.fix_broken_links(report)
        result["fixed_links"] = fixed

    return result


# ── Export ───────────────────────────────────────────────────────────

@router.get("/wiki/me/export")
async def export_wiki(user: User = Depends(require_user)):
    """Export the user's wiki as a ZIP file."""
    wiki = _get_wiki(user.id)
    wiki.ensure_wiki()

    tmp_path = os.path.join(tempfile.gettempdir(), f"wiki_{user.id[:8]}.zip")
    wiki.export_zip(tmp_path)

    return FileResponse(
        path=tmp_path,
        filename=f"wiki_{user.id[:8]}.zip",
        media_type="application/zip",
    )
