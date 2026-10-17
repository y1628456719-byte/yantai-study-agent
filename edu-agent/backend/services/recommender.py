"""院校推荐（SPEC §7.4）。

设计原则（SPEC §4.2 / §11）：
- **排序与分档 = 硬规则**（N8），模型只写理由文本 → 模型出错不会污染判断逻辑
- 理由/风险/未知项/来源**四个字段严格分离**，不混成一段长文本
- 每条理由带 `source_index` 并能区分 `kind: fact|judgement`（创新点 2）
- 拿不到的维度进 `unknowns`，绝不编造（SPEC §4.8）
"""
from __future__ import annotations

import re

from ..core import dimensions
from ..core import subjects as subject_table
from . import school_data

TIER_ORDER = {"冲": 0, "稳": 1, "保": 2}
#: N8 硬规则阈值：复试线与目标分的差值带
TIER_BAND = 10
#: N8 硬规则阈值：统考招生规模低于此值视为风险，分档上调一档
SMALL_SCALE = 15


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _val(item: dict, code: str):
    """取某一维度的裸值（兼容 metric 对象 / 裸标量 / None）。"""
    raw = (item.get("metrics") or {}).get(code)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _src_index(item: dict, code: str) -> int | None:
    raw = (item.get("metrics") or {}).get(code)
    if isinstance(raw, dict):
        return raw.get("source_index")
    return None


def _reason(item: dict, code: str, text: str, kind: str = "fact") -> dict:
    reason = {"text": text, "kind": kind}
    src = _src_index(item, code)
    if src is not None:
        reason["source_index"] = src
    return reason


def _single_line_text(single: dict) -> str:
    """把单科线拼成人话。

    ⚠️ 两种数据形态必须区别对待（否则会出现"政治 315 / 业务课二 None"这种错话）：
    * 逐科表格（吉大）：有 politics / foreign / biz1 / biz2 → 逐科列出，None 的直接跳过；
    * 图片转录的通栏表（东北大学）：官方只给"单科满分=100"和"单科满分>100"两列
      → 用后者的口径描述，不能硬套"政治/外语"。
    """
    parts: list[str] = []
    labels = (("politics", "政治"), ("foreign", "外语"),
              ("subject_one", "业务课一"), ("subject_two", "业务课二"))
    for key, label in labels:
        value = single.get(key)
        if value is not None:
            parts.append(f"{label} {value}")
    if parts:
        return "单科线 " + " / ".join(parts)
    generic = []
    if single.get("single_100") is not None:
        generic.append(f"满分 100 的科目 {single['single_100']}")
    if single.get("single_150") is not None:
        generic.append(f"满分 >100 的科目 {single['single_150']}")
    if generic:
        return "单科线 " + " / ".join(generic)
    return ""


def target_from_profile(profile: dict) -> tuple[int | None, str | None]:
    """从档案里取"用户目标区间"（N8 的输入）。

    只在**明确表达分数意图**的上下文里取值，避免把科目代码（如 301）误当目标分。
    """
    profile = profile or {}
    explicit = profile.get("target_score")
    if isinstance(explicit, int) and 100 <= explicit <= 500:
        return explicit, "profile.target_score"
    text = " ".join(str(profile.get(k) or "") for k in ("preferences", "background", "major"))
    for pattern, label in (
        (r"目标[^\d]{0,4}(\d{3})", "profile.preferences（目标 NNN）"),
        (r"(\d{3})\s*分", "profile.preferences（NNN 分）"),
        (r"考\s*(\d{3})", "profile.preferences（考 NNN）"),
    ):
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            if 100 <= value <= 500:
                return value, label
    return None, None


# --------------------------------------------------------------------------
# N8：硬规则分档
# --------------------------------------------------------------------------

def _harder(tier: str | None) -> str | None:
    if tier is None:
        return None
    order = ["保", "稳", "冲"]
    return order[min(2, order.index(tier) + 1)]


def _relative_tier(idx: int, n: int) -> str | None:
    """按名次相对分档；**候选院校只有 2 所时给 冲/保**（不虚构中间档）。"""
    if n <= 1:
        return None
    if n == 2:
        return "冲" if idx == 0 else "保"
    ratio = idx / n
    return "冲" if ratio < 1 / 3 else ("稳" if ratio < 2 / 3 else "保")


