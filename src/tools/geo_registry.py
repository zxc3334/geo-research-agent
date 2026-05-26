"""GIS / remote-sensing structured registry tools.

These tools provide deterministic, evidence-shaped facts for the GeoResearch
MVP. They are intentionally small and curated; M4 can later replace or augment
them with STAC search, official documentation retrieval, or local literature RAG.
"""
from __future__ import annotations

from typing import Any


DATASETS: dict[str, dict[str, Any]] = {
    "landsat": {
        "dataset": "Landsat 8/9 Collection 2",
        "aliases": ["landsat", "landsat 8", "landsat 9", "oli", "tirs"],
        "variables": ["surface reflectance", "thermal infrared", "lst_candidate", "ndvi", "ndbi"],
        "spatial_resolution": "30 m multispectral; TIRS thermal bands acquired at 100 m and commonly resampled to 30 m",
        "temporal_resolution": "16 days per satellite; 8 days for Landsat 8/9 combined when both are available",
        "required_bands": {
            "NDVI": ["red", "near infrared"],
            "NDBI": ["shortwave infrared", "near infrared"],
            "LST": ["thermal infrared Band 10", "emissivity", "atmospheric correction inputs"],
        },
        "strengths": [
            "Long-term archive suitable for multi-year urban thermal studies.",
            "Thermal infrared information supports LST retrieval.",
        ],
        "limitations": [
            "Cloud contamination can strongly reduce usable scenes.",
            "Band 11 on Landsat 8 has known stray-light concerns; Band 10 is commonly preferred for single-channel LST.",
            "Morning overpass cannot characterize full diurnal or nocturnal UHI behavior.",
        ],
        "official_sources": [
            {
                "title": "USGS Landsat Collection 2",
                "url": "https://www.usgs.gov/landsat-missions/landsat-collection-2",
            },
            {
                "title": "USGS Landsat 8-9 OLI/TIRS",
                "url": "https://www.usgs.gov/landsat-missions/landsat-8",
            },
        ],
    },
    "sentinel-2": {
        "dataset": "Sentinel-2 MSI Level-2A",
        "aliases": ["sentinel-2", "sentinel 2", "s2", "msi"],
        "variables": ["surface reflectance", "ndvi", "ndbi", "land cover", "urban expansion"],
        "spatial_resolution": "10 m visible/NIR; 20 m red-edge/SWIR; 60 m atmospheric bands",
        "temporal_resolution": "About 5 days with Sentinel-2A/B constellation",
        "required_bands": {
            "NDVI": ["B4 red", "B8 NIR"],
            "NDBI": ["B11 SWIR", "B8 NIR"],
            "LST": [],
        },
        "strengths": [
            "High spatial resolution for urban expansion and vegetation indices.",
            "Frequent revisit improves cloud-free composite generation.",
        ],
        "limitations": [
            "No thermal infrared band; cannot directly retrieve LST.",
            "SWIR bands are 20 m, so index workflows may require resampling.",
        ],
        "official_sources": [
            {
                "title": "ESA Sentinel-2 Mission",
                "url": "https://sentinel.esa.int/web/sentinel/missions/sentinel-2",
            }
        ],
    },
    "modis-lst": {
        "dataset": "MODIS Land Surface Temperature products",
        "aliases": ["modis", "mod11", "myd11", "modis lst"],
        "variables": ["lst", "thermal time series"],
        "spatial_resolution": "Typically 1 km for standard daily LST products",
        "temporal_resolution": "Daily and composite products are available",
        "required_bands": {
            "LST": ["thermal infrared split-window product", "quality control flags"],
        },
        "strengths": [
            "High temporal frequency supports time-series thermal analysis.",
            "Useful for cross-checking Landsat LST temporal consistency.",
        ],
        "limitations": [
            "Coarse spatial resolution is not enough for detailed intra-urban thermal patterns.",
            "Scale mismatch with Landsat/Sentinel requires aggregation or downscaling decisions.",
        ],
        "official_sources": [
            {
                "title": "NASA MODIS Land Surface Temperature",
                "url": "https://modis.gsfc.nasa.gov/data/dataprod/mod11.php",
            }
        ],
    },
    "era5-land": {
        "dataset": "ERA5-Land",
        "aliases": ["era5", "era5-land", "reanalysis"],
        "variables": ["air temperature", "skin temperature", "meteorological covariates"],
        "spatial_resolution": "Approximately 9 km grid",
        "temporal_resolution": "Hourly",
        "required_bands": {},
        "strengths": [
            "Useful as meteorological context and gap-filling reference.",
            "Long time series and hourly temporal resolution.",
        ],
        "limitations": [
            "Too coarse for parcel-scale or neighborhood-scale UHI mapping.",
            "Reanalysis variables are not equivalent to satellite LST.",
        ],
        "official_sources": [
            {
                "title": "Copernicus ERA5-Land",
                "url": "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
            }
        ],
    },
}


