"""学习计划（SPEC §7.10 / N21 / N22）。

契约硬性要求：**修改每日时间不得清空已选院校、资料与作答记录**。
因此这里是无状态纯函数：只根据当前输入算出一份新计划，会话状态由 `session_store` 负责保留。
"""
from __future__ import annotations

from ..core import subjects as subject_table
from . import school_data

#: 每项任务的最小时长（分钟）
MIN_TASK_MINUTES = 10
#: 低于这个总时长时开始砍任务（而不是等比缩水）
DROP_THRESHOLD_MINUTES = 90


def _weak_topics(learning_state: dict | None) -> list[dict]:
    if not learning_state:
        return []
    return [item for item in (learning_state.get("ability_profile") or [])
            if item.get("topic") and item.get("status") == "weak"]


#: 时间紧张时科目的优先级权重。依据是初试分值占比与提分效率：
#: 数学/专业课 150 分且拉分明显 → 3；英语 100 分 → 2；政治性价比最低 → 1。
#: 早期版本所有科目同权重，于是"每天 30 分钟"的计划会砍掉数学、留下政治。
_SUBJECT_WEIGHT = {
    "MATH1": 3, "MATH2": 3, "MATH3": 3,
    "MATH_CALC": 3, "MATH_LINALG": 3, "MATH_PROB": 3,
    "ENG1": 2, "ENG2": 2,
    "POLITICS": 1,
}


def _subject_weight(code: str) -> int:
    if code in _SUBJECT_WEIGHT:
        return _SUBJECT_WEIGHT[code]
    category = (subject_table.by_code().get(code) or {}).get("category")
    return {"major_base": 3, "math": 3, "general": 2}.get(category, 2)


def _exam_subjects_of(school_id: str | None, major_hint: str | None) -> tuple[list[str], list[str], dict | None]:
    """返回 (考试科目中文原文, 标准科目代码, 专业信息)。"""
    if not school_id:
        return [], [], None
    built = school_data.build_metrics(school_id, major_hint=major_hint)
    metrics = built.get("metrics") or {}
    raw = metrics.get("exam_subjects") or []
    codes = metrics.get("exam_subject_codes") or []
    return list(raw), list(codes), built.get("program")


def build_plan(session: dict, daily_minutes: int) -> list[dict]:
    """生成计划条目（`kept=False` 表示这次被砍掉，仍会出现在 `changes` 里）。"""
    profile = session.get("profile") or {}
    learning_state = session.get("learning_state") or {}
    major_hint = profile.get("target_major") or profile.get("major")

    raw_subjects, codes, _program = _exam_subjects_of(session.get("selected_school_id"), major_hint)
    weak = _weak_topics(learning_state)

    candidates: list[dict] = []

    # 1) 诊断出的薄弱知识点优先（创新点 4：计划指向具体诊断结论）
    for item in weak:
        code = item.get("topic")
        count = int(item.get("evidence_count") or 0)
        basis = "实测" if item.get("basis") == "measured" else "自评"
        candidates.append({
            "task": f"薄弱专项：{subject_table.display_name(code)}",
            "weight": 4,
            "reason": f"盲点实验得出 {count} 条{basis}证据指向该知识点薄弱",
        })

    # 2) 目标院校的初试科目（每个科目一块）
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        blocked = any(w.get("topic") == code or w.get("topic") in subject_table.composed_of(code)
                      for w in weak)
        candidates.append({
            "task": f"初试科目：{subject_table.display_name(code)}",
            "weight": 3 if blocked else _subject_weight(code),
            "reason": f"这是目标院校的初试科目（标准科目：{code}）",
        })
    # 标准科目表没收录的科目也要练，但不参与前置推导
    for raw in raw_subjects:
        if not subject_table.match(raw)[0]:
            candidates.append({
                "task": f"初试科目：{raw}",
                "weight": 2,
                "reason": "目标院校初试科目；该科目尚未进入标准科目表，无法做前置知识推导",
            })

    # 3) 资料复盘（有资料才有）
    materials = session.get("materials") or []
    if materials:
        candidates.append({
            "task": "回看已保存资料",
            "weight": 1,
            "reason": f"会话内已有 {len(materials)} 份资料，可按需要回看原文",
        })

    # 4) 兜底：还没定目标院校时给出可执行的基础盘
    if not candidates:
        candidates.append({
            "task": "公共课基础盘：数学 + 英语各一组题",
            "weight": 2,
            "reason": "尚未确认目标院校，先用公共课基本盘保持手感（确认院校后计划会自动重算）",
        })
    candidates.append({
        "task": "错题回看",
        "weight": 1,
        "reason": "把当天错题按『概念混淆/计算失误/审题偏差』归类后重做",
    })

    # 先按优先级确定预算能容纳的任务，再分配；最低时长不能突破总预算。
    candidates.sort(key=lambda item: -item["weight"])
    cap = (2 if daily_minutes < 60 else 3) if daily_minutes < DROP_THRESHOLD_MINUTES else len(candidates)
    keep_count = min(len(candidates), cap, max(0, daily_minutes // MIN_TASK_MINUTES))
    plan = [{**item, "minutes": 0, "kept": index < keep_count}
            for index, item in enumerate(candidates)]
    for item in plan[keep_count:]:
        note = (f"每日预算{daily_minutes}分钟不足最小任务时长{MIN_TASK_MINUTES}分钟，本次全部暂缓。"
                if daily_minutes < MIN_TASK_MINUTES else "每日时间有限，本次按优先级暂缓。")
        item["reason"] += "；" + note
    if not keep_count:
        return plan

    kept = plan[:keep_count]
    for item in kept:
        item["minutes"] = MIN_TASK_MINUTES
    units = (daily_minutes - MIN_TASK_MINUTES * keep_count) // 5
    total_weight = sum(item["weight"] for item in kept)
    allocated = [units * item["weight"] // total_weight for item in kept]
    remaining = units - sum(allocated)
    # 最大余数法分配剩余5分钟块；同余数时保留原有优先级。
    order = sorted(range(keep_count), key=lambda index: -(units * kept[index]["weight"] % total_weight))
    for index in order[:remaining]:
        allocated[index] += 1
    for item, extra in zip(kept, allocated):
        item["minutes"] += extra * 5
    return plan


def stored_rule_plan(session: dict, daily_minutes: int) -> dict:
    """已有整体计划在关键条件变化后原子重排，不请求模型文案。"""
    rows = build_plan(session, daily_minutes)
    changes = [{"action": "kept" if item["kept"] else "dropped", "task": item["task"],
                "reason": item["reason"]} for item in rows]
    profile = session.get("profile") or {}
    return {
        "daily_minutes": daily_minutes,
        "items": [{k: item[k] for k in ("task", "minutes", "reason")} for item in rows if item["kept"]],
        "changes": changes,
        "dropped": [item for item in changes if item["action"] == "dropped"],
        "generated_by": "rules",
        "scope": {"selected_school_id": session.get("selected_school_id"), "major": profile.get("major"),
                  "target_major": profile.get("target_major")},
        "note": "学习时间、目标院校或报考方向变化后按当前条件重新安排，未调用模型。",
    }


def revise(session: dict, daily_minutes: int) -> dict:
    """SPEC §7.10 的纯计算部分（不含模型文案）。"""
    previous = (session.get("plan") or {}).get("daily_minutes") or \
               (session.get("profile") or {}).get("daily_minutes") or 0
    plan = build_plan(session, daily_minutes)
    return {
        "previous_daily_minutes": previous,
        "daily_minutes": daily_minutes,
        "plan": plan,
        "changes": [],   # 由 modelsvc.build_plan_changes 补全（N21）
    }
