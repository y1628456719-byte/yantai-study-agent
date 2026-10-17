"""端到端冒烟：13 个端点的真实调用链（输出写 UTF-8 文件，避免控制台乱码）。

用法：python scripts/smoke_e2e.py [输出文件]      # 默认写 data/smoke_report.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MOCK_LLM", "1")  # 冒烟默认离线，避免误用真实密钥

from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "smoke_report.json"

SAMPLE_TEXT = """模拟电子技术基础 第三章 放大电路

三极管放大电路的核心是设置合适的静态工作点。静态工作点由直流通路决定，
通常要求 I_CQ 落在负载线中部，否则会出现截止失真或饱和失真。

共射放大电路的电压增益 A_v = -β·R_L'/r_be，输入电阻约等于 r_be，
输出电阻约等于 R_C。旁路电容 C_E 的作用是提高交流增益。

这里我经常把截止失真和饱和失真的波形方向记反：截止失真是顶部削波。
"""


def main_flow() -> dict:
    client = TestClient(main.app)
    report: dict = {"steps": []}

    def step(name: str, resp):
        body = resp.json()
        report["steps"].append({"step": name, "status": resp.status_code, "ok": body.get("ok"),
                                "request_id": body.get("request_id"),
                                "error": body.get("error"),
                                "data": body.get("data")})
        return body

    # 1) 建档
    body = step("1 POST /sessions", client.post("/api/v1/sessions", json={
        "profile": {
            "exam_year": 2027, "major": "信息与通信工程",
            "background": "本科通信工程，在职备考", "preferred_regions": ["长春", "哈尔滨"],
            "daily_minutes": 60, "preferences": "希望专业课与本科方向一致",
            "undergrad_major": "通信工程", "target_major": "信息与通信工程",
            "is_cross_major": False, "english_level": "CET-6",
        }
    }))
    sid = body["data"]["session_id"]
    report["session_id"] = sid
    report["phase_1"] = body["data"]["onboarding"]

    # 2) 第一次推荐（能力画像为空）
    first = step("2 POST /recommendations (第一次)", client.post(f"/api/v1/sessions/{sid}/recommendations"))
    recs_first = first["data"]["recommendations"]
    report["first_ranking"] = [(r["school_id"], r["metrics"]["tier"], r["metrics"]["score_line_total"]) for r in recs_first]
    report["ability_flags_first"] = first["data"].get("ability_flags")
    report["excluded"] = first["data"]["excluded"]
    report["dimension_hits"] = first["data"]["dimension_hits"][:6]

    # 3) 录资料（N11 分段 + N12/N13）
    material = step("3 POST /materials", client.post(f"/api/v1/sessions/{sid}/materials", json={
        "title": "模电笔记·放大电路", "text": SAMPLE_TEXT,
        "source_url": None, "is_demo": True, "subject": "ANALOG_CIRCUIT",
    }))
    mid = material["data"]["material_id"]
    report["material_preview"] = material["data"].get("preview", "")[:120]

    # 4) 无资料诊断路径（N34）
    step("4 POST /lab/start (material_id=null, 量表)", client.post(
        f"/api/v1/sessions/{sid}/lab/start",
        json={"material_id": None, "topic": "MATH_FOUNDATION", "mode": "self_assessment"}))
    scale_q = _last(report)["data"]["question"]
    step("5 POST /lab/answer (量表首题)", client.post(
        f"/api/v1/sessions/{sid}/lab/answer",
        json={"question_id": scale_q["question_id"], "answer": "B"}))

    # 5) 有资料诊断路径（N34 path A）
    step("6 POST /lab/start (资料驱动)", client.post(
        f"/api/v1/sessions/{sid}/lab/start", json={"material_id": mid, "topic": "ANALOG_CIRCUIT"}))

    # 6) 连续作答直到 complete
    for i in range(1, 7):
        data = _last(report)["data"] or {}
        if data.get("next_action") == "complete":
            break
        question = data.get("question") or {}
        options = question.get("options") or []
        first = options[0] if options else None
        answer = (first or {}).get("key") if isinstance(first, dict) else (first or "B")
        step(f"7.{i} POST /lab/answer", client.post(
            f"/api/v1/sessions/{sid}/lab/answer",
            json={"question_id": question.get("question_id"), "answer": answer}))
    report["final_lab_state"] = _last(report)["data"]

    # 7) 第二次推荐（能力画像已生成 → ability_flags 必须出现）
    second = step("8 POST /recommendations (第二次, P2 二次推荐)", client.post(f"/api/v1/sessions/{sid}/recommendations"))
    report["ability_flags_second"] = second["data"].get("ability_flags")
    report["second_ranking"] = [(r["school_id"], r["metrics"]["tier"]) for r in second["data"]["recommendations"]]
    report["tier_change"] = _tier_change(report["first_ranking"], report["second_ranking"])

    # 8) 确认目标 + 备选清单
    chosen = recs_first[0]["school_id"]
    step("9 PUT /target", client.put(f"/api/v1/sessions/{sid}/target",
                                     json={"school_id": chosen, "user_note": "专业课与本科一致，先冲"}))
    step("10 PUT /shortlist", client.put(f"/api/v1/sessions/{sid}/shortlist",
                                         json={"school_ids": [r["school_id"] for r in recs_first[:2]]}))

    # 9) 计划：60 → 30 分钟（不得清空记录）
    revise = step("11 POST /plan/revise (60→30)", client.post(f"/api/v1/sessions/{sid}/plan/revise",
                                                              json={"daily_minutes": 30}))
    report["plan_changes"] = revise["data"]["changes"]
    report["plan_kept"] = revise["data"]["plan"]

    # 10) 快照：确认什么都没被清空
    snap = step("12 GET /sessions/{id}", client.get(f"/api/v1/sessions/{sid}"))
    d = snap["data"]
    report["snapshot_invariants"] = {
        "selected_school_id": d["selected_school_id"],
        "shortlist": d["shortlist"],
        "materials": len(d["materials"]),
        "learning_state_has_lab": bool((d.get("learning_state") or {}).get("lab")),
        "decisions": len(d["decisions"]),
        "revision": d["revision"],
        "phase": (d["profile"] or {}).get("phase"),
    }

    # 11) 错误路径
    step("13 GET 不存在的会话", client.get("/api/v1/sessions/nope"))
    step("14 未选目标的计划", client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 45}))
    step("15 幂等删除资料 A", client.delete(f"/api/v1/sessions/{sid}/materials/{mid}"))
    step("16 幂等删除资料 B", client.delete(f"/api/v1/sessions/{sid}/materials/{mid}"))
    step("17 错误 question_id", client.post(f"/api/v1/sessions/{sid}/lab/answer",
                                           json={"question_id": "bogus", "answer": "A"}))
    return report


def _last(report: dict) -> dict:
    return report["steps"][-1]


def _tier_change(first, second) -> dict:
    a = {sid: tier for sid, tier, _ in first}
    b = {sid: tier for sid, tier in second}
    return {sid: {"before": a.get(sid), "after": b.get(sid)} for sid in set(a) | set(b)}


if __name__ == "__main__":
    report = main_flow()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written {OUT} ({OUT.stat().st_size} bytes)")
    for s in report["steps"]:
        print(f"{s['status']} ok={s['ok']} {s['step']} {('ERR=' + str(s['error'])) if s['error'] else ''}")
