"""Agent 入口的真实模型自检（P0-1：一句话进，模型自己决定调哪些工具）。

跑法（仓库根目录，需要 `.env` 里的真实密钥）：
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\agent_model_check.py

它做三件事：
1. 只给一句自然语言（**不建会话**），看模型是否自己 `create_session` 并往下走；
2. 再给两句自然语言，看它是否会自主推进（推荐 / 开小测 / 定校 / 改计划）；
3. 把每一轮的**决策轨迹**（thought → 工具 → 结果）写进 `docs/evidence/agent_model_e2e.txt`。

退出码 0 表示全绿。轨迹文件本身就是"Agent 自主决策"的证据，可用于答辩展示。
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["MOCK_LLM"] = "0"
os.environ["EDU_AGENT_DB"] = str(pathlib.Path(tempfile.mkdtemp()) / "agent_e2e.db")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.main import app  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "agent_model_e2e.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

lines: list[str] = []
failures: list[str] = []


def log(text: str = "") -> None:
    lines.append(text)


def write_out() -> None:
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:  # pragma: no cover
        pass


atexit.register(write_out)


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)
        log(f"!! {message}")


def repeated_failure(data: dict) -> str | None:
    """同一轮里是否**重复**调用同一个失败的工具（说明模型没读懂错误、在原地打转）。

    工具报错本身是允许的观察结果（例如没定院校就改计划 → 409），
    要求的是模型据此改正或换一条路，而不是原样重试。
    """
    seen: set[str] = set()
    for entry in data.get("trace") or []:
        if entry.get("action") != "tool" or entry.get("ok"):
            continue
        key = f"{entry.get('tool')}::{json.dumps(entry.get('args') or {}, sort_keys=True, ensure_ascii=False)}"
        if key in seen:
            return key
        seen.add(key)
    return None


def failed_tools(data: dict) -> list[str]:
    return [str(e.get("tool")) for e in (data.get("trace") or [])
            if e.get("action") == "tool" and not e.get("ok")]


def turn(client, message: str, session_id: str | None = None, index: int = 1) -> dict:
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    resp = client.post("/api/v1/agent", json=payload)
    if resp.status_code != 200:
        log(f"!! 第 {index} 轮 HTTP {resp.status_code}：{resp.text[:600]}")
        raise SystemExit(1)
    body = resp.json()
    if not body.get("ok"):
        log(f"!! 第 {index} 轮失败响应：{json.dumps(body, ensure_ascii=False)[:600]}")
        raise SystemExit(1)
    data = body["data"]

    log(f"\n--- 第 {index} 轮 ---")
    log(f"用户：{message}")
    log(f"模型推理：{data.get('trace')[0].get('thought') if data.get('trace') else '（无轨迹）'}")
    for entry in data.get("trace") or []:
        mark = "工具" if entry.get("action") == "tool" else entry.get("action")
        detail = entry.get("result_summary") or entry.get("error") or entry.get("reply") or ""
        log(f"  [{mark}] {entry.get('tool') or '-'} :: {detail}")
        if entry.get("thought") and entry is not (data.get("trace") or [{}])[0]:
            log(f"         （决策理由：{entry.get('thought')}）")
    log(f"Agent 回复：{data.get('reply')}")
    log(f"本轮工具链：{data.get('tools_used')} steps={data.get('steps')} "
        f"terminated={data.get('terminated')} model_used={data.get('model_used')} "
        f"generated_by={data.get('generated_by')} is_demo={data.get('is_demo')}")
    return data


def main() -> int:
    log("Agent 决策层 · 真实模型端到端自检")
    log(f"base_url={config.LLM_BASE_URL} model={config.LLM_MODEL} "
        f"mock={config.MOCK_LLM} max_steps={config.MAX_AGENT_STEPS}")
    if not config.LLM_API_KEY:
        log("!! 没有读到 LLM_API_KEY：请确认仓库根的 .env")
        return 1

    with TestClient(app) as client:
        first = turn(client, "我是通信工程大三，想考长春或哈尔滨的学校，但数学一直没底", index=1)
        check(first.get("model_used") is True, "第 1 轮应使用真实模型")
        check(first.get("generated_by") == "model", f"第 1 轮 generated_by={first.get('generated_by')!r}")
        check("create_session" in (first.get("tools_used") or []),
              "第 1 轮应由 Agent 自己建档（create_session）")
        check(bool(first.get("reply")), "第 1 轮必须有给用户的回复")
        sid = first.get("session_id")
        check(bool(sid), "第 1 轮应返回 session_id")

        second = turn(client, "那就先测一下我数学到底什么水平", session_id=sid, index=2)
        third = turn(client, "B", session_id=sid, index=3)
        fourth = turn(client, "每天只能学一个小时，帮我安排一下", session_id=sid, index=4)

        used: list[str] = []
        for data in (first, second, third, fourth):
            used.extend(data.get("tools_used") or [])
            bad = failed_tools(data)
            if bad:
                log(f"（可接受的工具报错：{bad} —— 模型已据此改口/换路）")
            check(repeated_failure(data) is None,
                  f"有重复调用同一个失败工具：{repeated_failure(data)}")
        log(f"\n四轮累计工具链：{used}")
        check(any(tool in used for tool in ("recommend", "lab_start")),
              "四轮里至少要自主走到推荐或开小测")
        check(all(data.get("model_used") for data in (first, second, third, fourth)),
              "四轮都必须走真实模型（不允许静默降级）")

        snapshot = client.get(f"/api/v1/sessions/{sid}").json()
        check(snapshot.get("ok") is True, "会话快照应可读")
        state = (snapshot.get("data") or {})
        log(f"最终会话：phase={((state.get('onboarding') or {}).get('phase'))} "
            f"revision={state.get('revision')} materials={len(state.get('materials') or [])} "
            f"selected={(state.get('selected_school_id'))}")

    if failures:
        log(f"\n结论：{len(failures)} 项不通过")
        return 1
    log("\n结论：全部通过（Agent 自主建档/推进，轨迹可审计，无静默降级）")
    return 0


if __name__ == "__main__":
    code = main()
    print(f"exit={code} evidence={OUT}")
    raise SystemExit(code)
