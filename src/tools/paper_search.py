"""Academic paper search tool for GIS/remote-sensing evidence."""
from __future__ import annotations

from typing import Any

from .arxiv_reader import ArxivReaderTool


class PaperSearchTool:
    """Search academic papers through OpenAlex/Semantic Scholar/ArXiv.

    This is a clearer domain-level wrapper around the legacy ArxivReaderTool.
    The default backend is OpenAlex because it does not require an API key.
    """

    name = "paper_search"
    description = (
        "Search academic literature for GIS/remote-sensing methods, algorithms, formulas, and validation evidence. "
        "Default backend is OpenAlex and does not require an API key. "
        "Input: {'query': str(optional), 'paper_id': str(optional), 'max_results': int(optional)}. "
        "Output: structured papers with title, authors, year, abstract/summary, URL, citation count, and source metadata."
    )

    def __init__(self, backend: str = "openalex", use_mock: bool = False) -> None:
        self.reader = ArxivReaderTool(backend=backend, use_mock=use_mock)
        self.backend = self.reader.backend
        self.use_mock = use_mock

    def get_openai_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paper_id": {
                            "type": "string",
                            "description": "Optional paper identifier such as DOI, OpenAlex ID, Semantic Scholar ID, or arXiv ID.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Literature search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of papers to return.",
                            "default": 5,
                        },
                    },
                    "anyOf": [{"required": ["paper_id"]}, {"required": ["query"]}],
                },
            },
        }

    async def execute(
        self,
        query: str | None = None,
        paper_id: str | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        result = await self.reader.execute(paper_id=paper_id, query=query, max_results=max_results)
        papers = []
        for paper in result.get("papers", []) or []:
            if not isinstance(paper, dict):
                continue
            papers.append(self._normalize_paper(paper))

        response = {
            "query": query or paper_id,
            "papers": papers,
            "total": len(papers),
            "source": f"paper_search:{result.get('source', self.backend)}",
            "source_type": "academic_paper",
            "evidence_level": "evidence_backed" if papers else "speculative",
            "backend": self.backend,
        }
        if result.get("error"):
            response["error"] = result["error"]
        return response

    def _normalize_paper(self, paper: dict[str, Any]) -> dict[str, Any]:
        url = paper.get("pdf_url", "") or paper.get("url", "")
        return {
            "id": paper.get("id", ""),
            "title": paper.get("title", ""),
            "authors": paper.get("authors", []),
            "summary": (paper.get("summary", "") or paper.get("abstract", ""))[:500],
            "published": paper.get("published", "") or paper.get("year", ""),
            "url": url,
            "pdf_url": paper.get("pdf_url", ""),
            "source": paper.get("source", self.backend),
            "source_type": "academic_paper",
            "citation_count": paper.get("citation_count"),
        }
