"""队友 C 的实验室模块自检：字段级校验 + 两条路径的状态机检查。

用法（仓库根目录下）：
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\lab_teammate_check.py
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\lab_teammate_check.py lab.example_impl
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\lab_teammate_check.py --module 你的模块名

默认模块 `lab.teammate_impl`（可用环境变量 LAB_TEAMMATE_MODULE 覆盖）。
控制台只输出 ASCII 的 [OK]/[FAIL]；完整返回树写进 `data/lab_check_report.json`（UTF-8）。
退出码 0 = 全部通过。
"""
from __future__ import annotations

import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.core import subjects as subject_table  # noqa: E402
from backend.lab_adapter.base import LabError, STAGES, normalize_result  # noqa: E402

REPORT_PATH = os.path.join(ROOT, "data", "lab_check_report.json")
results: list[tuple[str, bool, str]] = []
raw_log: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    flag = "OK  " if ok else "FAIL"
    print(f"[{flag}] {name}" + (f"  -- {detail}" if (detail and not ok) else ""))


# --------------------------------------------------------------------- context
def make_context(*, with_material: bool) -> dict:
    material = None
    if with_material:
        material = {
            "material_id": "m_check_0001",
            "title": "模电笔记（自检用）",
            "subject": "ANALOG_CIRCUIT",
            "school_id": "jlu",
            "text": "共射放大电路的静态工作点由基极偏置电阻决定。Q 点偏低时输出正半周进入截止区，产生截止失真。",
            "paragraphs": [
                {"seq": 1, "locator": "第 1 段",
                 "text": "共射放大电路的静态工作点由基极偏置电阻决定。"},
                {"seq": 2, "locator": "第 2 段",
                 "text": "Q 点偏低时输出正半周进入截止区，产生截止失真。"},
            ],
        }
    return {
        "session_id": "s_labcheck",
        "profile": {"exam_year": 2027, "major": "信息与通信工程", "study_status": "边工作边考"},
        "selected_school_id": "jlu",
        "selected_school": {"school_id": "jlu", "school": "吉林大学"},
        "material": material,
        "learning_state": {},
        "subject_table": subject_table.as_meta(),
        "diagnosis_mode": "material" if with_material else "self_assessment",
        "topic": "ANALOG_CIRCUIT",
    }


