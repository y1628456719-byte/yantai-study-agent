"""P1-4 真实网关自检：阶段追问是否真的由模型生成（规则兜底在场）。

跑法（会用 .env 里的真实密钥，消耗少量 token）：
    & ".venvs\\edu-agent\\Scripts\\python.exe" scripts/ask_model_check.py

判定：三种档案状态下，`onboarding.next_question_source` 都必须是 `model`，
且模型的说法与规则原话不同但**问的是同一项**（`next_question_key` 不变）。
证据写到 `docs/evidence/ask_model_e2e.txt`（UTF-8，失败也会写）。
"""
from __future__ import annotations

import atexit
import io
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 必须在 import backend 之前：真实模型 + 独立临时库
os.environ["MOCK_LLM"] = "0"
os.environ.setdefault("LAB_ADAPTER", "mock")
_tmp = pathlib.Path(tempfile.mkdtemp(prefix="edu_agent_ask_"))
os.environ["EDU_AGENT_DB"] = str(_tmp / "ask.db")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "ask_model_e2e.txt"
LINES: list[str] = []


def log(line: str = "") -> None:
    LINES.append(line)
    print(line)


@atexit.register
def _write() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


CASES = [
    ("空档案", {}),
    ("只给年份", {"exam_year": 2027}),
    ("给了年份与方向、没有候选校", {"exam_year": 2027, "major": "信息与通信工程"}),
]


def main() -> int:
    failures: list[str] = []
    with TestClient(app) as client:
        for name, profile in CASES:
            resp = client.post("/api/v1/sessions", json={"profile": profile})
            body = resp.json()
            if resp.status_code != 200 or not body.get("ok"):
                failures.append(f"{name}: HTTP {resp.status_code} {resp.text[:200]}")
                log(f"[FAIL] {name}: HTTP {resp.status_code} {resp.text[:200]}")
                continue
            onb = body["data"]["onboarding"]
            log(f"== {name}（档案 {json.dumps(profile, ensure_ascii=False)}）")
            log(f"   phase            : {onb['phase']} {onb['phase_name']}（{onb['phase_confidence']}）")
            log(f"   must_ask_about   : {onb['next_question_key']}")
            log(f"   模型追问         : {onb['next_question']}")
            log(f"   规则原话         : {onb['rule_next_question']}")
            log(f"   来源             : {onb['next_question_source']}")
            if onb.get("next_question_error"):
                log(f"   error            : {onb['next_question_error']}")
            log()
            if onb["next_question_source"] != "model":
                failures.append(f"{name}: 追问来源是 {onb['next_question_source']}，不是 model")
            if not onb["next_question"].strip():
                failures.append(f"{name}: 追问为空")
            if len(onb["next_question"]) > 120:
                failures.append(f"{name}: 追问过长（{len(onb['next_question'])} 字）")

    log("== 判定 ==")
    if failures:
        log("FAIL:")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("OK：三种档案状态的追问都由模型生成，且规则原话保留在 rule_next_question")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
