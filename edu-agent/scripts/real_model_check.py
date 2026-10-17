"""真实模型端到端自检（需要 `.env` 里的真实密钥；离线模式跑不出 `generated_by="model"`）。

跑法（仓库根目录）：
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\real_model_check.py

覆盖：真实模型出文案的推荐 → 二次推荐（能力画像）→ 确认目标 → 选计划。
输出写 `docs/evidence/real_model_e2e.txt`（规避 Windows 控制台 GBK 乱码），退出码 0 表示全绿。
"""
from __future__ import annotations

import atexit
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 真实模型：显式设成 0，并用临时库，避免污染演示数据
os.environ["MOCK_LLM"] = "0"
os.environ["EDU_AGENT_DB"] = str(pathlib.Path(tempfile.mkdtemp()) / "real_e2e.db")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.main import app  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "real_model_e2e.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

lines: list[str] = []


def log(text: str = "") -> None:
    lines.append(text)


def write_out() -> None:
    """无论成功还是中途抛异常，都把已记录内容写出来（否则只看到栈，看不到失败原因）。"""
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:  # pragma: no cover
        pass


atexit.register(write_out)


def body(resp, expect: int = 200) -> dict:
    if resp.status_code != expect:
        log(f"!! HTTP {resp.status_code}（期望 {expect}）：{resp.text[:800]}")
        raise SystemExit(1)
    payload = resp.json()
    if not payload.get("ok"):
        log(f"!! 失败响应：{payload}")
        raise SystemExit(1)
    return payload["data"]


def main() -> int:
    log(f"model_key_configured={bool(config.LLM_API_KEY)} base_url={config.LLM_BASE_URL} "
        f"model={config.LLM_MODEL} mock={config.MOCK_LLM}")
    if not config.LLM_API_KEY:
        log("!! 没有读到 LLM_API_KEY：请确认仓库根的 .env")
        return 1

    with TestClient(app) as client:
        session = body(client.post("/api/v1/sessions", json={"profile": {
            "exam_year": 2027,
            "major": "信息与通信工程",
            "preferred_regions": ["长春", "哈尔滨", "沈阳"],
            "daily_minutes": 90,
            "study_status": "边工作边考",
            "target_score": 330,
        }}))
        sid = session["session_id"]
        log(f"POST /sessions -> 200 sid={sid}")

        first = body(client.post(f"/api/v1/sessions/{sid}/recommendations", json={}))
        texts = first.get("model_texts") or {}
        log("POST /recommendations -> 200")
        log(f"model_used={first.get('model_used')} generated_by={texts.get('generated_by')} "
            f"model_error={texts.get('model_error')}")
        log(f"understood_as={first['profile_echo'].get('understood_as')}")
        for item in first["recommendations"]:
            log(f"  - {item['school_name']} tier={item['metrics'].get('tier')} "
                f"fit_reasons={len(item['fit_reasons'])} risks={len(item['risks'])} unknowns={len(item['unknowns'])}")
        log(f"excluded={[e.get('school_name') for e in first['excluded']]}")
        log(f"tier_rule={first.get('tier_rule')}")
        log(f"dimension_hits={len(first['dimension_hits'])} dimension_missing={len(first['dimension_missing'])}")

        body(client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "neu"}))
        log("PUT /target -> 200 selected=neu")

        started = body(client.post(f"/api/v1/sessions/{sid}/lab/start",
                                   json={"material_id": None, "topic": "ANALOG_CIRCUIT",
                                         "mode": "self_assessment"}))
        question = started["question"] or {}
        log(f"lab/start(material_id=null) -> 200 question={question.get('question_id')}")
        answered = body(client.post(f"/api/v1/sessions/{sid}/lab/answer",
                                    json={"question_id": question.get("question_id"), "answer": "2"}))
        log(f"lab/answer -> 200 stage={answered.get('stage')} next_action={answered.get('next_action')}")

        second = body(client.post(f"/api/v1/sessions/{sid}/recommendations", json={}))
        log("POST /recommendations(2nd) -> 200")
        flags = second.get("ability_flags") or []
        log(f"ability_flags={[(f.get('topic'), f.get('status'), f.get('evidence_count')) for f in flags]}")
        log(f"dimension_hits={len(second['dimension_hits'])} dimension_missing={len(second['dimension_missing'])}")

        revised = body(client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 45}))
        log(f"POST /plan/revise -> 200 generated_by={revised.get('generated_by')} "
            f"model_error={revised.get('model_error')}")
        for change in revised["changes"]:
            mark = "[砍掉]" if change["action"] == "dropped" else "[保留]"
            log(f"  {mark} {change['task']} —— {change['reason']}")
        for task in revised["plan"]:
            log(f"  [计划] {task['task']} {task['minutes']}min —— {task['reason']}")

        generated = revised.get("generated_by") or ""
        if generated != "model":
            log(f"!! 期望真实模型文案，实际 generated_by={generated!r}")
            return 1
    return 0


if __name__ == "__main__":
    code = main()
    print(f"exit={code} evidence={OUT}")
    raise SystemExit(code)