METHODS: dict[str, dict[str, Any]] = {
    "ndvi": {
        "method": "NDVI",
        "aliases": ["ndvi", "vegetation index"],
        "formula": "(NIR - Red) / (NIR + Red)",
        "required_inputs": ["red reflectance", "near infrared reflectance"],
        "valid_for": ["vegetation cover proxy", "emissivity estimation support"],
        "limitations": ["Saturates in dense vegetation.", "Not a direct urban expansion indicator."],
    },
    "ndbi": {
        "method": "NDBI",
        "aliases": ["ndbi", "built-up index", "building index"],
        "formula": "(SWIR - NIR) / (SWIR + NIR)",
        "required_inputs": ["shortwave infrared reflectance", "near infrared reflectance"],
        "valid_for": ["built-up surface proxy", "urban expansion screening"],
        "limitations": ["Can confuse bare soil with built-up surfaces.", "Should be validated with land-cover classification or samples."],
    },
    "lst-single-channel": {
        "method": "Landsat single-channel LST retrieval",
        "aliases": ["lst", "land surface temperature", "single channel", "band 10"],
        "formula": "Thermal radiance/brightness temperature + emissivity and atmospheric correction",
        "required_inputs": ["thermal infrared band", "surface emissivity", "atmospheric correction parameters"],
        "valid_for": ["Landsat-based land surface temperature retrieval"],
        "limitations": ["Sensitive to emissivity and atmospheric correction.", "Cloud and cloud-shadow masking is mandatory."],
    },
    "gwr": {
        "method": "Geographically Weighted Regression",
        "aliases": ["gwr", "geographically weighted regression"],
        "formula": "Local regression with spatially varying coefficients",
        "required_inputs": ["dependent variable such as LST", "spatial predictors such as NDVI/NDBI/impervious surface", "coordinates"],
        "valid_for": ["spatial non-stationarity analysis"],
        "limitations": ["Bandwidth choice affects results.", "Spatial autocorrelation and multicollinearity must be checked."],
    },
}


class DatasetRegistryTool:
    """Return curated dataset compatibility facts for GIS/remote-sensing tasks."""

    name = "dataset_registry"
    description = (
        "Look up curated GIS/remote-sensing dataset capabilities and limitations. "
        "Use this before proposing sensors or variables. "
        "Input: {'query': str}. Output includes candidates, limitations, and official sources."
    )

    def get_openai_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Dataset, sensor, variable, or research need."},
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, query: str) -> dict[str, Any]:
        query_lower = query.lower()
        matches = []
        for key, record in DATASETS.items():
            aliases = [key] + record.get("aliases", [])
            variables = record.get("variables", [])
            if any(alias in query_lower for alias in aliases) or any(var in query_lower for var in variables):
                matches.append(record)

        if not matches:
            matches = list(DATASETS.values())

        return {
            "query": query,
            "registry_type": "dataset",
            "evidence_level": "verified",
            "results": matches,
        }


