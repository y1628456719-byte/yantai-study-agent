"""N32 推荐维度全量表（SPEC §6）。

33 个**决策维度**（参与排序/展示依据）+ 3 个**数据质量元维度**（每校必带，不参与排序）。
口径统一为：**33 个决策维度 + 3 个数据质量元维度**（避免答辩时被数出 33/36 不一致）。

`value_kind` 说明（这是 v1.1 统一 `metrics` 结构的依据）：
- ``scalar_year``：值必须写成 ``{"value": ..., "year": ..., "source_index": ...}``
- ``scalar``：纯标量白名单（仅 ``city`` / ``tier`` / ``degree_type`` 这类无年份无来源争议的值）
- ``list``：值本身是数组（如 ``exam_subjects``）
- ``derived``：后端由其它维度**计算**得出（如 ``line_vs_national``），必须能给出算式
- ``internal``：后端内部推断（如 ``ability_match`` / ``preference_match``），必须能给出依据
"""
from __future__ import annotations

# 纯标量白名单：只有这些维度允许直接写裸值
SCALAR_WHITELIST = {"city", "tier", "degree_type"}


DIMENSIONS: list[dict] = [
    # A. 录取难度（硬门槛类）
    {"code": "score_line_total", "name": "近三年复试线（总分）", "group": "A", "group_name": "录取难度", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "score_line_single", "name": "复试线单科要求", "group": "A", "group_name": "录取难度", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "line_vs_national", "name": "复试线 − 国家线差值", "group": "A", "group_name": "录取难度", "level": "A", "ranking": True, "value_kind": "derived"},
    {"code": "admission_ratio", "name": "报录比", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "admitted_min_score", "name": "拟录取最低分/平均分", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "re_exam_ratio", "name": "复试差额比例", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "score_weight", "name": "初试/复试成绩权重", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "accepts_transfer", "name": "是否接收调剂/调剂名额", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "protects_first_choice", "name": "是否保护一志愿", "group": "A", "group_name": "录取难度", "level": "C", "ranking": True, "value_kind": "scalar_year"},
    {"code": "score_deflation", "name": "是否压分", "group": "A", "group_name": "录取难度", "level": "D", "ranking": True, "value_kind": "scalar_year"},
    {"code": "past_papers_public", "name": "专业课真题是否公开", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "exam_subjects", "name": "初试考试科目", "group": "A", "group_name": "录取难度", "level": "A", "ranking": True, "value_kind": "list"},
    {"code": "reference_books_count", "name": "参考书目数量", "group": "A", "group_name": "录取难度", "level": "B", "ranking": True, "value_kind": "scalar_year"},

    # B. 机会与门槛（资格类）
    {"code": "recommend_ratio", "name": "推免占比", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "admission_scale", "name": "统考招生规模", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "accepts_cross_major", "name": "是否接受跨考", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "undergrad_requirement", "name": "本科背景限制", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "equivalent_requirement", "name": "同等学力/四六级要求", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "degree_type", "name": "学硕/专硕·全日制/非全日制", "group": "B", "group_name": "机会与门槛", "level": "A", "ranking": True, "value_kind": "scalar"},

    # C. 培养与投入
    {"code": "duration_years", "name": "学制", "group": "C", "group_name": "培养与投入", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "tuition", "name": "学费", "group": "C", "group_name": "培养与投入", "level": "A", "ranking": True, "value_kind": "scalar_year"},
    {"code": "scholarship", "name": "奖助学金覆盖", "group": "C", "group_name": "培养与投入", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "discipline_rating", "name": "学科评估等级", "group": "C", "group_name": "培养与投入", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "has_phd", "name": "是否有博士点", "group": "C", "group_name": "培养与投入", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "advisor_count", "name": "导师数量与研究方向", "group": "C", "group_name": "培养与投入", "level": "B", "ranking": True, "value_kind": "scalar_year"},

    # D. 地理与生活
    {"code": "city", "name": "城市", "group": "D", "group_name": "地理与生活", "level": "A", "ranking": True, "value_kind": "scalar"},
    {"code": "living_cost", "name": "生活成本", "group": "D", "group_name": "地理与生活", "level": "C", "ranking": True, "value_kind": "scalar_year"},
    {"code": "distance_from_home", "name": "与常驻地距离", "group": "D", "group_name": "地理与生活", "level": "C", "ranking": True, "value_kind": "scalar_year"},
    {"code": "regional_industry", "name": "区域产业与就业市场", "group": "D", "group_name": "地理与生活", "level": "B", "ranking": True, "value_kind": "scalar_year"},

    # E. 前景与匹配
    {"code": "graduate_destination", "name": "毕业生去向", "group": "E", "group_name": "前景与匹配", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "alumni_records", "name": "直系学长学姐录取记录", "group": "E", "group_name": "前景与匹配", "level": "B", "ranking": True, "value_kind": "scalar_year"},
    {"code": "ability_match", "name": "与用户能力画像的匹配度", "group": "E", "group_name": "前景与匹配", "level": "C", "ranking": True, "value_kind": "internal"},
    {"code": "preference_match", "name": "用户偏好对齐度", "group": "E", "group_name": "前景与匹配", "level": "C", "ranking": True, "value_kind": "internal"},
]

META_DIMENSIONS: list[dict] = [
    {"code": "missing_fields", "name": "信息缺失项数量", "ranking": False},
    {"code": "latest_data_year", "name": "最新数据年份", "ranking": False},
    {"code": "policy_change_flag", "name": "政策变动提示", "ranking": False},
]

ALL_DIMENSIONS = DIMENSIONS + META_DIMENSIONS
BY_CODE = {d["code"]: d for d in ALL_DIMENSIONS}
RANKING_CODES = [d["code"] for d in DIMENSIONS]
DECISION_DIMENSION_COUNT = len(DIMENSIONS)          # 33
META_DIMENSION_COUNT = len(META_DIMENSIONS)         # 3


def get(code: str) -> dict | None:
    return BY_CODE.get(code)


def label(code: str) -> str:
    d = BY_CODE.get(code)
    return f"{d['name']}（{code}）" if d else code


def _has_value(value) -> bool:
    """维度是否真的被用上：None / 空数组 / value 为 None 都算缺失。"""
    if value is None:
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    if isinstance(value, dict) and "value" in value and value.get("value") is None:
        return False
    return True


def split_hits(metrics: dict) -> tuple[list[str], list[str]]:
    """把某校 ``metrics`` 拆成 (被真正用上的维度, 因缺数据未参与的维度)。

    顺序始终按全量表顺序，保证前端渲染稳定、demo 可预期。
    """
    hits, missing = [], []
    for code in RANKING_CODES:
        (hits if _has_value(metrics.get(code)) else missing).append(code)
    return hits, missing
