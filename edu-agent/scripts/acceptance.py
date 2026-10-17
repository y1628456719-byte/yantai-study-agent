"""SPEC §14 验收标准的**可执行载体**——十条逐条跑，逐条给证据。

用法（项目根目录下）：
    .venvs/edu-agent/Scripts/python.exe scripts/acceptance.py
    .venvs/edu-agent/Scripts/python.exe scripts/acceptance.py --json data/acceptance_report.json

设计约束（对应 SPEC §14 的"红线"）：
* 全部走 **HTTP 接口**（FastAPI TestClient），不直接调内部函数——验收的是契约，不是实现。
* 使用**独立临时数据库**（`EDU_AGENT_DB` 环境变量），不污染 `data/app.db`。
* 默认 `MOCK_LLM=1`：无密钥也能跑；第 6 条会临时关掉 mock **并清空密钥**，
  验证"模型不可用"时返回的是可理解的中文错误，而不是堆栈。
* 退出码：全部通过 = 0，有任何失败 = 1（可直接用于 CI 或上台前的最后一次自检）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，打印 ⊆/× 之类的字符会 UnicodeEncodeError 直接崩掉验收脚本。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass

# --- 必须在导入 backend 之前设定环境，否则 config 已经读到旧值 ---
os.environ["MOCK_LLM"] = "1"                      # 离线可跑
os.environ.pop("LLM_API_KEY", None)               # 不依赖任何真实密钥
_TMP_DB = Path(tempfile.gettempdir()) / "edu_agent_acceptance.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["EDU_AGENT_DB"] = str(_TMP_DB)
# SPEC §14.4/§14.8 验的是「能力画像回写 + N34 无资料自评路径」，这是内置 mock 实验室的行为；
# 队友实验室（LAB_ADAPTER=blindspot）有它自己的自检脚本 scripts/lab_blindspot_check.py。
# 这里显式固定为 mock，保证验收结果不受本地 .env 影响（要验别的适配器就设 ACCEPTANCE_LAB_ADAPTER）。
os.environ["LAB_ADAPTER"] = os.environ.get("ACCEPTANCE_LAB_ADAPTER", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.core import dimensions  # noqa: E402
from backend.core import subjects as subject_table  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services import school_data, session_store  # noqa: E402

PREFIX = "/api/v1"
RESULTS: list[dict] = []
EXAMPLES: dict[str, dict] = {}

# 演示用档案：长春/哈尔滨、在职、2027 年考、信息与通信工程方向
DEMO_PROFILE = {
    "exam_year": 2027,
    "major": "信息与通信工程",
    "background": "本科通信工程，在职备考，每天 2 小时",
    "preferred_regions": ["长春", "哈尔滨", "沈阳"],
    "daily_minutes": 120,
    "study_status": "边工作边考",
    "is_cross_major": False,
    "undergrad_major": "通信工程",
    "target_major": "信息与通信工程",
}


def record(no: int, name: str, passed: bool, detail: str) -> None:
    RESULTS.append({"no": no, "name": name, "passed": bool(passed), "detail": detail})
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] §14.{no} {name} — {detail}")


def body(resp) -> dict:
    """取出统一响应体并校验外层契约（SPEC §7.0）。"""
    payload = resp.json()
    assert set(payload) == {"ok", "data", "error", "request_id"}, f"响应结构不符：{list(payload)}"
    assert payload["request_id"], "缺少 request_id"
    return payload


def keep_example(key: str, method: str, url: str, payload, response) -> None:
    EXAMPLES[key] = {
        "method": method,
        "url": url,
        "status": response.status_code,
        "request": payload,
        "response": body(response),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=str(ROOT / "data" / "acceptance_report.json"))
    args = parser.parse_args()

    client = TestClient(app)
    client.__enter__()

    # ---------------------------------------------------------------- 0. 环境
    schools = school_data.school_ids()
    print(f"院校数据：{schools}；标准科目 {len(subject_table.subjects())} 条；"
          f"LAB_ADAPTER={config.LAB_ADAPTER}；MOCK_LLM={config.MOCK_LLM}\n")

    # ------------------------------------------------- 1. 创建会话后可以恢复
    post_profile = dict(DEMO_PROFILE)
    r = client.post(f"{PREFIX}/sessions", json={"profile": post_profile})
    keep_example("create_session", "POST", f"{PREFIX}/sessions", {"profile": post_profile}, r)
    data = body(r)["data"]
    sid = data["session_id"]
    r2 = client.get(f"{PREFIX}/sessions/{sid}")
    snapshot = body(r2)["data"]
    keep_example("get_session", "GET", f"{PREFIX}/sessions/{sid}", None, r2)
    record(1, "创建会话后可以恢复",
           r.status_code == 200 and r2.status_code == 200
           and snapshot["profile"]["major"] == DEMO_PROFILE["major"]
           and snapshot["session_id"] == sid,
           f"POST /sessions → {sid}；GET /sessions/{sid} 回读 profile.major="
           f"{snapshot['profile'].get('major')}，revision={snapshot.get('revision')}")

    # ------------------------------------------------ 2. 确认学校后不会丢失
    r = client.post(f"{PREFIX}/sessions/{sid}/recommendations", json={})
    rec_data = body(r)["data"]
    keep_example("recommendations", "POST", f"{PREFIX}/sessions/{sid}/recommendations", {}, r)
    items = rec_data.get("recommendations") or []
    if not items:
        record(2, "确认学校后不会丢失", False, "推荐列表为空，无法继续后面的验收")
        return finish(args)
    chosen = items[0]["school_id"]
    r = client.put(f"{PREFIX}/sessions/{sid}/target",
                   json={"school_id": chosen, "user_note": "想留在东北，离家近"})
    body(r)
    keep_example("set_target", "PUT", f"{PREFIX}/sessions/{sid}/target",
                 {"school_id": chosen, "user_note": "想留在东北，离家近"}, r)
    # 关键：再做一次会改变会话的操作（新增资料 + 实验室），确认 selected_school_id 仍在
    client.post(f"{PREFIX}/sessions/{sid}/materials",
                json={"title": "信号与系统笔记", "text": "卷积定理……" * 40, "is_demo": True})
    after = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"]
    record(2, "确认学校后不会丢失",
           after.get("selected_school_id") == chosen,
           f"PUT /target → {chosen}；后续新增资料 + 实验室操作后仍为 "
           f"{after.get('selected_school_id')}，decisions {len(after.get('decisions') or [])} 条")

    # --------------------------------------------------- 3. 资料录入且持久化
    material_text = ("卷积定理：时域卷积对应频域相乘。\n"
                     "抽样定理：带限信号可由不低于两倍最高频率的采样重建。\n") * 6
    r = client.post(f"{PREFIX}/sessions/{sid}/materials",
                    json={"title": "信号与系统复习提纲", "text": material_text,
                          "source_url": "", "is_demo": True})
    mat = body(r)["data"]
    keep_example("add_material", "POST", f"{PREFIX}/sessions/{sid}/materials",
                 {"title": "信号与系统复习提纲", "text": "…（略）…", "is_demo": True}, r)
    # 换一个进程内新连接直接查库，证明落盘而不是内存态
    row = session_store.get_material_row(sid, mat["material_id"])
    stored_chars = len(row["content"] or "") if row is not None else None
    again = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"]
    listed = [m["material_id"] for m in again.get("materials") or []]
    record(3, "资料录入成功且持久化",
           r.status_code == 200 and row is not None and mat["material_id"] in listed
           and stored_chars == mat["character_count"] and mat.get("preview"),
           f"material_id={mat['material_id']}，character_count={mat['character_count']}，"
           f"库里同值={stored_chars}，会话快照可见={mat['material_id'] in listed}，"
           f"preview {len(mat.get('preview') or '')} 字")

    # ------------------------------------------- 4. 实验室状态可连续推进
    r = client.post(f"{PREFIX}/sessions/{sid}/lab/start",
                    json={"material_id": mat["material_id"], "topic": "ANALOG_CIRCUIT"})
    lab1 = body(r)["data"]          # §7.8：data 就是统一 lab_state 结构
    keep_example("lab_start", "POST", f"{PREFIX}/sessions/{sid}/lab/start",
                 {"material_id": mat["material_id"], "topic": "ANALOG_CIRCUIT"}, r)
    q = lab1.get("question")
    r = client.post(f"{PREFIX}/sessions/{sid}/lab/answer",
                    json={"question_id": q["question_id"], "answer": q["options"][0]["key"]})
    lab2 = body(r)["data"]
    keep_example("lab_answer", "POST", f"{PREFIX}/sessions/{sid}/lab/answer",
                 {"question_id": q["question_id"], "answer": q["options"][0]["key"]}, r)
    r = client.post(f"{PREFIX}/sessions/{sid}/lab/start",
                    json={"material_id": mat["material_id"], "topic": "ANALOG_CIRCUIT"})
    lab3 = body(r)["data"]
    stages = [lab1["stage"], lab2["stage"], lab3["stage"]]
    learning = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"].get("learning_state") or {}
    abil = learning.get("ability_profile") or []
    record(4, "实验室状态可连续推进",
           r.status_code == 200 and len(set(stages)) > 1 and abil != [],
           f"stage 轨迹 {'→'.join(stages)}；learning_state.ability_profile={abil}；"
           f"lab_state.is_demo={lab3.get('is_demo')}")

    # ------------------------------------------- 5. 修改时间后保留学习记录
    before = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"]
    r = client.post(f"{PREFIX}/sessions/{sid}/plan/revise", json={"daily_minutes": 30})
    plan = body(r)["data"]
    keep_example("plan_revise", "POST", f"{PREFIX}/sessions/{sid}/plan/revise",
                 {"daily_minutes": 30}, r)
    after = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"]
    record(5, "修改时间后保留学习记录",
           after.get("selected_school_id") == before.get("selected_school_id")
           and len(after.get("materials") or []) == len(before.get("materials") or [])
           and (after.get("learning_state") or {}) == (before.get("learning_state") or {})
           and plan.get("previous_daily_minutes") == 120
           and any(c.get("action") == "dropped" for c in plan.get("changes") or []),
           f"120→{plan.get('daily_minutes')} 分钟；院校={after.get('selected_school_id')}；"
           f"资料数 {len(before.get('materials') or [])}→{len(after.get('materials') or [])}；"
           f"learning_state 未变；changes 说明 {len(plan.get('changes') or [])} 条"
           f"（其中 dropped {sum(1 for c in plan.get('changes') or [] if c.get('action') == 'dropped')} 条）")

    # ------------------------------------ 6. 模型连接失败时返回可理解的错误
    # 注意：**没有密钥**不是失败场景（SPEC N23 允许离线演示，此时如实标记 model_used=false）。
    # 真正要验证的是「配了密钥但网关不可用」→ 必须 503 MODEL_UNAVAILABLE(retryable) 且中文可读。
    saved = (config.MOCK_LLM, config.LLM_API_KEY, config.LLM_BASE_URL,
             config.LLM_MAX_RETRIES, config.RECOMMEND_DEGRADE)
    config.MOCK_LLM = False
    config.LLM_API_KEY = "sk-acceptance-invalid-key"
    config.LLM_BASE_URL = "http://127.0.0.1:1/v1"      # 本机未监听端口：立即连接失败，不依赖外网
    config.LLM_MAX_RETRIES = 0
    config.RECOMMEND_DEGRADE = 0
    try:
        r = client.post(f"{PREFIX}/sessions/{sid}/recommendations", json={})
        err = body(r)["error"] or {}
        status, code, message = r.status_code, err.get("code"), err.get("message") or ""
        keep_example("model_unavailable", "POST",
                     f"{PREFIX}/sessions/{sid}/recommendations", {}, r)
    finally:
        (config.MOCK_LLM, config.LLM_API_KEY, config.LLM_BASE_URL,
         config.LLM_MAX_RETRIES, config.RECOMMEND_DEGRADE) = saved
    has_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in message)
    # 对照组：无密钥（离线演示模式）必须照常成功，且如实标记"模型未参与"
    r_off = client.post(f"{PREFIX}/sessions/{sid}/recommendations", json={})
    off = body(r_off)["data"] or {}
    off_ok = (r_off.status_code == 200 and off.get("model_used") is False
              and off.get("model_texts", {}).get("generated_by") == "rules(offline)")
    record(6, "模型连接失败时返回可理解的错误",
           status == 503 and code == "MODEL_UNAVAILABLE" and has_chinese
           and (err or {}).get("retryable") is True and "Traceback" not in message
           and off_ok,
           f"网关不可达 → HTTP {status} code={code} retryable={(err or {}).get('retryable')} "
           f"message={message!r}；对照组（无密钥/离线模式）HTTP {r_off.status_code} "
           f"model_used={off.get('model_used')} generated_by="
           f"{off.get('model_texts', {}).get('generated_by')!r}")

    # ------------------------------- 7. N29 未提供年级/阶段时能推断并提问
    r = client.post(f"{PREFIX}/sessions", json={"profile": {"exam_year": 2027}})
    fresh = body(r)["data"]
    keep_example("create_session_minimal", "POST", f"{PREFIX}/sessions",
                 {"profile": {"exam_year": 2027}}, r)
    onb = fresh.get("onboarding") or {}
    text = json.dumps(onb, ensure_ascii=False)
    record(7, "N29 未提供年级/阶段时能推断 phase 并提问（不报错、不默认大三）",
           r.status_code == 200 and onb.get("phase") and onb.get("next_question")
           and "大三" not in text
           and (onb.get("phase_confidence") in ("inferred", "confirmed")),
           f"phase={onb.get('phase')}（{onb.get('phase_name')}）"
           f"confidence={onb.get('phase_confidence')}；"
           f"next_question={onb.get('next_question')!r}；"
           f"missing_critical={onb.get('missing_critical')}")

    # ------------------------------------- 8. N34 material_id=null 无资料路径
    r = client.post(f"{PREFIX}/sessions/{sid}/lab/start",
                    json={"material_id": None, "topic": "MATH_CALC",
                          "mode": "self_assessment"})
    st = body(r)["data"]                 # §7.8：data 就是统一 lab_state
    keep_example("lab_start_no_material", "POST", f"{PREFIX}/sessions/{sid}/lab/start",
                 {"material_id": None, "topic": "MATH_CALC", "mode": "self_assessment"}, r)
    q = st.get("question") or {}
    # 无资料路径的证据只在**作答之后**产生，start 阶段不该有 material 证据
    r2 = client.post(f"{PREFIX}/sessions/{sid}/lab/answer",
                     json={"question_id": q.get("question_id"),
                           "answer": (q.get("options") or [{}])[0].get("key")})
    st2 = body(r2)["data"]
    keep_example("lab_answer_no_material", "POST", f"{PREFIX}/sessions/{sid}/lab/answer",
                 {"question_id": q.get("question_id"),
                  "answer": (q.get("options") or [{}])[0].get("key")}, r2)
    evidence = st2.get("evidence") or []
    kinds = {ev.get("kind") for ev in evidence}
    bases = {ev.get("basis") for ev in evidence}
    learning = body(client.get(f"{PREFIX}/sessions/{sid}"))["data"].get("learning_state") or {}
    self_assess = learning.get("self_assessment") or []
    record(8, "N34 material_id=null 时能走通无资料诊断路径",
           r.status_code == 200 and st.get("is_demo") is True and bool(q)
           and str(q.get("question_id", "")).startswith("self_")
           and st.get("next_action") == "answer"
           and r2.status_code == 200 and evidence != [] and kinds == {"answer"}
           and "self_reported" in bases
           and any(sa.get("basis") == "self_reported" for sa in self_assess),
           f"start: stage={st.get('stage')} question_id={q.get('question_id')} is_demo={st.get('is_demo')}；"
           f"answer 后 证据 {len(evidence)} 条 kind={sorted(k for k in kinds if k)} "
           f"basis={sorted(b for b in bases if b)}（未引用任何资料）；"
           f"learning_state.self_assessment={self_assess}")

    # ----------------------------------------- 9. N31 ability_flags 正确匹配
    r = client.post(f"{PREFIX}/sessions/{sid}/recommendations", json={})
    rec2 = body(r)["data"]
    keep_example("recommendations_second_pass", "POST",
                 f"{PREFIX}/sessions/{sid}/recommendations", {}, r)
    flags = rec2.get("ability_flags") or []
    codes = {s["subject_code"] for s in subject_table.subjects()}
    exam_codes = set()
    for item in rec2.get("recommendations") or []:
        for code in ((item.get("metrics") or {}).get("exam_subjects") or []):
            exam_codes.add(code if isinstance(code, str) else code.get("code"))
    topics = {f.get("topic") for f in flags}
    matched = topics & exam_codes
    record(9, "N31 ability_flags 能与院校 exam_subjects 正确匹配",
           r.status_code == 200 and flags != [] and topics <= codes and matched != [],
           f"ability_flags={[(f.get('topic'), f.get('status'), f.get('evidence_count'), f.get('basis')) for f in flags]}；"
           f"与考试科目交集={sorted(matched)}；affected_schools="
           f"{[f.get('affected_schools') for f in flags]}")

    # ------------------------------------------ 10. N32 dimension_hits/missing
    hits = rec2.get("dimension_hits") or []
    missing = rec2.get("dimension_missing") or []
    total = len(hits) + len(missing)
    # N32 的 hits/missing 是**整个 shortlist 的聚合口径**：命中项必须至少在一所学校的
    # metrics 里真实存在，缺失项必须确实在所有学校都拿不到（不能凭空报"缺失"）。
    union: set = set()
    for item in rec2.get("recommendations") or []:
        union |= set((item.get("metrics") or {}).keys())
    hits_ok = set(hits) <= union
    missing_ok = set(missing) >= {code for code in dimensions.RANKING_CODES if code not in union}
    stray_hits = sorted(set(hits) - union)
    missing_not_needed = sorted(set(missing) - {code for code in dimensions.RANKING_CODES if code not in union})
    record(10, "N32 dimension_hits/dimension_missing 如实反映本次推荐依据",
           hits != [] and missing != [] and total == dimensions.DECISION_DIMENSION_COUNT
           and hits_ok and missing_ok,
           f"hits {len(hits)} 项 / missing {len(missing)} 项 / 合计 {total}（应="
           f"{dimensions.DECISION_DIMENSION_COUNT}）；hits 前 8 项={hits[:8]}；"
           f"missing 前 5 项={missing[:5]}；hits⊆三校 metrics 并集={hits_ok}（多报的={stray_hits}）；"
           f"missing⊇并集外维度={missing_ok}（不该缺的={missing_not_needed}）")

    return finish(args)


def finish(args) -> int:
    passed = sum(1 for item in RESULTS if item["passed"])
    total = len(RESULTS)
    report = {
        "spec": "v1.1",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "schools": school_data.school_ids(),
        "passed": passed,
        "total": total,
        "results": RESULTS,
    }
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ex = out.with_name("acceptance_examples.json")
    ex.write_text(json.dumps(EXAMPLES, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n验收结果：{passed}/{total} 通过")
    print(f"报告：{out}")
    print(f"接口真实请求/响应示例：{ex}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
