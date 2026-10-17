from .errors import LabError


def build_learning_plan(learning_state, daily_minutes):
    """Pure bounded plan; the host owns profile changes and persistence."""
    if type(daily_minutes) is not int or not 1 <= daily_minutes <= 1440:
        raise LabError("INVALID_INPUT", "每日时间须为1至1440的整数分钟。")
    if not isinstance(learning_state, dict):
        raise LabError("INVALID_INPUT", "生成任务至少需要一次有效作答。")
    answers = learning_state.get("answers")
    if (not isinstance(answers, list) or not 1 <= len(answers) <= 3
            or any(not isinstance(a, dict) or not isinstance(a.get("answer_id"), str)
                   or not a["answer_id"] for a in answers)):
        raise LabError("INVALID_INPUT", "学习状态缺少有效作答ID。")
    focus = learning_state.get("focus", "uncertain")
    ids = [a["answer_id"] for a in learning_state["answers"]]
    first = ("圈出连接词并写出条件方向" if focus == "reading"
             else "对照资料画出充分与必要条件的箭头")
    source = [(first, 10), ("用反例检查倒推是否成立", 20), ("换情境练习并复盘错误", 30)]
    tasks, deferred, remaining = [], [], daily_minutes
    for task, desired in source:
        if remaining == 0:
            deferred.append(task)
            continue
        minutes = min(desired, remaining)
        tasks.append({"task": task, "minutes": minutes,
                      "shortened": minutes < desired,
                      "reason": "依据本轮作答安排；单次表现不能证明长期掌握。",
                      "evidence_ids": ids})
        remaining -= minutes
    return {"daily_minutes": daily_minutes, "tasks": tasks, "deferred": deferred,
            "total_minutes": sum(t["minutes"] for t in tasks)}
