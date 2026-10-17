"""元信息端点（SPEC §7.13）：数据来源与标准科目表。

答辩现场要能回答"数据哪来的、覆盖到哪一年"，所以 `coverage` 是逐校逐类目算出来的，
不是写死的文案。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import response
from ..core import subjects as subject_table
from ..services import school_data

router = APIRouter(tags=["meta"])

_LEVELS = ("official", "official_summary", "inferred")


def _norm_level(level: str | None) -> str:
    level = (level or "official").strip()
    return level if level in _LEVELS else "official"


@router.get("/meta/sources")
async def meta_sources():
    """N24：数据来源总览（crawl_time / coverage / total_records / levels）。"""
    coverage = []
    levels = {k: 0 for k in _LEVELS}
    total = 0
    crawl_times: list[str] = []

    for school_id in school_data.school_ids():
        school = school_data.get(school_id) or {}
        categories: dict[str, int] = {}
        for rec in school.get("records", []):
            category = rec.get("category") or "unknown"
            year = rec.get("data_year") or 0
            categories[category] = max(categories.get(category, 0), year)
            levels[_norm_level(rec.get("level"))] += 1
            total += 1
            if rec.get("fetched_at"):
                crawl_times.append(rec["fetched_at"])
        for cat in school.get("catalogs", []):
            categories["program_catalog_pdf"] = max(
                categories.get("program_catalog_pdf", 0), cat.get("applicable_year") or 0
            )
            levels[_norm_level((cat.get("source") or {}).get("source_level"))] += 1
            total += 1
        coverage.append({
            "school_id": school_id,
            "school_name": school.get("school_name"),
            "categories": sorted(categories.keys()),
            "latest_year": max(categories.values()) if categories else None,
            "category_years": categories,
            "record_count": len(school.get("records", [])) + len(school.get("catalogs", [])),
        })

    return response.ok({
        "crawl_time": max(crawl_times) if crawl_times else school_data.crawl_time(),
        "coverage": coverage,
        "total_records": total,
        "levels": levels,
        "school_ids": school_data.school_ids(),
        "notes": [
            "levels.official=官方原文（可点回原文链接）；official_summary=官方汇总口径；inferred=推断值，一律标记为 judgement。",
            "latest_year 取该类目下最新数据年份；页面重发日期不等于数据年份，年份以标题/正文为准。",
        ],
    })


@router.get("/meta/subjects")
async def meta_subjects():
    """N31：标准科目表（创新点 1 的公共词典）。"""
    return response.ok(subject_table.as_meta())
