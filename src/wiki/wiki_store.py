"""WikiStore — 用户专属 Wiki 知识库管理器。

每个用户拥有独立完整的 Wiki 知识库，存储为 Markdown 文件。
三层架构：Layer 1 (Raw) → Layer 2 (Wiki) → Layer 3 (Config)

冷启动靠"证据门槛 + 页面生命周期"让知识库自然生长。
"""
from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

__all__ = ["WikiStore"]


# ── Default _config.yaml template ───────────────────────────────────

_DEFAULT_CONFIG = {
    "structure": {
        "categories": [
            {"name": "sensors", "description": "遥感传感器实体页（Landsat, Sentinel, MODIS 等）"},
            {"name": "methods", "description": "遥感方法/算法页（LST反演, NDVI, NDBI 等）"},
            {"name": "analyses", "description": "研究分析综述页（城市热岛, 绿地监测 等）"},
            {"name": "comparisons", "description": "对比分析页（A vs B）"},
            {"name": "projects", "description": "用户研究项目页"},
            {"name": "notes", "description": "用户笔记/备忘"},
        ],
    },
    "naming": {
        "file_format": "lowercase, hyphen-separated",
        "entity_rule": "one page = one knowledge entity",
        "cross_ref_format": "[[category/page-name]]",
    },
    "evidence_gate": {
        "min_level": "evidence_backed",
        "min_confidence": 0.6,
        "allow_speculative_if_high_conf": True,
        "speculative_confidence_threshold": 0.8,
        "allow_rejected_as_negative": True,
    },
    "page_lifecycle": {
        "initial_status": "draft",
        "confirm_threshold": 2,
        "verify_threshold": 3,
        "stale_days": 30,
    },
    "ingest": {
        "new_page_threshold": "entity would be referenced by other pages",
        "update_threshold": "new info supplements or corrects existing content",
        "auto_ingest": True,
        "max_pages_per_ingest": 5,
    },
}

_DEFAULT_CATEGORIES = ["sensors", "methods", "analyses", "comparisons", "projects", "notes", "raws"]