def assign_tiers(items: list[dict], target_score: int | None) -> str:
    """给每项写入 `metrics.tier`，返回本次分档依据（供 profile_echo/说明）。"""
    for item in items:
        item.setdefault("metrics", {})["tier"] = None
        item["_tier_basis"] = None
        item["_tier_adjustments"] = []

    if target_score is not None:
        basis = f"规则：复试线 − 目标分 {target_score}（差值 >{TIER_BAND} 为冲，{TIER_BAND}~−{TIER_BAND} 为稳，<−{TIER_BAND} 为保）"
        for item in items:
            total = _val(item, "score_line_total")
            if total is None:
                item["_tier_basis"] = "该校复试线缺失，未分档"
                continue
            diff = int(total) - target_score
            tier = "冲" if diff > TIER_BAND else ("保" if diff < -TIER_BAND else "稳")
            item["metrics"]["tier"] = tier
            item["_tier_basis"] = f"{basis}；本例 {total} − {target_score} = {diff:+d} → {tier}"
    else:
        scored = [it for it in items if _val(it, "score_line_total") is not None]
        scored.sort(key=lambda it: -int(_val(it, "score_line_total")))
        basis = "规则：未提供目标分，按候选院校复试线高低相对分档（高位为冲、中位为稳、低位为保）"
        for idx, item in enumerate(scored):
            tier = _relative_tier(idx, len(scored))
            if tier is None:
                item["_tier_basis"] = "只有 1 条可比较的复试线且未提供目标分，按规则不分档（如实返回 null）"
                continue
            item["metrics"]["tier"] = tier
            item["_tier_basis"] = f"{basis}；{len(scored)} 所中第 {idx + 1} 位 → {tier}"
        for item in items:
            if item["metrics"].get("tier") is None:
                item["_tier_basis"] = item.get("_tier_basis") or "该校复试线缺失，未分档"

    # 硬规则修正 1：诊断出的薄弱项落在该校初试科目上 → 风险上调一档（创新点 1 的分档联动）
    for item in items:
        tier = item["metrics"].get("tier")
        hit = next((f for f in (item.get("ability_flags") or [])
                    if f.get("status") == "weak" and f.get("direction") == "hit"), None)
        if tier and hit:
            harder = _harder(tier)
            if harder != tier:
                item["metrics"]["tier"] = harder
                note = (f"初试科目含你诊断出的薄弱项「{hit.get('topic_name')}」"
                        f"（{hit.get('evidence_count')} 条实测证据），风险上调一档：{tier} → {harder}")
                item["_tier_adjustments"].append(note)
                item["risks"].append(_reason(item, "ability_match", note, kind="judgement"))
            else:
                item["_tier_adjustments"].append(
                    f"初试科目含你诊断出的薄弱项「{hit.get('topic_name')}」，但已是最高风险档「{tier}」，"
                    f"不再上调（风险以风险条目体现）"
                )

    # 硬规则修正 2：招生规模过小 → 风险上调一档
    for item in items:
        tier = item["metrics"].get("tier")
        scale = _val(item, "admission_scale")
        if tier and isinstance(scale, (int, float)) and scale < SMALL_SCALE:
            harder = _harder(tier)
            if harder != tier:
                item["metrics"]["tier"] = harder
                item["_tier_adjustments"].append(
                    f"统考招生规模仅 {int(scale)} 人（<{SMALL_SCALE}），风险上调一档：{tier} → {harder}"
                )
                item["risks"].append(_reason(
                    item, "admission_scale",
                    f"统考招生规模仅 {int(scale)} 人，容错空间小（硬规则据此把分档上调为 {harder}）",
                ))
        elif tier and scale is None:
            item["_tier_adjustments"].append("统考招生规模未知，未做规模修正（如实标注，不猜测）")
    return basis


# --------------------------------------------------------------------------
# N5：淘汰名单（否定性判断，只依据可核验事实）
# --------------------------------------------------------------------------

