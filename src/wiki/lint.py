"""Wiki Lint — Quality checks for wiki knowledge base.

Checks for broken links, duplicate pages, stale content, and missing metadata.
Can be run manually or scheduled periodically.

Usage:
    from src.wiki.lint import WikiLinter
    linter = WikiLinter(wiki_store)
    report = linter.lint()
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .wiki_store import WikiStore

logger = logging.getLogger(__name__)

__all__ = ["WikiLinter", "LintReport", "LintIssue"]


@dataclass
class LintIssue:
    """A single lint issue found in the wiki."""
    severity: str  # "error" | "warning" | "info"
    category: str  # "broken_link" | "duplicate" | "stale" | "missing_metadata" | "empty_page"
    page: str      # Affected page path
    message: str   # Human-readable description
    suggestion: str = ""  # Optional fix suggestion


@dataclass
class LintReport:
    """Summary of all lint issues found."""
    issues: list[LintIssue] = field(default_factory=list)
    pages_scanned: int = 0
    scan_time: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_scanned": self.pages_scanned,
            "scan_time": self.scan_time,
            "summary": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "info": self.info_count,
            },
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "page": i.page,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
        }

    def __str__(self) -> str:
        lines = [f"Lint Report: {self.pages_scanned} pages scanned"]
        lines.append(f"  Errors: {self.error_count}, Warnings: {self.warning_count}, Info: {self.info_count}")
        for issue in self.issues:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue.severity, "?")
            lines.append(f"  {icon} [{issue.category}] {issue.page}: {issue.message}")
            if issue.suggestion:
                lines.append(f"      → {issue.suggestion}")
        return "\n".join(lines)


class WikiLinter:
    """Performs quality checks on a user's wiki."""

    # Pattern: [[category/page-name]]
    _LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")

    def __init__(self, wiki_store: WikiStore, stale_days: int = 30):
        self.wiki = wiki_store
        self.stale_days = stale_days

    def lint(self) -> LintReport:
        """Run all lint checks and return a report."""
        self.wiki.ensure_wiki()
        report = LintReport(scan_time=datetime.now(timezone.utc).isoformat())

        pages = self.wiki.list_pages()
        report.pages_scanned = len(pages)

        # Build page path set for link checking
        page_paths = {p["path"] for p in pages}

        # Run checks
        self._check_broken_links(pages, page_paths, report)
        self._check_stale_pages(pages, report)
        self._check_missing_metadata(pages, report)
        self._check_empty_pages(pages, report)
        self._check_duplicates(pages, report)

        # Log summary
        logger.info(f"[Lint] Scanned {report.pages_scanned} pages: "
                     f"{report.error_count} errors, {report.warning_count} warnings")

        return report

    # ── Check: Broken Links ──────────────────────────────────────────

    def _check_broken_links(
        self,
        pages: list[dict],
        page_paths: set[str],
        report: LintReport,
    ) -> None:
        """Find [[category/page]] links that point to non-existent pages."""
        for page in pages:
            content = self.wiki._read_file(self.wiki.wiki_path / page["path"])
            links = self._LINK_PATTERN.findall(content)

            for link in links:
                # Normalize link: "category/page-name" or just "page-name"
                link_path = link.strip()
                if not link_path.endswith(".md"):
                    link_path += ".md"

                # Check if link target exists
                if link_path not in page_paths:
                    # Try without category prefix
                    basename = link_path.split("/")[-1] if "/" in link_path else link_path
                    if not any(p.endswith(basename) for p in page_paths):
                        report.issues.append(LintIssue(
                            severity="warning",
                            category="broken_link",
                            page=page["path"],
                            message=f"Broken link: [[{link}]] — target page not found",
                            suggestion=f"Create page '{link_path}' or update the link",
                        ))

    # ── Check: Stale Pages ───────────────────────────────────────────

    def _check_stale_pages(self, pages: list[dict], report: LintReport) -> None:
        """Find pages that haven't been updated in stale_days and have low confirmation_count."""
        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(days=self.stale_days)

        for page in pages:
            status = page.get("status", "draft")
            conf_count = page.get("confirmation_count", 0)

            # Skip verified pages (they're stable, not stale)
            if status == "verified":
                continue

            # Check last modified time
            modified_str = page.get("modified", "")
            if modified_str:
                try:
                    modified = datetime.fromisoformat(modified_str)
                    if modified < stale_threshold and conf_count < 2:
                        report.issues.append(LintIssue(
                            severity="info",
                            category="stale",
                            page=page["path"],
                            message=f"Page not updated in {self.stale_days}+ days "
                                    f"(status={status}, confirmation_count={conf_count})",
                            suggestion="Review for accuracy or archive",
                        ))
                except ValueError:
                    pass

    # ── Check: Missing Metadata ──────────────────────────────────────

    def _check_missing_metadata(self, pages: list[dict], report: LintReport) -> None:
        """Find pages missing required metadata (status, confirmation_count)."""
        for page in pages:
            content = self.wiki._read_file(self.wiki.wiki_path / page["path"])

            if "status:" not in content:
                report.issues.append(LintIssue(
                    severity="warning",
                    category="missing_metadata",
                    page=page["path"],
                    message="Missing 'status' field in Page Status section",
                    suggestion="Add '## Page Status' section with '- status: draft'",
                ))

            if "confirmation_count:" not in content:
                report.issues.append(LintIssue(
                    severity="warning",
                    category="missing_metadata",
                    page=page["path"],
                    message="Missing 'confirmation_count' field",
                    suggestion="Add '- confirmation_count: 1' to Page Status section",
                ))

    # ── Check: Empty Pages ───────────────────────────────────────────

    def _check_empty_pages(self, pages: list[dict], report: LintReport) -> None:
        """Find pages with very little content (likely placeholders)."""
        for page in pages:
            content = self.wiki._read_file(self.wiki.wiki_path / page["path"])
            # Remove metadata sections when counting content
            content_without_meta = re.sub(
                r"## Page Status.*$", "", content, flags=re.DOTALL | re.MULTILINE
            ).strip()

            if len(content_without_meta) < 100:
                report.issues.append(LintIssue(
                    severity="info",
                    category="empty_page",
                    page=page["path"],
                    message=f"Page has very little content ({len(content_without_meta)} chars)",
                    suggestion="Add substantive content or consider archiving",
                ))

    # ── Check: Duplicates ────────────────────────────────────────────

    def _check_duplicates(self, pages: list[dict], report: LintReport) -> None:
        """Find pages with very similar names that might be duplicates."""
        # Group by category
        by_category: dict[str, list[dict]] = {}
        for page in pages:
            cat = page.get("category", "other")
            by_category.setdefault(cat, []).append(page)

        for cat, cat_pages in by_category.items():
            names = []
            for p in cat_pages:
                # Normalize name for comparison
                name = p["name"].replace(".md", "").replace("-", " ").lower().strip()
                names.append((name, p["path"]))

            # Check for near-duplicates
            for i, (name_a, path_a) in enumerate(names):
                for j, (name_b, path_b) in enumerate(names):
                    if j <= i:
                        continue
                    # Simple similarity: one name contains the other
                    if name_a in name_b or name_b in name_a:
                        if abs(len(name_a) - len(name_b)) < 5:
                            report.issues.append(LintIssue(
                                severity="warning",
                                category="duplicate",
                                page=path_a,
                                message=f"Possible duplicate: '{name_a}' and '{name_b}'",
                                suggestion=f"Consider merging into one page",
                            ))

    # ── Auto-fix ─────────────────────────────────────────────────────

    def fix_broken_links(self, report: LintReport) -> list[str]:
        """Remove broken [[links]] from pages. Returns list of fixed pages."""
        fixed = []
        for issue in report.issues:
            if issue.category != "broken_link":
                continue

            # Extract the broken link from the message
            match = re.search(r"\[\[(.+?)\]\]", issue.message)
            if not match:
                continue
            broken_link = match.group(1)

            # Read page and remove the broken link
            full_path = self.wiki.wiki_path / issue.page
            if not full_path.exists():
                continue

            content = self.wiki._read_file(full_path)
            # Remove [[broken_link]] references
            updated = content.replace(f"[[{broken_link}]]", broken_link)
            if updated != content:
                self.wiki._write_file(full_path, updated)
                fixed.append(issue.page)
                logger.info(f"[Lint] Fixed broken link [[{broken_link}]] in {issue.page}")

        if fixed:
            self.wiki._append_log(f"Lint: fixed broken links in {len(fixed)} pages")

        return fixed

    def remove_stale_pages(self, report: LintReport, dry_run: bool = True) -> list[str]:
        """Remove pages flagged as stale. Returns list of removed page paths.

        Args:
            dry_run: If True, only returns what would be removed without actually deleting.
        """
        removed = []
        for issue in report.issues:
            if issue.category != "stale":
                continue

            if dry_run:
                removed.append(issue.page)
                continue

            success = self.wiki.delete_page(issue.page)
            if success:
                removed.append(issue.page)
                logger.info(f"[Lint] Removed stale page: {issue.page}")

        if removed and not dry_run:
            self.wiki._append_log(f"Lint: removed {len(removed)} stale pages")

        return removed