class WikiStore:
    """用户专属 Wiki 知识库管理器。"""

    def __init__(self, base_path: str, user_id: str):
        """
        Args:
            base_path: Wiki 根目录（如 "data/wiki"）
            user_id: 用户 ID
        """
        self.base_path = Path(base_path)
        self.user_id = user_id
        self.wiki_path = self.base_path / "users" / user_id

    # ── Initialization ───────────────────────────────────────────────

    def ensure_wiki(self) -> None:
        """首次使用时创建 wiki 结构（冷启动）。已存在则跳过。"""
        config_path = self.wiki_path / "_config.yaml"
        if config_path.exists():
            return

        logger.info(f"[Wiki] Creating wiki for user {self.user_id[:8]}...")
        self.wiki_path.mkdir(parents=True, exist_ok=True)

        # Create category directories
        for category in _DEFAULT_CATEGORIES:
            (self.wiki_path / category).mkdir(exist_ok=True)

        # Write default config
        self._write_yaml(config_path, _DEFAULT_CONFIG)

        # Write default index
        self._write_file(
            self.wiki_path / "index.md",
            f"# Knowledge Index\n\n> Auto-generated for user {self.user_id[:8]}\n\n"
            "_No pages yet. Start a research to build your knowledge base._\n",
        )

        # Write empty log
        self._write_file(
            self.wiki_path / "log.md",
            f"# Wiki Operation Log\n\n## {self._today()}\n- Wiki initialized for user {self.user_id[:8]}\n",
        )

        logger.info(f"[Wiki] Wiki created at {self.wiki_path}")

    def wiki_exists(self) -> bool:
        """Check if wiki has been initialized."""
        return (self.wiki_path / "_config.yaml").exists()

    # ── Layer 1: Raw Storage ─────────────────────────────────────────

    def save_raw(self, report_content: str, query: str) -> str:
        """Save a research report to raws/.

        Returns:
            Path to the saved raw file.
        """
        self.ensure_wiki()
        slug = self._slugify(query)[:60]
        date = self._today()
        filename = f"{date}_{slug}.md"
        path = self.wiki_path / "raws" / filename
        self._write_file(path, report_content)
        logger.info(f"[Wiki] Raw saved: {path.name}")
        return str(path)

    def get_latest_raw(self) -> str | None:
        """Get the most recent raw report path."""
        raws_dir = self.wiki_path / "raws"
        if not raws_dir.exists():
            return None
        files = sorted(raws_dir.glob("*.md"), reverse=True)
        return str(files[0]) if files else None

    def list_raws(self) -> list[dict[str, Any]]:
        """List all raw reports with metadata."""
        raws_dir = self.wiki_path / "raws"
        if not raws_dir.exists():
            return []
        results = []
        for f in sorted(raws_dir.glob("*.md"), reverse=True):
            results.append({
                "name": f.name,
                "path": f"raws/{f.name}",
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        return results

    # ── Layer 2: Wiki Pages ──────────────────────────────────────────

    def list_pages(self, category: str | None = None, include_raws: bool = False) -> list[dict[str, Any]]:
        """List wiki pages, optionally filtered by category.

        Args:
            category: Filter by specific category. None = all categories.
            include_raws: Whether to include raws/ directory (Layer 1).
                         Default False — raws are raw reports, not wiki pages.
        """
        self.ensure_wiki()
        pages = []
        categories = [category] if category else _DEFAULT_CATEGORIES

        for cat in categories:
            # Skip raws by default (they are Layer 1, not wiki pages)
            if cat == "raws" and not include_raws:
                continue
            cat_dir = self.wiki_path / cat
            if not cat_dir.is_dir():
                continue
            for f in sorted(cat_dir.glob("*.md")):
                status = self._parse_field_from_file(f, "status")
                conf_count = self._parse_field_from_file(f, "confirmation_count")
                pages.append({
                    "name": f.name,
                    "path": f"{cat}/{f.name}",
                    "category": cat,
                    "status": status or "draft",
                    "confirmation_count": int(conf_count) if conf_count else 0,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                })
        return pages

    def read_page(self, path: str) -> dict[str, Any] | None:
        """Read a wiki page by relative path (e.g. 'sensors/landsat-8-9.md')."""
        full_path = self.wiki_path / path
        if not full_path.exists() or not full_path.is_file():
            return None
        content = self._read_file(full_path)
        return {
            "path": path,
            "content": content,
            "status": self._parse_field(content, "status") or "draft",
            "confirmation_count": int(self._parse_field(content, "confirmation_count") or 0),
            "size": len(content),
        }

    def create_page(self, path: str, content: str, category: str = "notes") -> dict[str, Any]:
        """Create a new wiki page."""
        self.ensure_wiki()
        # Ensure category directory exists
        cat_dir = self.wiki_path / category
        cat_dir.mkdir(exist_ok=True)

        full_path = self.wiki_path / path
        if full_path.exists():
            raise ValueError(f"Page already exists: {path}")

        # Add lifecycle metadata if not present
        if "status:" not in content:
            content += f"\n\n## Page Status\n- status: draft\n- first_seen: {self._today()}\n- confirmation_count: 1\n"

        self._write_file(full_path, content)
        self._rebuild_index()
        self._append_log(f"Created page: {path}")
        logger.info(f"[Wiki] Page created: {path}")
        return {"path": path, "status": "draft"}

    def update_page(self, path: str, content: str) -> dict[str, Any]:
        """Update an existing wiki page."""
        full_path = self.wiki_path / path
        if not full_path.exists():
            raise ValueError(f"Page not found: {path}")
        self._write_file(full_path, content)
        self._append_log(f"Updated page: {path}")
        return {"path": path, "status": "updated"}

    def update_page_section(self, path: str, section: str, new_content: str) -> dict[str, Any]:
        """Append content to a specific section of a wiki page."""
        full_path = self.wiki_path / path
        if not full_path.exists():
            raise ValueError(f"Page not found: {path}")

        existing = self._read_file(full_path)
        # Find the section and append
        pattern = rf"(## {re.escape(section)}.*?)(\n## |\Z)"
        match = re.search(pattern, existing, re.DOTALL)
        if match:
            updated = existing[:match.end(1)] + "\n" + new_content + "\n" + existing[match.end(1):]
        else:
            # Section doesn't exist, append it
            updated = existing.rstrip() + f"\n\n## {section}\n{new_content}\n"

        self._write_file(full_path, updated)
        return {"path": path, "section": section, "status": "updated"}

    def delete_page(self, path: str) -> bool:
        """Delete a wiki page."""
        full_path = self.wiki_path / path
        if not full_path.exists():
            return False
        full_path.unlink()
        self._rebuild_index()
        self._append_log(f"Deleted page: {path}")
        return True

    def bump_confirmation(self, path: str) -> str:
        """Increment confirmation_count and update status.

        Returns:
            New status string.
        """
        full_path = self.wiki_path / path
        if not full_path.exists():
            return "draft"

        content = self._read_file(full_path)
        config = self._load_config()
        lifecycle = config.get("page_lifecycle", {})

        count = int(self._parse_field(content, "confirmation_count") or 0)
        new_count = count + 1

        if new_count >= lifecycle.get("verify_threshold", 3):
            new_status = "verified"
        elif new_count >= lifecycle.get("confirm_threshold", 2):
            new_status = "confirmed"
        else:
            new_status = "draft"

        content = self._update_field(content, "status", new_status)
        content = self._update_field(content, "confirmation_count", str(new_count))
        content = self._update_field(content, "last_confirmed", self._today())
        self._write_file(full_path, content)

        logger.info(f"[Wiki] {path}: confirmation_count={new_count}, status={new_status}")
        return new_status

    # ── Index ────────────────────────────────────────────────────────

    def get_index(self) -> str:
        """Get index.md content (for system prompt injection)."""
        self.ensure_wiki()
        index_path = self.wiki_path / "index.md"
        if index_path.exists():
            return self._read_file(index_path)
        return ""

    def get_context(
        self,
        query: str,
        max_tokens: int = 4000,
        top_k: int = 3,
    ) -> str:
        """Three-level cache strategy to assemble wiki context for an agent.

        Level 1: index.md (handled externally by PromptBuilder pipe — not repeated here)
        Level 2: Wiki pages (loaded on demand, sorted by status priority)
        Level 3: Raw documents (fallback when wiki content is insufficient)

        Args:
            query: The current research query or task description.
            max_tokens: Token budget for context assembly.
            top_k: Maximum wiki pages to include.

        Returns:
            Formatted context string (empty if wiki has no relevant content).
        """
        self.ensure_wiki()
        parts: list[str] = []
        used_chars = 0
        max_chars = int(max_tokens * 3.5)  # token → char heuristic

        # ── L2: Wiki pages (sorted by status: verified > confirmed > draft) ──
        pages = self.search(query, limit=top_k * 2)
        pages.sort(key=lambda p: self._status_priority(p.get("status", "draft")), reverse=True)

        for page in pages[:top_k]:
            content = self._read_file(self.wiki_path / page["path"])
            if not content:
                continue
            status = page.get("status", "draft")
            # Truncate long pages
            if len(content) > max_chars * 0.4:
                content = content[:int(max_chars * 0.4)] + "\n\n[... truncated]"
            if used_chars + len(content) > max_chars * 0.7:
                break
            parts.append(f"## [Wiki - {status}] {page['path']}\n{content}")
            used_chars += len(content)

        # ── L3: Raw document fallback (when wiki is sparse) ──
        if used_chars < max_chars * 0.2:
            raw_path = self.get_latest_raw()
            if raw_path:
                raw_content = self._read_file(self.wiki_path / raw_path)
                if raw_content:
                    # Limit raw content
                    raw_snippet = raw_content[:int(max_chars * 0.5)]
                    if len(raw_content) > len(raw_snippet):
                        raw_snippet += "\n\n[... truncated]"
                    parts.append(f"## [Research Archive]\n{raw_snippet}")

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _status_priority(status: str) -> int:
        """Higher number = higher priority in context assembly."""
        return {"verified": 3, "confirmed": 2, "draft": 1}.get(status, 0)

    def rebuild_index(self) -> None:
        """Public wrapper for index rebuild."""
        self.ensure_wiki()
        self._rebuild_index()

    # ── Config ───────────────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        """Get the wiki config."""
        return self._load_config()

    def get_categories(self) -> list[str]:
        """Get list of configured categories."""
        config = self._load_config()
        return [c["name"] for c in config.get("structure", {}).get("categories", [])]

    # ── Search ───────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Simple keyword search across all wiki pages."""
        self.ensure_wiki()
        query_lower = query.lower()
        results = []

        for page in self.list_pages():
            content = self._read_file(self.wiki_path / page["path"])
            content_lower = content.lower()
            # Simple relevance: count keyword hits
            hits = sum(1 for word in query_lower.split() if word in content_lower)
            if hits > 0:
                results.append({
                    **page,
                    "relevance": hits,
                    "snippet": self._extract_snippet(content, query_lower),
                })

        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results[:limit]

    # ── Export ────────────────────────────────────────────────────────

    def export_zip(self, output_path: str) -> str:
        """Export wiki as a ZIP file."""
        import zipfile
        self.ensure_wiki()
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(self.wiki_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, self.wiki_path)
                    zf.write(file_path, arcname)
        return output_path

    # ── Helpers ───────────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        """Load _config.yaml, return defaults if missing."""
        config_path = self.wiki_path / "_config.yaml"
        if config_path.exists():
            return self._read_yaml(config_path)
        return _DEFAULT_CONFIG.copy()

    def _rebuild_index(self) -> None:
        """Rebuild index.md from all wiki pages."""
        pages = self.list_pages()
        lines = [
            f"# Knowledge Index",
            f"",
            f"> Auto-generated | {len(pages)} pages | {self._today()}",
            "",
        ]

        current_category = ""
        for page in sorted(pages, key=lambda p: (p["category"], p["name"])):
            if page["category"] != current_category:
                current_category = page["category"]
                lines.append(f"\n## {current_category.title()}\n")

            name = page["name"].replace(".md", "").replace("-", " ").title()
            status = page["status"]
            conf_count = page["confirmation_count"]
            status_emoji = {"draft": "🟡", "confirmed": "🔵", "verified": "🟢", "stale": "⚪"}.get(status, "⚪")
            lines.append(f"- {status_emoji} [[{page['path']}]] ({status}, ×{conf_count}) — {name}")

        if not pages:
            lines.append("_No pages yet. Start a research to build your knowledge base._")

        self._write_file(self.wiki_path / "index.md", "\n".join(lines) + "\n")

    def _append_log(self, message: str) -> None:
        """Append to log.md."""
        log_path = self.wiki_path / "log.md"
        entry = f"\n- [{self._now()}] {message}"
        if log_path.exists():
            existing = self._read_file(log_path)
            self._write_file(log_path, existing + entry)
        else:
            self._write_file(log_path, f"# Wiki Operation Log\n{entry}")

    def _parse_field(self, content: str, field: str) -> str | None:
        """Extract a field value from page content (e.g., 'status: draft')."""
        match = re.search(rf"^- {field}:\s*(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _parse_field_from_file(self, path: Path, field: str) -> str | None:
        """Extract a field value from a file."""
        if not path.exists():
            return None
        content = self._read_file(path)
        return self._parse_field(content, field)

    def _update_field(self, content: str, field: str, value: str) -> str:
        """Update a field value in page content."""
        pattern = rf"^(- {field}:).*$"
        replacement = rf"\1 {value}"
        if re.search(pattern, content, re.MULTILINE):
            return re.sub(pattern, replacement, content, flags=re.MULTILINE)
        # Field doesn't exist, append to Page Status section
        return content.rstrip() + f"\n- {field}: {value}\n"

    def _extract_snippet(self, content: str, query: str, max_len: int = 200) -> str:
        """Extract a relevant snippet from content."""
        content_lower = content.lower()
        words = query.split()
        best_pos = 0
        best_hits = 0
        for i in range(0, len(content_lower) - max_len, 50):
            window = content_lower[i:i + max_len]
            hits = sum(1 for w in words if w in window)
            if hits > best_hits:
                best_hits = hits
                best_pos = i
        return content[best_pos:best_pos + max_len].strip()

    def _write_file(self, path: Path, content: str) -> None:
        """Write content to a file, creating parent directories if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_file(self, path: Path) -> str:
        """Read file content."""
        return path.read_text(encoding="utf-8")

    def _write_yaml(self, path: Path, data: dict) -> None:
        """Write YAML file."""
        self._write_file(path, yaml.dump(data, allow_unicode=True, default_flow_style=False))

    def _read_yaml(self, path: Path) -> dict:
        """Read YAML file."""
        return yaml.safe_load(self._read_file(path)) or {}

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a filename-safe slug."""
        text = text.lower().strip()
        # Replace / with - before cleaning
        text = text.replace("/", "-")
        text = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        # Collapse multiple hyphens but keep single ones
        text = re.sub(r"-{2,}", "-", text)
        return text.strip("-")[:60]

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