def build_excluded(candidates: list[dict], profile: dict) -> list[dict]:
    excluded: list[dict] = []
    is_cross = bool(profile.get("is_cross_major"))
    study_status = str(profile.get("study_status") or "")
    for item in candidates:
        accepts = _val(item, "accepts_cross_major")
        if accepts is False and is_cross:
            excluded.append({
                "school_id": item["school_id"],
                "school_name": item["school_name"],
                "reason": "该专业招生目录明确不招收跨学科考生，而你的档案标记为跨专业报考",
                "kind": "fact",
                "source_index": _src_index(item, "accepts_cross_major"),
            })
            continue
        degree = _val(item, "degree_type")
        if degree and "非全日制" in str(degree) and study_status == "在校":
            excluded.append({
                "school_id": item["school_id"],
                "school_name": item["school_name"],
                "reason": "可匹配的专业为非全日制，与你的在校生身份不匹配（如你实际考虑非全，请修改档案）",
                "kind": "fact",
                "source_index": _src_index(item, "degree_type"),
            })
            continue
        equivalent = _val(item, "equivalent_requirement")
        if equivalent and "不招收同等学力" in str(equivalent) and study_status == "同等学力":
            excluded.append({
                "school_id": item["school_id"],
                "school_name": item["school_name"],
                "reason": "该专业明确不招收同等学力考生",
                "kind": "fact",
                "source_index": _src_index(item, "equivalent_requirement"),
            })
    return excluded


# --------------------------------------------------------------------------
# N6：理由 / 风险 / 未知项
# --------------------------------------------------------------------------

def build_reasons(item: dict, profile: dict) -> None:
    metrics = item["metrics"]
    internal = item.get("internal") or {}
    total = _val(item, "score_line_total")
    if total is not None:
        raw = metrics.get("score_line_total") or {}
        year = raw.get("year") if isinstance(raw, dict) else None
        label = f"{year} 年" if year else "最近一年"
        item["fit_reasons"].append(_reason(
            item, "score_line_total", f"{label}复试线 {int(total)} 分（{internal.get('_score_line_row') or '工学门类'}）"
        ))
    single = internal.get("_score_line_singles") or {}
    single_text = _single_line_text(single)
    if single_text:
        item["fit_reasons"].append(_reason(item, "score_line_single", single_text))
    scale = _val(item, "admission_scale")
    if isinstance(scale, (int, float)):
        college = (internal.get("_college_name") or "").strip()
        program_name = (internal.get("_program_name") or "").strip()
        who = " ".join(x for x in (college, program_name) if x)
        item["fit_reasons"].append(_reason(
            item, "admission_scale",
            f"{who}统考招生 {int(scale)} 人" if who else f"统考招生 {int(scale)} 人",
        ))
    books = _val(item, "reference_books_count")
    if books:
        item["fit_reasons"].append(_reason(
            item, "reference_books_count", f"官方指定参考书 {int(books)} 本，复习范围明确"
        ))
    years = _val(item, "duration_years")
    if years:
        item["fit_reasons"].append(_reason(item, "duration_years", f"学制 {int(years)} 年"))
    tuition = _val(item, "tuition")
    if isinstance(tuition, (int, float)):
        per_year = (internal.get("_tuition_per_year") or 0)
        suffix = f"（约 {int(per_year)} 元/年）" if per_year else ""
        item["fit_reasons"].append(_reason(item, "tuition", f"学费 {int(tuition)} 元{suffix}"))
    city = _val(item, "city")
    regions = profile.get("preferred_regions") or []
    if city:
        if regions and city in regions:
            item["fit_reasons"].append(_reason(
                item, "city", f"城市 {city} 在你选择的地域（{'、'.join(regions)}）内", kind="judgement"
            ))
        elif regions:
            item["risks"].append(_reason(
                item, "city", f"城市 {city} 不在你选择的地域（{'、'.join(regions)}）内", kind="judgement"
            ))
    accepts = _val(item, "accepts_cross_major")
    if accepts is True:
        item["fit_reasons"].append(_reason(item, "accepts_cross_major", "招生目录声明招收跨学科考生，不加试"))
    equivalent = _val(item, "equivalent_requirement")
    if equivalent:
        item["fit_reasons"].append(_reason(item, "equivalent_requirement", str(equivalent)))

    # 薄弱项与考试科目重合（创新点 1 的展示点）
    for flag in item.get("ability_flags", []):
        if flag.get("status") == "weak" and flag.get("direction") == "hit":
            text = (f"该校初试科目含 {flag.get('matched_exam_subject_name') or flag.get('matched_exam_subject')}，"
                    f"与你诊断出的薄弱项「{flag.get('topic_name')}」重合")
            item["risks"].append(_reason(item, "ability_match", text, kind="judgement"))
        elif flag.get("status") in ("solid", "ok") and flag.get("direction") == "solid":
            text = (f"该校初试科目含 {flag.get('matched_exam_subject_name') or flag.get('matched_exam_subject')}，"
                    f"属于你已确认掌握的部分")
            item["fit_reasons"].append(_reason(item, "ability_match", text, kind="judgement"))

    # 数据缺失本身是风险（诚实边界）
    missing_relevant = [c for c in ("score_line_total", "admission_scale", "exam_subjects", "tuition")
                        if _val(item, c) in (None, [], {})]
    if missing_relevant:
        item["risks"].append({
            "text": "以下关键维度缺失，判断存在盲区：" + "、".join(dimensions.label(c) for c in missing_relevant),
            "kind": "judgement",
        })
    for conflict in item.get("data_conflicts", []):
        item["risks"].append({
            "text": f"数据冲突：{conflict.get('note')}（{dimensions.label(conflict.get('dimension'))}）",
            "kind": "fact",
            "source_index": conflict.get("catalog_source_index"),
        })


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def _score_only_match(school_id: str, major_hint: str) -> dict | None:
    """Use only an exact historical score row; never borrow another major's catalog."""
    school = school_data.get(school_id) or {}
    records = sorted((r for r in school.get('records', []) if r.get('category') == 'score_line'),
                     key=lambda r: int(r.get('data_year') or 0), reverse=True)
    wanted = re.sub(r'\s+', '', major_hint)
    for record in records:
        for table in school_data._score_line_tables(record):
            columns = school_data._map_columns(table)
            for row in table[1:]:
                if not row:
                    continue
                label = str(row[0])
                name = re.sub(r'[\[［【（(].*?[\]］】）)]', '', label)
                if re.sub(r'\s+', '', name) != wanted:
                    continue
                index = columns.get('total', 1)
                if index >= len(row):
                    continue
                raw = str(row[index]).strip().replace(' ', '')
                if not raw.isdigit():
                    continue
                source = next((s for s in school_data.sources(school_id) if s.get('url') == record.get('url')), None)
                if not source:
                    continue
                return {
                    'metrics': {'score_line_total': school_data.metric(int(raw),record.get('data_year'),0),
                                'city': school.get('city'), '_score_line_row':label,
                                '_catalog_missing':True},
                    'unknowns':['当前仅匹配到该方向的历史复试线；招生目录、当年是否招生、人数、考试科目、学费均待核对。'],
                    'data_conflicts':[], 'sources':[source],
                }
    return None


