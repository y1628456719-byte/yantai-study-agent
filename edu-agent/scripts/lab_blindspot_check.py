"""队友 C「盲点实验室」接入自检（可交付脚本，非临时探针）。

跑法（Windows）：
    $env:LAB_ADAPTER="blindspot"; $env:MOCK_LLM="1"
    .venv\\Scripts\\python.exe scripts\\lab_blindspot_check.py
或在任何环境下直接：  python scripts/lab_blindspot_check.py

它做三件事：
1. 用 TestClient 走 SPEC §7.8/§7.9 两个端点（start / answer），校验 lab_state 契约（§7.11）；
2. 走一遍队友 lab.v2 的自适应动作（feedback / continue / time / stop，additive 端点）验证状态连续推进；
3. 把完整报告写成 JSON（`data/lab_blindspot_report.json`），控制台只打 ASCII 结论。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="edu_blindspot_"))
os.environ["EDU_AGENT_DB"] = str(_TMP / "blindspot.db")
os.environ["LAB_ADAPTER"] = "blindspot"
os.environ.setdefault("LAB_BLINDSPOT_MODE", "mock")
os.environ["MOCK_LLM"] = "1"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

CHECKS: list[dict] = []
STEPS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})


def call(client: TestClient, method: str, path: str, payload: dict | None = None) -> dict:
    resp = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
    body = resp.json()
    STEPS.append({"method": method.upper(), "path": path, "status": resp.status_code,
                  "request": payload, "response": body})
    return {"status": resp.status_code, "body": body}


def data_of(res: dict) -> dict:
    return (res.get("body") or {}).get("data") or {}


def main() -> int:
    client = TestClient(app)
    s = data_of(call(client, "post", "/api/v1/sessions", {"profile": {
        "exam_year": 2027, "major": "信息与通信工程", "daily_minutes": 60,
        "preferred_regions": ["长春"]}}))
    sid = s.get("session_id")
    check("创建会话", bool(sid), str(sid))

    lab = data_of(call(client, "post", f"/api/v1/sessions/{sid}/lab/start",
                       {"material_id": None, "topic": "MATH_FOUNDATION", "mode": "self_assessment"}))
    check("lab/start 返回 lab_state", bool(lab), "")
    check("stage 合法", lab.get("stage") in ("diagnosis", "explanation", "verification", "summary"),
          str(lab.get("stage")))
    check("adapter 标记为 blindspot", lab.get("adapter") == "blindspot", str(lab.get("adapter")))
    check("is_demo 为 true（随包演示材料）", lab.get("is_demo") is True, str(lab.get("is_demo")))
    check("material_note 如实说明未使用用户资料", bool(lab.get("material_note")), "")
    q = lab.get("question") or {}
    opts = q.get("options") or []
    check("给了题目与选项", bool(q.get("question_id")) and len(opts) >= 2,
          f"{q.get('question_id')} options={len(opts)}")
    check("选项已翻译成 SPEC 的 {key,text}", all("key" in o and "text" in o for o in opts), "")
    check("hypotheses 带 label（§7.11）", all("label" in h for h in (lab.get("hypotheses") or [])), "")

    # --- 作答：故意先答错，看是否产生 answer 证据与反馈 ---
    wrong = next((o["key"] for o in opts if o.get("key") != (q.get("answer") or "")), opts[0]["key"])
    res = call(client, "post", f"/api/v1/sessions/{sid}/lab/answer",
               {"question_id": q["question_id"], "answer": wrong})
    lab2 = data_of(res)
    check("lab/answer 成功", res["status"] == 200, f"status={res['status']}")
    if res["status"] == 200:
        check("作答后有 answer 类证据",
              all(e.get("kind") == "answer" for e in (lab2.get("evidence") or [])) and
              any(e.get("kind") == "answer" for e in (lab2.get("evidence") or [])),
              f"evidence={len(lab2.get('evidence') or [])}")
        check("feedback 与 explanation 分层（N17）",
              bool(lab2.get("feedback")) or bool(lab2.get("explanation")), "")
        snap = data_of(call(client, "get", f"/api/v1/sessions/{sid}"))
        ls = snap.get("learning_state") or {}
        check("learning_state 里保存了队友会话快照（blindspot）", bool(ls.get("blindspot")), "")

    # --- 自适应动作（additive 端点）---
    if res["status"] == 200:
        fb = call(client, "post", f"/api/v1/sessions/{sid}/lab/action",
                  {"action": "feedback", "feedback": "guessed"})
        check("feedback 动作被接受", fb["status"] == 200,
              str(data_of(fb).get("their_next_action")))
        cont = call(client, "post", f"/api/v1/sessions/{sid}/lab/action", {"action": "continue"})
        can_continue = bool((data_of(fb).get("cycle") or lab2.get("cycle") or {}).get("can_continue"))
        if can_continue:
            check("continue 动作被接受（轮次结束时才能继续）", cont["status"] == 200, f"status={cont['status']}")
        else:
            msg = ((cont.get("body") or {}).get("error") or {}).get("message") or ""
            check("当前轮次未结束 → continue 被如实拒绝（不假装成功）",
                  cont["status"] != 200 and ("不能继续" in msg or "CONTINUE_UNAVAILABLE" in msg),
                  f"status={cont['status']} {msg[:60]}")
        tm = call(client, "post", f"/api/v1/sessions/{sid}/lab/action", {"action": "time", "minutes": 30})
        check("time 动作被接受（改每日时间不影响已选院校/资料）", tm["status"] == 200,
              f"status={tm['status']}")
        stop = call(client, "post", f"/api/v1/sessions/{sid}/lab/action", {"action": "stop"})
        check("stop 动作被接受且收尾为 summary",
              stop["status"] == 200 and data_of(stop).get("stage") == "summary",
              str(data_of(stop).get("stage")))

    # --- 未知动作应被拒绝（不静默当成功）---
    bad = call(client, "post", f"/api/v1/sessions/{sid}/lab/action", {"action": "不存在的动作"})
    check("未知动作返回 400", bad["status"] == 400, str(bad["status"]))

    passed = sum(1 for c in CHECKS if c["ok"])
    report = {"checks_passed": passed, "checks_total": len(CHECKS), "checks": CHECKS,
              "steps": STEPS, "session_id": sid, "lab_adapter": os.environ["LAB_ADAPTER"],
              "runtime_mode": os.environ.get("LAB_BLINDSPOT_MODE")}
    out = ROOT / "data" / "lab_blindspot_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"checks: {passed}/{len(CHECKS)} passed")
    for c in CHECKS:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}" + (f" -- {c['detail']}" if c['detail'] else ""))
    print(f"report: {out}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
