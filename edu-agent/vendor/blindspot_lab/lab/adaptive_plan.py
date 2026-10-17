"""Evidence based scheduling for v2; total fits the current estimated budget."""
from .adaptive import STRATEGIES
from .errors import LabError


def build_adaptive_plan(state, daily_minutes, view):
    if type(daily_minutes) is not int or not 1 <= daily_minutes <= 1440:
        raise LabError("INVALID_INPUT", "时间须为1至1440的整数。")
    available = max(0, daily_minutes - state["spent_minutes"])
    ids = [a["answer_id"] for a in state["core"]["answers"] + state["followups"]]
    ids += [f["id"] for f in state["feedback_records"]]
    reason = view["adaptation"]["reason"]
    n = len(state["core"]["answers"])
    if state["stopped"]:
        source = []
    elif n < 2:
        source = ([] if n else [("完成首题，记录当前理解",1,"observe")])
        source += [("完成区分题，核对暂时判断",1,"observe"),
                   ("用未见题核对本次理解",1,"verify")]
    elif view["cycle"]["stop_reason"] in ("round_limit", "bank_exhausted"):
        source = [("保存本次结果，后续使用新样本复查", 2, "review")]
    else:
        status = (view.get("summary") or {}).get("result_status")
        source = [("用未见题核对本次理解", 1 if n == 2 else 2, "verify")]
        if n >= 3 and not state["pending_question_id"]:
            source += [("尝试条件链迁移" if status == "tentative_transfer" else
                        STRATEGIES[state["strategy"]][0] + "：回看已答题", 1, "learn")]
        source += [("复盘变化与仍不确定的地方", 3, "review")]
    tasks, deferred, remaining = [], [], available
    minimum = 3 if n >= 3 and not state["pending_question_id"] else 2 if state["pending_question_id"] else 1
    if view["cycle"]["stop_reason"] == "time_budget" and available < minimum:
        return {"daily_minutes":daily_minutes,"estimated_spent_minutes":state["spent_minutes"],
                "available_minutes":available,"tasks":[],"deferred":[t[0] for t in source],"total_minutes":0,
                "note":"剩余预算不足以完成讲解与验证，暂停后可增加时间；已答记录保留。"}
    for title, desired, kind in source:
        if remaining < (desired if kind == "verify" else 1):
            deferred.append(title)
            continue
        minutes = min(desired, remaining)
        tasks.append({"task": title, "minutes":minutes, "kind":kind,
                      "reason":reason, "evidence_ids":ids})
        remaining -= minutes
    if n >= 3 and not state["pending_question_id"]:
        tasks.sort(key=lambda t: {"learn":0,"verify":1,"review":2}.get(t["kind"],0))
    return {"daily_minutes":daily_minutes,"estimated_spent_minutes":state["spent_minutes"],
            "available_minutes":available,"tasks":tasks,"deferred":deferred,
            "total_minutes":sum(t["minutes"] for t in tasks),
            "note":"时间为演示估算；首轮验证1分钟，追加轮讲解1分钟与验证2分钟。先留验证预算，不将未验证记为完成。"}
