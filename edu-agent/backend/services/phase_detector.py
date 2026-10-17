"""N29 阶段推断服务（SPEC §5）。

**无状态纯函数**，便于单测。核心立场：**年级不是输入，是推断结果**——
所以这里只消费 `known_facts` 与已有条件，绝不默认"大三"。

规范化的 `known_facts` 键（缺失即 `None`，一律不猜）：

| 键 | 含义 |
|---|---|
| `exam_year_known` | 是否已确定报考年份 |
| `has_direction` | 是否已有目标专业方向 |
| `has_candidate_schools` | 是否已有候选院校 |
| `has_materials` | 手上是否已有复习资料 |
| `study_status` | `in_school` / `graduated` / `working` / `other` |
| `daily_minutes_known` | 是否已给出每日可投入时间 |

降级提问顺序（SPEC §5.4）：exam_year → 有无方向 → 有无候选校 → 有无资料 →（可选）状态。
"""
from __future__ import annotations

import re

PHASES = {
    "P0": "信息缺失期",
    "P1": "方向探索期",
    "P2": "目标筛选期",
    "P3": "备考执行期",
    "P4": "冲刺期",
}

KNOWN_FACT_KEYS = (
    "exam_year_known",
    "has_direction",
    "has_candidate_schools",
    "has_materials",
    "study_status",
    "daily_minutes_known",
)

# 降级提问顺序：每个问题对应的 known_fact 键 + 提问文案 + 缺失时是否算"关键缺失"
_QUESTIONS: list[tuple[str, str, bool]] = [
    ("exam_year_known", "你打算参加哪一年的考研？", True),
    ("has_direction", "有没有想考的专业方向？（不确定也可以说，我们一起看）", True),
    ("has_candidate_schools", "有没有已经看中的学校？", True),
    ("has_materials", "手上有没有已经在用的复习资料？没有也没关系，我们可以先做基础自测。", False),
    ("study_status", "你现在是在校、已毕业，还是边工作边考？", False),
]


def _guess_study_status(profile: dict) -> str | None:
    """从档案里的显式字段或自述文本**尽量**判断身份；判断不出返回 None（不猜）。

    早期版本只认 `profile.study_status`，于是"本科通信工程，在职备考"的用户
    仍然被问"你现在是在校、已毕业，还是边工作边考？"——用户会觉得系统没在听。
    """
    explicit = str(profile.get("study_status") or "").strip()
    if explicit:
        return explicit
    text = " ".join(str(profile.get(k) or "") for k in
                    ("background", "preferences", "notes", "major"))
    for pattern, value in (
        (r"同等学力", "同等学力"),
        (r"在上|在职|边工作边考|工作中", "边工作边考"),
        (r"应届|在校|大四|大三|大二|本科在读", "在校"),
        (r"已毕业|毕业(?:后|多年)|往届", "已毕业"),
    ):
        if re.search(pattern, text):
            return value
    return None


def infer_known_facts(profile: dict | None) -> dict:
    """从已有的 profile 里**尽量推断** known_facts；推不出来的保持 None。"""
    profile = profile or {}
    facts = dict(profile.get("known_facts") or {})

    if facts.get("exam_year_known") is None:
        facts["exam_year_known"] = True if profile.get("exam_year") else None
    if facts.get("has_direction") is None:
        if profile.get("target_major") or profile.get("major"):
            facts["has_direction"] = True
    if facts.get("daily_minutes_known") is None:
        if profile.get("daily_minutes"):
            facts["daily_minutes_known"] = True
    if facts.get("study_status") is None:
        facts["study_status"] = _guess_study_status(profile)
    return {k: facts.get(k) for k in KNOWN_FACT_KEYS}


def detect(
    profile: dict | None,
    *,
    selected_school_id: str | None = None,
    material_count: int = 0,
    has_shortlist: bool = False,
    today_iso: str | None = None,
) -> dict:
    """输出 `phase` + `phase_confidence` + `next_question` + `missing_critical`。

    `phase_confidence` 只有两种情况：
    - 用户/前端显式传了 `profile.phase` → `confirmed`
    - 其余一律 `inferred`（并且保留推断依据，便于答辩解释）
    """
    profile = profile or {}
    facts = infer_known_facts(profile)
    reasons: list[str] = []

    has_direction = facts.get("has_direction")
    if has_direction is None and (profile.get("target_major") or profile.get("major")):
        has_direction = True
    has_candidates = facts.get("has_candidate_schools")
    if has_candidates is None and (has_shortlist or selected_school_id):
        has_candidates = True
    has_materials = facts.get("has_materials")
    if has_materials is None and material_count > 0:
        has_materials = True

    # ---- 阶段判定（从后往前：已经走到后面的阶段就不该被前一个问题拉回去）----
    if selected_school_id:
        months = _months_to_exam(profile.get("exam_year"), today_iso)
        if months is not None and months <= 3:
            phase = "P4"
            reasons.append(f"已确认院校，且距考试约 {months} 个月，进入冲刺期")
        else:
            phase = "P3"
            reasons.append("已确认目标院校，进入备考执行期")
    elif has_candidates or has_shortlist:
        phase = "P2"
        reasons.append("已有候选院校，处于目标筛选期")
    elif has_direction:
        phase = "P2"
        reasons.append("方向已明确但还没有候选院校，进入目标筛选期")
    elif facts.get("exam_year_known") is not None:
        phase = "P1"
        reasons.append("已知报考年份但方向未定，处于方向探索期")
    else:
        phase = "P0"
        reasons.append("报考年份等关键信息缺失，处于信息缺失期")

    # ---- 下一个该问什么（按降级顺序取第一个真正缺失的）----
    runtime_facts = {
        "exam_year_known": facts.get("exam_year_known"),
        "has_direction": has_direction,
        "has_candidate_schools": has_candidates,
        "has_materials": has_materials,
        "study_status": facts.get("study_status"),
    }
    next_question = None
    next_question_key = None
    missing_critical: list[str] = []
    for key, question, critical in _QUESTIONS:
        if runtime_facts.get(key) is None:
            if next_question is None:
                next_question = question
                next_question_key = key
            if critical and key not in missing_critical:
                missing_critical.append(key)
    if next_question is None:
        next_question = "条件都齐了，要不要现在就生成一份院校推荐？"

    missing_profile_fields = [
        key for key in ("exam_year", "major", "preferred_regions", "daily_minutes")
        if not profile.get(key)
    ]

    supplied_phase = profile.get("phase")
    confidence = "confirmed" if supplied_phase else "inferred"
    if supplied_phase and supplied_phase != phase:
        reasons.append(f"用户显式声明阶段为 {supplied_phase}，与推断结果 {phase} 不一致，已按用户声明为准")
        phase = supplied_phase

    return {
        "phase": phase,
        "phase_name": PHASES.get(phase, phase),
        "phase_confidence": confidence,
        "next_question": next_question,
        "next_question_key": next_question_key,
        "missing_critical": missing_critical,
        "known_facts": runtime_facts,
        "missing_profile_fields": missing_profile_fields,
        "reasons": reasons,
    }


def _months_to_exam(exam_year, today_iso: str | None) -> int | None:
    """粗略估算距考试月数（考研在 12 月）。无法判断时返回 None。"""
    if not exam_year:
        return None
    try:
        year = int(exam_year)
    except (TypeError, ValueError):
        return None
    today = today_iso or _today()
    try:
        cur_year, cur_month = int(today[:4]), int(today[5:7])
    except (TypeError, ValueError):
        return None
    return max(0, (year - cur_year) * 12 + (12 - cur_month))


def _today() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()