def recommend(session: dict, *, limit: int | None = None) -> dict:
    """生成推荐结果（不含模型文案；`understood_as` 由调用方补）。"""
    profile = session.get("profile") or {}
    learning_state = session.get("learning_state") or {}
    ability_profile = learning_state.get("ability_profile") or {}
    shortlist = [sid for sid in (session.get("shortlist") or []) if sid]

    major_hint = profile.get("target_major") or profile.get("major")
    schools = shortlist or school_data.school_ids()

    candidates: list[dict] = []
    unmatched: list[dict] = []
    for school_id in schools:
        program, _method = school_data.pick_program(school_id, major_hint)
        hint = str(major_hint or '').strip()
        matched = not hint or bool(program and (hint in str(program.get('program_name') or '') or
                                   (hint.isdigit() and str(program.get('program_code') or '').startswith(hint))))
        if matched:
            built = school_data.build_metrics(school_id, program=program, major_hint=major_hint)
        else:
            built = _score_only_match(school_id,hint)
            if built is None:
                unmatched.append({'school_id':school_id,'school_name':school_data.display_name(school_id),
                    'reason':f'当前资料未覆盖「{hint}」的匹配专业，暂不能据此推荐；这不表示该校不招收此方向。',
                    'kind':'judgement'})
                continue
        if not built.get("metrics"):
            continue
        metrics = {k: v for k, v in built["metrics"].items() if not k.startswith("_")}
        internal = {k: v for k, v in built["metrics"].items() if k.startswith("_")}
        item = {
            "school_id": school_id,
            "school_name": school_data.display_name(school_id),
            "metrics": metrics,
            "fit_reasons": [],
            "risks": [],
            "unknowns": list(built["unknowns"]),
            "data_conflicts": built["data_conflicts"],
            "sources": built["sources"],
            "ability_flags": [],
            "internal": internal,
        }
        codes = metrics.get("exam_subject_codes") or []
        if ability_profile and codes:
            item["ability_flags"] = subject_table.ability_flags(ability_profile, codes)
            hits_n = sum(1 for f in item["ability_flags"] if f.get("direction") == "hit")
            metrics["ability_match"] = {
                "value": hits_n,
                "differentiator": "weak_topic_overlap" if hits_n else "no_overlap",
                "evidence_count": sum(int(f.get("evidence_count") or 0) for f in item["ability_flags"]),
                "kind": "judgement",
            }
        regions = profile.get("preferred_regions") or []
        city = metrics.get("city")
        if regions and city:
            metrics["preference_match"] = {
                "value": city in regions,
                "detail": f"{city} {'在' if city in regions else '不在'}你选择的地域（{'、'.join(regions)}）",
                "kind": "judgement",
            }
        candidates.append(item)

    for item in candidates:
        build_reasons(item, profile)

    target_score, target_source = target_from_profile(profile)
    tier_basis = assign_tiers(candidates, target_score)
    for item in candidates:
        if item['internal'].get('_catalog_missing'):
            item['metrics']['tier'] = None
            item['_tier_basis'] = '仅有历史复试线，专业招生资料未齐，暂不作适配分档'
            item['risks'].insert(0,{'text':'仅有历史复试线，不能据此确认当年招生或专业匹配程度。','kind':'judgement'})

    excluded = build_excluded(candidates, profile) + unmatched
    excluded_ids = {e["school_id"] for e in excluded}
    kept = [it for it in candidates if it["school_id"] not in excluded_ids]

    kept.sort(key=lambda it: (
        TIER_ORDER.get(it["metrics"].get("tier"), 9),
        -(_val(it, "admission_scale") or 0),
        -(_val(it, "score_line_total") or 0),
    ))
    if limit:
        kept = kept[:limit]

    hits, missing = dimensions.split_hits({k: v for it in kept for k, v in it["metrics"].items()})
    aggregated_flags = _aggregate_ability_flags(kept)

    return {
        "recommendations": [_public_item(it) for it in kept],
        "excluded": excluded,
        "dimension_hits": hits,
        "dimension_missing": missing,
        "profile_echo": {
            "exam_year": profile.get("exam_year"),
            "major": profile.get("major"),
            "preferred_regions": profile.get("preferred_regions") or [],
            "daily_minutes": profile.get("daily_minutes"),
            "target_score": target_score,
            "target_score_source": target_source,
            "understood_as": None,
        },
        "tier_rule": tier_basis,
        "ability_flags": aggregated_flags,
        "ability_profile_echo": ability_profile or None,
        "is_demo": bool(session.get("is_demo")),
    }


def _public_item(item: dict) -> dict:
    return {
        "school_id": item["school_id"],
        "school_name": item["school_name"],
        "metrics": item["metrics"],
        "fit_reasons": item["fit_reasons"],
        "risks": item["risks"],
        "unknowns": item["unknowns"],
        "data_conflicts": item["data_conflicts"],
        "sources": item["sources"],
        "ability_flags": item["ability_flags"],
        "tier_note": item.get("_tier_basis"),
        "tier_adjustments": item.get("_tier_adjustments") or [],
    }


def _aggregate_ability_flags(items: list[dict]) -> list[dict]:
    """N27：跨院校聚合，给出 `affected_schools`。"""
    merged: dict[tuple, dict] = {}
    for item in items:
        for flag in item.get("ability_flags") or []:
            key = (flag.get("topic"), flag.get("status"), flag.get("basis"), flag.get("direction"))
            entry = merged.setdefault(key, {
                "topic": flag.get("topic"),
                "topic_name": flag.get("topic_name"),
                "status": flag.get("status"),
                "evidence_count": flag.get("evidence_count"),
                "basis": flag.get("basis"),
                "direction": flag.get("direction"),
                "affected_schools": [],
            })
            entry["affected_schools"].append(item["school_id"])
    return list(merged.values())
