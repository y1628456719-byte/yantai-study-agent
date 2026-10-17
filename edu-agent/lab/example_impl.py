"""队友 C 的接口骨架（**示例，请替换为真实实现**）。

用法：把本文件复制成 `lab/teammate_impl.py` 后往里填。
自测：`python scripts/lab_teammate_check.py`（默认就调 `lab.teammate_impl`）。

契约（SPEC §10.1 / §7.11）：
- 两个同步函数：`start_lab(context) -> dict`、`submit_answer(context, question_id, answer) -> dict`
- 返回 `{"lab_state": {...}, "learning_state": {...}}`
- 任何失败直接 raise（后端会转成 503 LAB_UNAVAILABLE，中文文案直接展示给用户）
- 禁止静默返回 mock；禁止把自评结论当成实测结论

本示例只演示"结构 + 两条路径的状态机"，题目是写死的，真正的实现在你那边。
"""
from __future__ import annotations

# 一个最小的假题库（示例用；真实实现应自己组织题目/规则/模型调用）
_QUESTIONS = {
    "ANALOG_CIRCUIT": {
        "question_id": "ac_qpoint_1",
        "subject_code": "ANALOG_CIRCUIT",
        "stem": "共射放大电路中，静态工作点 Q 偏低会导致什么？",
        "options": [
            {"key": "A", "text": "截止失真"},
            {"key": "B", "text": "饱和失真"},
            {"key": "C", "text": "不影响输出波形"},
        ],
        "answer": "A",
        "explanation": "Q 点偏低会让输出正半周进入截止区，出现截止失真；偏高才进饱和。",
        "feedback_wrong": "把 Q 点偏低与饱和失真弄反了，属于「概念混淆」。",
    }
}
_DEFAULT_TOPIC = "ANALOG_CIRCUIT"


def _topic(context: dict) -> str:
    return context.get("topic") or _DEFAULT_TOPIC


def start_lab(context: dict) -> dict:
    topic = _topic(context)
    material = context.get("material") or {}
    diagnosis_mode = context.get("diagnosis_mode") or ("material" if material else "self_assessment")

    if diagnosis_mode == "self_assessment" or not material:
        # 路径 B（N34）：先给自评量表题，选项是 1~5 分
        question = {
            "question_id": f"self_{topic}",
            "subject_code": topic,
            "stem": "用 1（完全没学过）到 5（能独立做题）给自己这项打个分：",
            "options": [{"key": str(i), "text": f"{i} 分"} for i in range(1, 6)],
        }
        evidence = []            # 自评的证据在作答之后才产生
        hypotheses = [{
            "label": f"{topic} 掌握情况（待验证）",
            "subject_code": topic,
            "status": "tentative",
            "reason": "还没有任何作答证据，先按自评了解你的起点",
        }]
    else:
        # 路径 A：资料驱动，quote/locator 必须能在资料里定位（N14）
        paragraphs = material.get("paragraphs") or []
        quote = (paragraphs[0]["text"][:60] if paragraphs else "")
        locator = (paragraphs[0].get("locator") if paragraphs else None)
        question = dict(_QUESTIONS[topic])
        hypotheses = [{
            "label": "模拟电子技术掌握情况",
            "subject_code": topic,
            "status": "tentative",
            "reason": "需要一道题确认你是否真的掌握静态工作点",
        }]
        evidence = ([{"kind": "material", "material_id": material.get("material_id"),
                      "quote": quote, "locator": locator, "basis": "measured"}]
                    if quote else [])

    lab_state = {
        "stage": "diagnosis",
        "question": question,
        "feedback": None,
        "hypotheses": hypotheses,
        "evidence": evidence,
        "explanation": None,
        "next_action": "answer",
        "is_demo": False,
    }
    learning_state = {
        "lab": {"topic": topic, "diagnosis_mode": diagnosis_mode,
                "stage": "diagnosis", "current_question_id": question["question_id"],
                "round": int((context.get("learning_state") or {}).get("lab", {}).get("round", 0)),
                "asked": [question["question_id"]]},
    }
    return {"lab_state": lab_state, "learning_state": learning_state}


def submit_answer(context: dict, question_id: str, answer: str) -> dict:
    topic = _topic(context)
    material = context.get("material") or {}
    prev = context.get("learning_state") or {}
    round_no = int((prev.get("lab") or {}).get("round", 0)) + 1

    if question_id.startswith("self_"):
        # 路径 B：自评 → 只写 self_reported，且不得作为排除院校的依据
        score = int(answer) if str(answer).strip().isdigit() else None
        weak = score is None or score <= 2
        item = {
            "label": f"{topic} 自评", "subject_code": topic,
            "status": "tentative" if weak else "supported",
            "reason": f"自评 {score} 分（自评只能作为线索，不作为结论）",
        }
        lab_state = {
            "stage": "diagnosis",
            "question": dict(_QUESTIONS[topic]),       # 自评之后来一道实测题
            "feedback": None,
            "hypotheses": [item],
            "evidence": [{"kind": "answer", "question_id": question_id, "quote": f"自评 {score} 分",
                          "basis": "self_reported"}],
            "explanation": None,
            "next_action": "answer",
            "is_demo": False,
        }
        learning_state = {
            "lab": {"topic": topic, "stage": "diagnosis",
                    "current_question_id": _QUESTIONS[topic]["question_id"], "round": round_no},
            "self_assessment": [{"topic": topic, "score": score, "basis": "self_reported"}],
            "ability_profile": [{"topic": topic, "status": "tentative",
                                 "evidence_count": 1, "basis": "self_reported"}],
        }
        return {"lab_state": lab_state, "learning_state": learning_state}

    # 路径 A（以及路径 B 的实测题）：判对错
    q = _QUESTIONS[topic]
    right = str(answer).strip().upper() == q["answer"]
    evidence = [{"kind": "answer", "question_id": question_id, "quote": f"作答 {answer}",
                 "basis": "measured"}]
    if right:
        lab_state = {
            "stage": "summary", "question": None,
            "feedback": None,
            "hypotheses": [{"label": "模拟电子技术掌握情况", "subject_code": topic,
                            "status": "supported", "reason": "实测答对，假设成立"}],
            "evidence": evidence,
            "explanation": q["explanation"],
            "next_action": "complete", "is_demo": False,
        }
    else:
        lab_state = {
            "stage": "verification",
            "question": {**q, "question_id": q["question_id"] + "_v2",
                         "stem": "（追问·同一知识点）" + q["stem"]},
            "feedback": q["feedback_wrong"],                 # N17 第一层：错在哪（含 N18 归类）
            "hypotheses": [{"label": "模拟电子技术掌握情况", "subject_code": topic,
                            "status": "revised", "reason": "实测答错，原假设被修正"}],
            "evidence": evidence,
            "explanation": q["explanation"],                # N17 第二层：正确思路
            "next_action": "answer", "is_demo": False,
        }

    learning_state = {
        "lab": {"topic": topic, "stage": lab_state["stage"],
                "current_question_id": (lab_state["question"] or {}).get("question_id"),
                "round": round_no},
        "ability_profile": [{"topic": topic, "status": "solid" if right else "weak",
                             "evidence_count": 1, "basis": "measured"}],
    }
    return {"lab_state": lab_state, "learning_state": learning_state}