class MethodRegistryTool:
    """Return curated method formulas, inputs, and limitations."""

    name = "method_registry"
    description = (
        "Look up curated remote-sensing/GIS method formulas, required inputs, valid use cases, and limitations. "
        "Input: {'query': str}. Output includes formula, inputs, and caveats."
    )

    def get_openai_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Method name or analysis goal."},
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, query: str) -> dict[str, Any]:
        query_lower = query.lower()
        matches = []
        for key, record in METHODS.items():
            aliases = [key] + record.get("aliases", [])
            if any(alias in query_lower for alias in aliases):
                matches.append(record)

        if not matches:
            matches = list(METHODS.values())

        return {
            "query": query,
            "registry_type": "method",
            "evidence_level": "verified",
            "results": matches,
        }


class GeoPlanValidatorTool:
    """Validate common GIS/remote-sensing plan compatibility issues."""

    name = "geo_plan_validator"
    description = (
        "Validate a GIS/remote-sensing research plan for dataset/method compatibility. "
        "Checks known pitfalls such as Sentinel-2 direct LST retrieval, missing thermal bands, CRS/resolution/temporal consistency. "
        "Input: {'plan': str}. Output includes passed checks, warnings, rejected claims, and required fixes."
    )

    def get_openai_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan": {"type": "string", "description": "Proposed GIS/remote-sensing workflow or claim to validate."},
                    },
                    "required": ["plan"],
                },
            },
        }

    async def execute(self, plan: str) -> dict[str, Any]:
        text = plan.lower()
        checks: list[dict[str, Any]] = []

        if "sentinel" in text and "lst" in text:
            checks.append({
                "level": "rejected",
                "claim": "Sentinel-2 can directly retrieve LST.",
                "reason": "Sentinel-2 MSI has no thermal infrared band; use Landsat/ECOSTRESS/MODIS for LST or treat Sentinel-2 as auxiliary surface reflectance.",
                "fix": "Use Sentinel-2 for NDVI/NDBI/land-cover and Landsat or MODIS for LST.",
            })

        if "landsat" in text and ("lst" in text or "地表温度" in text):
            checks.append({
                "level": "verified",
                "claim": "Landsat can support LST retrieval with thermal infrared data.",
                "reason": "Landsat 8/9 TIRS provides thermal infrared data; Band 10 is commonly used for single-channel LST workflows.",
                "fix": "Apply cloud masking, emissivity estimation, and atmospheric correction; document thermal-band resolution.",
            })

        if "modis" in text and "30m" in text:
            checks.append({
                "level": "speculative",
                "claim": "MODIS LST can be used directly at 30 m.",
                "reason": "Standard MODIS LST is coarse resolution; direct 30 m use requires downscaling or fusion and validation.",
                "fix": "Aggregate Landsat for comparison or explicitly implement and validate a downscaling workflow.",
            })

        if "ndbi" in text:
            checks.append({
                "level": "evidence_backed",
                "claim": "NDBI can support built-up area screening.",
                "reason": "NDBI uses SWIR and NIR but can confuse bare soil with built-up surfaces.",
                "fix": "Validate with classification samples or combine with NDVI/MNDWI/land-cover masks.",
            })

        if not checks:
            checks.append({
                "level": "speculative",
                "claim": "Plan needs more explicit dataset/method details before validation.",
                "reason": "No known deterministic compatibility rule was triggered.",
                "fix": "Specify sensor, target variable, bands, time range, AOI, CRS, and validation data.",
            })

        return {
            "registry_type": "geo_plan_validation",
            "evidence_level": "verified" if any(c["level"] == "verified" for c in checks) else "speculative",
            "checks": checks,
            "results": [
                {
                    "title": "GeoResearch built-in validator",
                    "url": "geo-registry://validator",
                    "snippet": "; ".join(f"{c['level']}: {c['claim']}" for c in checks),
                }
            ],
        }