# ------------------------------------------------------------------ validation
def validate(tag: str, context: dict, result) -> dict | None:
    """对一次调用做结构校验，返回归一化后的 {lab_state, learning_state}。"""
    raw_log.append({"call": tag, "context_mode": context["diagnosis_mode"], "result": result})
    if not isinstance(result, dict) or "lab_state" not in result:
        check(f"{tag}: 返回含 lab_state 的 dict", False, f"实际类型 {type(result).__name__}")
        return None
    try:
        fixed = normalize_result(result, is_demo=False)
    except LabError as exc:
        check(f"{tag}: 通过后端结构校验", False, str(exc))
        return None
    check(f"{tag}: 通过后端结构校验", True)

    lab = fixed["lab_state"]
    check(f"{tag}: stage 合法", lab.get("stage") in STAGES, f"stage={lab.get('stage')!r}")
    check(f"{tag}: is_demo 为 false", lab.get("is_demo") is False, f"is_demo={lab.get('is_demo')!r}")
    check(f"{tag}: next_action 合法", lab.get("next_action") in ("answer", "complete"),
          f"next_action={lab.get('next_action')!r}")
    check(f"{tag}: learning_state 是 object/null",
          fixed.get("learning_state") is None or isinstance(fixed.get("learning_state"), dict))

    q = lab.get("question")
    if lab.get("next_action") == "answer":
        ok = isinstance(q, dict) and q.get("question_id") and isinstance(q.get("options"), list) \
            and len(q["options"]) >= 2 and all(
                isinstance(o, dict) and o.get("key") and o.get("text") for o in q["options"])
        check(f"{tag}: question 可用（question_id + 至少 2 个 key/text 选项）", bool(ok),
              json.dumps(q, ensure_ascii=False)[:200])
    else:
        check(f"{tag}: complete 时可不给题目", q is None, f"question={str(q)[:80]}")

    hypo_ok, hypo_detail = True, ""
    for h in lab.get("hypotheses") or []:
        if not h.get("label"):
            hypo_ok, hypo_detail = False, "缺 label"
        if h.get("status") not in ("tentative", "supported", "revised"):
            hypo_ok, hypo_detail = False, f"status={h.get('status')!r}"
        code = h.get("subject_code")
        if code is not None and code not in subject_table.by_code():
            hypo_ok, hypo_detail = False, f"subject_code {code!r} 不在标准科目表（会被置 null）"
    check(f"{tag}: hypotheses 合法且 subject_code 在标准表内", hypo_ok, hypo_detail)

    ev_ok, ev_detail = True, ""
    for e in lab.get("evidence") or []:
        if e.get("kind") == "material":
            if not (e.get("quote") and e.get("locator")):
                ev_ok, ev_detail = False, "material 证据缺 quote/locator（N14）"
        elif e.get("kind") == "answer":
            if not e.get("question_id"):
                ev_ok, ev_detail = False, "answer 证据缺 question_id"
        else:
            ev_ok, ev_detail = False, f"kind={e.get('kind')!r}"
    check(f"{tag}: evidence 合法", ev_ok, ev_detail)
    return fixed


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    module_name = (args[0] if args else None) or os.environ.get("LAB_TEAMMATE_MODULE") \
        or "lab.teammate_impl"

    print(f"== lab teammate check: module={module_name} ==")
    try:
        import importlib
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        check(f"导入模块 {module_name}", False, f"{type(exc).__name__}: {exc}")
        print("\n提示：模块还没写？可以先跑示例："
              "python scripts/lab_teammate_check.py lab.example_impl")
        write_report(module_name)
        return 1

    check(f"导入模块 {module_name}", True)
    check("提供 start_lab(context)", callable(getattr(module, "start_lab", None)))
    check("提供 submit_answer(context, question_id, answer)",
          callable(getattr(module, "submit_answer", None)))
    if not (callable(getattr(module, "start_lab", None))
            and callable(getattr(module, "submit_answer", None))):
        write_report(module_name)
        return 1

    # ---------------- 路径 A：有资料 ----------------
    ctx_a = make_context(with_material=True)
    try:
        fixed = validate("A.start", ctx_a, module.start_lab(ctx_a))
    except Exception as exc:  # noqa: BLE001
        check("A.start: 不抛异常", False, f"{type(exc).__name__}: {exc}")
        fixed = None
    if fixed:
        ctx_a["learning_state"] = fixed.get("learning_state") or {}
        qid = (fixed["lab_state"]["question"] or {}).get("question_id")
        keys = [o.get("key") for o in ((fixed["lab_state"]["question"] or {}).get("options") or [])]
        # 逐个选项试，找一个「错误分支」（feedback 非空或假设被 revised）；
        # 每次都用同一份「作答前」状态，避免上一次尝试污染下一轮。
        wrong, call_ok = None, True
        for k in (keys or ["Z"]):
            probe_ctx = dict(ctx_a)
            try:
                res = validate(f"A.answer({k})", probe_ctx, module.submit_answer(probe_ctx, qid, k))
            except Exception as exc:  # noqa: BLE001
                check(f"A.answer({k}): 不抛异常", False, f"{type(exc).__name__}: {exc}")
                call_ok, res = False, None
            if res and (res["lab_state"].get("feedback")
                        or any(h.get("status") == "revised"
                               for h in res["lab_state"].get("hypotheses") or [])):
                wrong = res
                break
        if wrong is None:
            check("A.answer: 错误分支可复现（所有选项都被判为正确）", call_ok,
                  "请确认题干里确实存在错误选项，否则无法验证答错路径")
        else:
            lab2 = wrong["lab_state"]
            check("A.answer: 答错后有 feedback（错在哪）", bool(lab2.get("feedback")),
                  f"feedback={lab2.get('feedback')!r}")
            check("A.answer: 答错后有 explanation（正确思路）", bool(lab2.get("explanation")),
                  f"explanation={lab2.get('explanation')!r}")
            check("A.answer: 假设被修正或明确标记",
                  any(h.get("status") in ("revised", "supported")
                      for h in lab2.get("hypotheses") or []),
                  json.dumps(lab2.get("hypotheses"), ensure_ascii=False)[:200])
            check("A.answer: 作答证据 kind=answer",
                  any(e.get("kind") == "answer" for e in lab2.get("evidence") or []))

    # ---------------- 路径 B：无资料（N34） ----------------
    ctx_b = make_context(with_material=False)
    try:
        fixedb = validate("B.start", ctx_b, module.start_lab(ctx_b))
    except Exception as exc:  # noqa: BLE001
        check("B.start: 不抛异常", False, f"{type(exc).__name__}: {exc}")
        fixedb = None
    if fixedb:
        qid = (fixedb["lab_state"]["question"] or {}).get("question_id") or ""
        check("B.start: 自评量表题（question_id 以 self_ 开头）", str(qid).startswith("self_"),
              f"question_id={qid!r}")
        ctx_b["learning_state"] = fixedb.get("learning_state") or {}
        try:
            fixedb2 = validate("B.answer(自评)", ctx_b, module.submit_answer(ctx_b, qid, "2"))
        except Exception as exc:  # noqa: BLE001
            check("B.answer: 不抛异常", False, f"{type(exc).__name__}: {exc}")
            fixedb2 = None
        if fixedb2:
            ev = fixedb2["lab_state"].get("evidence") or []
            check("B.answer: 自评证据带 basis=self_reported",
                  any(e.get("basis") == "self_reported" for e in ev) or any(
                      "自评" in str(e.get("quote", "")) for e in ev),
                  json.dumps(ev, ensure_ascii=False)[:200])
            q2 = fixedb2["lab_state"].get("question") or {}
            check("B.answer: 自评之后跟一道实测题（非 self_）",
                  bool(q2) and not str(q2.get("question_id", "")).startswith("self_"),
                  json.dumps(q2, ensure_ascii=False)[:200])

    passed = sum(1 for _n, ok, _d in results if ok)
    total = len(results)
    print(f"\n== {passed}/{total} checks passed ==")
    write_report(module_name)
    return 0 if passed == total else 1


def write_report(module_name: str) -> None:
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump({"module": module_name,
                   "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
                   "raw_calls": raw_log}, fh, ensure_ascii=False, indent=2)
    print(f"完整报告（UTF-8，中文可读）：{REPORT_PATH}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
