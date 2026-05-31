"""Shared domain-to-tier classification and authority scoring.

Single source of truth for "how much do we trust this domain?" used by both:
- **WebSearchTool** (search-time): ranks results so high-quality sources appear first.
- **EvidenceStore** (evidence-time): grades claim provenance for the final report.

Design note — two separate concerns share one table:
┌──────────────────────────────────────────────────────────────────┐
│  DOMAIN_TIER_TABLE  —  "what kind of source is this domain?"    │
│                       (shared, single source of truth)           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Search-time scoring (WebSearchTool._rank_results)               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ domain_score = authority_score(url)    ← tier → 0-100      │  │
│  │ relevance    = keyword_hits / query_len ← content matching  │  │
│  │ final_score  = domain × 0.6 + relevance × 0.4              │  │
│  │                                                            │  │
│  │ Purpose: sort results so agent sees NASA before CSDN.       │  │
│  │ Signal:  domain authority + content relevance               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Evidence-time grading (EvidenceStore.classify)                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ source_tier = classify_url(url)        ← tier enum          │  │
│  │ + consensus = count of distinct domains agreeing            │  │
│  │ → EvidenceLevel: VERIFIED / EVIDENCE_BACKED / SPECULATIVE   │  │
│  │                                                            │  │
│  │ Purpose: tell the user "this claim is backed by NASA docs"  │  │
│  │          vs "this claim is from a CSDN blog post".          │  │
│  │ Signal:  source type + cross-source agreement               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Why both?                                                       │
│  - Search scoring uses keyword relevance (content-aware).        │
│  - Evidence grading uses cross-source consensus (verification).  │
│  - They operate at different pipeline stages and serve           │
│    different consumers (agent vs. summarizer/user).              │
└──────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from urllib.parse import urlparse

from ..orchestrator.schemas import SourceTier

__all__ = [
    "classify_url",
    "authority_score",
    "best_tier",
    "extract_hostname",
    "DOMAIN_TIER_TABLE",
    "TIER_BASE_SCORE",
]


# =====================================================================
#  Unified domain → SourceTier mapping (single source of truth)
# =====================================================================

DOMAIN_TIER_TABLE: dict[str, SourceTier] = {
    # ── Official / government / space agency ──────────────────────
    "nasa.gov":                          SourceTier.OFFICIAL,
    "usgs.gov":                          SourceTier.OFFICIAL,
    "esa.int":                           SourceTier.OFFICIAL,
    "copernicus.eu":                     SourceTier.OFFICIAL,
    "sentinel.esa.int":                  SourceTier.OFFICIAL,
    "sentinels.copernicus.eu":           SourceTier.OFFICIAL,
    "sentiwiki.copernicus.eu":           SourceTier.OFFICIAL,
    "documentation.dataspace.copernicus.eu": SourceTier.OFFICIAL,
    "modis.gsfc.nasa.gov":               SourceTier.OFFICIAL,
    "lpdaac.usgs.gov":                   SourceTier.OFFICIAL,
    "earthengine.google.com":            SourceTier.OFFICIAL,
    "developers.google.com":             SourceTier.OFFICIAL,
    "planetarycomputer.microsoft.com":   SourceTier.OFFICIAL,
    "microsoft.com":                     SourceTier.OFFICIAL,
    "ecmwf.int":                         SourceTier.OFFICIAL,
    "noaa.gov":                          SourceTier.OFFICIAL,
    "eumetsat.int":                      SourceTier.OFFICIAL,
    "geoservice.dlr.de":                 SourceTier.OFFICIAL,
    "jpl.nasa.gov":                      SourceTier.OFFICIAL,
    "earthdata.nasa.gov":                SourceTier.OFFICIAL,
    # ── Academic / publisher ──────────────────────────────────────
    "arxiv.org":                         SourceTier.ACADEMIC,
    "doi.org":                           SourceTier.ACADEMIC,
    "scholar.google.com":                SourceTier.ACADEMIC,
    "semanticscholar.org":               SourceTier.ACADEMIC,
    "pubmed.ncbi.nlm.nih.gov":           SourceTier.ACADEMIC,
    "ieee.org":                          SourceTier.ACADEMIC,
    "ieee.com":                          SourceTier.ACADEMIC,
    "springer.com":                      SourceTier.ACADEMIC,
    "link.springer.com":                 SourceTier.ACADEMIC,
    "sciencedirect.com":                 SourceTier.ACADEMIC,
    "nature.com":                        SourceTier.ACADEMIC,
    "wiley.com":                         SourceTier.ACADEMIC,
    "tandfonline.com":                   SourceTier.ACADEMIC,
    "mdpi.com":                          SourceTier.ACADEMIC,
    "researchgate.net":                  SourceTier.ACADEMIC,
    "academia.edu":                      SourceTier.ACADEMIC,
    "openalex.org":                      SourceTier.ACADEMIC,
    # ── Authoritative reference ───────────────────────────────────
    "wikipedia.org":                     SourceTier.AUTHORITATIVE,
    "en.wikipedia.org":                  SourceTier.AUTHORITATIVE,
    "stackoverflow.com":                 SourceTier.AUTHORITATIVE,
    "github.com":                        SourceTier.AUTHORITATIVE,
    "docs.python.org":                   SourceTier.AUTHORITATIVE,
    "learn.microsoft.com":               SourceTier.AUTHORITATIVE,
    "cloud.google.com":                  SourceTier.AUTHORITATIVE,
    "aws.amazon.com":                    SourceTier.AUTHORITATIVE,
    "gnu.org":                           SourceTier.AUTHORITATIVE,
    "kernel.org":                        SourceTier.AUTHORITATIVE,
    # ── General / low-quality / user-generated ────────────────────
    "csdn.net":                          SourceTier.GENERAL,
    "blog.csdn.net":                     SourceTier.GENERAL,
    "zhihu.com":                         SourceTier.GENERAL,
    "jianshu.com":                       SourceTier.GENERAL,
    "baidu.com":                         SourceTier.GENERAL,
    "baike.baidu.com":                   SourceTier.GENERAL,
    "tieba.baidu.com":                   SourceTier.GENERAL,
    "juejin.cn":                         SourceTier.GENERAL,
    "segmentfault.com":                  SourceTier.GENERAL,
    "cnblogs.com":                       SourceTier.GENERAL,
    "bilibili.com":                      SourceTier.GENERAL,
    "douyin.com":                        SourceTier.GENERAL,
    "weibo.com":                         SourceTier.GENERAL,
    "medium.com":                        SourceTier.GENERAL,
    "dev.to":                            SourceTier.GENERAL,
    "hashnode.dev":                      SourceTier.GENERAL,
    "reddit.com":                        SourceTier.GENERAL,
    "quora.com":                         SourceTier.GENERAL,
    "yahoo.com":                         SourceTier.GENERAL,
}

# Tier → default numeric authority score (0-100).
TIER_BASE_SCORE: dict[SourceTier, float] = {
    SourceTier.OFFICIAL:       95.0,
    SourceTier.ACADEMIC:       75.0,
    SourceTier.AUTHORITATIVE:  55.0,
    SourceTier.GENERAL:        30.0,
    SourceTier.UNVERIFIED:     10.0,
}

# TLD fallback rules (checked when domain is not in the table).
_TLD_TIER_RULES: list[tuple[str, SourceTier]] = [
    (".gov",      SourceTier.OFFICIAL),
    (".edu",      SourceTier.ACADEMIC),
    (".mil",      SourceTier.OFFICIAL),
    (".gov.cn",   SourceTier.OFFICIAL),
    (".edu.cn",   SourceTier.ACADEMIC),
    (".ac.cn",    SourceTier.ACADEMIC),
    (".org",      SourceTier.AUTHORITATIVE),
]


# =====================================================================
#  Public API
# =====================================================================

def classify_url(url: str) -> SourceTier:
    """Classify a URL into a source quality tier.

    This is a heuristic about *source type*, NOT about whether the claim
    is true.  A high-tier source can still contain errors; a low-tier
    source can still be correct.
    """
    hostname = extract_hostname(url)
    if not hostname:
        return SourceTier.UNVERIFIED

    # 1. Exact match in table.
    if hostname in DOMAIN_TIER_TABLE:
        return DOMAIN_TIER_TABLE[hostname]

    # 2. Parent-domain match (e.g. blog.csdn.net → csdn.net).
    for domain, tier in DOMAIN_TIER_TABLE.items():
        if hostname.endswith("." + domain) or hostname == domain:
            return tier

    # 3. TLD heuristic.
    for tld, tier in _TLD_TIER_RULES:
        if hostname.endswith(tld):
            return tier

    # 4. Unknown → general.
    return SourceTier.GENERAL


def authority_score(url: str) -> float:
    """Return a 0-100 numeric authority score for a URL.

    Used by WebSearchTool to compute a weighted ranking score.
    The mapping is: tier → base score, with slight variations for
    well-known high-value domains.
    """
    tier = classify_url(url)
    base = TIER_BASE_SCORE[tier]

    # Slight intra-tier boosts for especially authoritative domains.
    hostname = extract_hostname(url)
    if hostname:
        _BOOSTS: dict[str, float] = {
            "nasa.gov": 5.0, "usgs.gov": 5.0, "esa.int": 5.0,
            "nature.com": 3.0, "ieee.org": 3.0,
        }
        for domain, boost in _BOOSTS.items():
            if hostname == domain or hostname.endswith("." + domain):
                base = min(100.0, base + boost)

    return base


def best_tier(urls: list[str]) -> SourceTier:
    """Return the highest-quality tier among a list of URLs."""
    tier_rank = {
        SourceTier.OFFICIAL: 4,
        SourceTier.ACADEMIC: 3,
        SourceTier.AUTHORITATIVE: 2,
        SourceTier.GENERAL: 1,
        SourceTier.UNVERIFIED: 0,
    }
    best = SourceTier.UNVERIFIED
    for url in urls:
        tier = classify_url(url)
        if tier_rank[tier] > tier_rank[best]:
            best = tier
    return best


def extract_hostname(url: str) -> str:
    """Extract the normalized hostname from a URL (without www prefix)."""
    if not url:
        return ""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return ""
    return hostname.lower().lstrip("www.")
