"""前端静态一致性自检（frontend/ 没有构建步骤，用它代替打包器的报错）。

它检查四件事：
1. `index.html` 里每个 `id` 都不重复，且 `app.js` 里 `byId('x')` 引用的 id 都存在；
2. 左侧导航每个 `data-step="N"` 在 `SECTIONS` 里有对应项，且 `id="step-N"` 的 section 存在（反之亦然）；
3. `index.html` 引用的 `style.css` / `app.js` 文件真实存在，且没有引用外部 CDN（离线演示要求）；
4. Agent 面板的四张卡片、输入框、发送按钮、示例 chip 都在。

跑法（仓库根目录）：
    .venvs\\edu-agent\\Scripts\\python.exe scripts\\frontend_check.py
退出码 0 表示全绿。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HTML = FRONTEND / "index.html"
JS = FRONTEND / "app.js"
CSS = FRONTEND / "style.css"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

problems: list[str] = []
notes: list[str] = []


def check(cond: bool, message: str) -> None:
    if cond:
        notes.append(f"[PASS] {message}")
    else:
        problems.append(message)
        notes.append(f"[FAIL] {message}")


def main() -> int:
    check(HTML.exists(), f"index.html 存在（{HTML.relative_to(ROOT)}）")
    check(JS.exists(), f"app.js 存在（{JS.relative_to(ROOT)}）")
    check(CSS.exists(), f"style.css 存在（{CSS.relative_to(ROOT)}）")
    if problems:
        print("\n".join(notes))
        return 1

    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    ids = re.findall(r'\bid="([^"]+)"', html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    check(not dupes, f"index.html 没有重复 id（重复：{dupes}）")
    id_set = set(ids)

    used = sorted(set(re.findall(r"byId\('([^']+)'\)", js)))
    injected = set(re.findall(r'id="([^"]+)"', js))  # 动态注入的片段（innerHTML 之后再 byId）
    missing = [i for i in used if i not in id_set and i not in injected]
    check(not missing, f"app.js 引用的 {len(used)} 个 id 都存在"
                       f"（静态 {len(used) - len(injected & set(used))} 个 + 动态注入 "
                       f"{len(injected & set(used))} 个；缺失：{missing}）")

    steps = sorted(set(re.findall(r'data-step="(\d+)"', html)), key=int)
    sections = sorted(set(re.findall(r'id="step-([0-9a-z\-]+)"', html)))
    expect_steps = ["0" if s == "agent" else s for s in sections]
    check(steps == sorted(expect_steps, key=int),
          f"导航 data-step {steps} 与 section id step-{sections} 一一对应（0 ↔ agent）")
    for step in steps:
        check(bool(re.search(rf"\b{step}:\s*\[", js)),
              f"SECTIONS 里有 {step} 的条目")

    section_map = re.search(r"var SECTIONS = \{(.*?)\n\};", js, re.S)
    check(bool(section_map), "app.js 里能读到 SECTIONS 定义")
    if section_map:
        declared = set(re.findall(r"(\d+):\s*\[([^\]]*)\]", section_map.group(1)))
        for step, body in declared:
            check(f"step-{step}" in body or (step == "0" and "step-agent" in body),
                  f"SECTIONS[{step}] 指向 step-{step}")

    deps = set(re.findall(r'(?:href|src)="([^"]+)"', html))
    external = [d for d in deps if d.startswith("http") or d.startswith("//")]
    check(not external, f"没有外部 CDN 依赖（离线可跑）（外部：{external}）")
    for dep in deps:
        if dep.startswith("#") or dep.startswith("data:"):
            continue
        check((FRONTEND / dep).exists(), f"index.html 引用的 {dep} 存在")

    for need in ("agent_input", "btnAgentSend", "agent_meta", "agent_reply",
                 "agent_trace", "agent_history", "agent_card_trace", "btnGoAgent"):
        check(need in id_set, f"Agent 面板元素 id={need} 存在")
    chips = re.findall(r'data-agent-example="([^"]+)"', html)
    check(len(chips) >= 3, f"Agent 示例问题至少 3 个（实得 {len(chips)}）")

    agent_fn = JS.read_text(encoding="utf-8")
    for fn in ("function agentSend", "function renderAgent", "function agentTraceHtml",
               "function bindAgent", "bindAgent();"):
        check(fn in agent_fn, f"app.js 里有 {fn}")
    check("'/api/v1/agent'" in js, "前端只调 /api/v1/agent（同源，不直连模型）")
    check("trace-item" in css and "agent-history" in css, "style.css 里有 Agent 面板样式")

    print("\n".join(notes))
    if problems:
        print(f"\n结论：{len(problems)} 项不通过")
        return 1
    print(f"\n结论：{len(notes)} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
