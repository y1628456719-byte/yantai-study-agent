# 给队友 B：Agent 面板视觉收尾（交接说明）

> **一句话**：这个演示台的**视觉与五步界面是你的**（commit `23fee5a`）。
> 我在 `a6e29d7` 里往你的三个文件里**追加**了一块「★ 一句话开始 / Agent 面板」，
> 共 **+239 / −2 行**。这份文档把这块的元素、数据契约和"不能改的东西"交给你，
> 视觉统一由你来做——**我之后不再动 `frontend/`**，除非你让我改。

## 1. 我到底动了哪三处

| 文件 | 我加的 | 行号（当前） |
|---|---|---|
| `frontend/index.html` | +60 行：导航项 + 一个 `<section id="step-agent">` + 第 1 步顶部一个入口按钮 | 见 §2 |
| `frontend/app.js` | +149 行：5 个渲染函数 + 1 个发送函数 + 1 个绑定函数 + `SECTIONS[0]` | `app.js:1393-1533`，`SECTIONS` 在 `app.js:43` |
| `frontend/style.css` | +32 行：Agent 面板的全部样式类 + 1 条窄屏覆写 | `style.css:357-385`，覆写在 `style.css:398` |

我用的都是你已有的设计 token（`--brand` / `--panel` / `--line` / `--ink-soft` / `--red` / `--green` / `--red-bg` / `--grey-bg`），所以能跑，但**间距、圆角、层级这些细节没有跟你的设计语言逐项对齐**——这正是要你收的地方。

## 2. HTML 结构（`index.html`）

- `index.html:68` `<section id="step-agent" class="step hidden">`（与 `step-1`…`step-5` 同级、同一种显隐机制）
- `index.html:85` `<textarea id="agent_input" class="input agent-input" rows="3">`
- `index.html:89-92` 四个示例 chip：`<button class="chip chip-btn" data-agent-example="…">`
- `index.html:95` `<button id="btnAgentSend" class="btn btn-primary">发送给 Agent</button>`
- `index.html:100` `<div id="agent_meta" class="card agent-meta hidden">`
- `index.html:102-104` 回复卡：`#agent_card_reply` → `#agent_reply.agent-reply`
- `index.html:107-109` 轨迹卡：`#agent_card_trace` → `#agent_trace.trace-list`
- `index.html:112-114` 对话记录卡：`#agent_card_history` → `#agent_history.agent-history`
- `index.html:124` 第 1 步里的入口按钮：`<button id="btnGoAgent" class="btn btn-ghost btn-sm">懒得填表？用一句话让 Agent 自己建档 →</button>`
- 左侧导航新增的第一个按钮：`data-step="0"`，文字「★ 一句话开始」（`SECTIONS[0] = ['step-agent']`）

## 3. 数据契约（渲染时能拿到什么）

`POST /api/v1/agent` 的 `data`（前端 `api()` 已自动解包外层 `{ok,data,error,request_id}`）：

```jsonc
{
  "session_id": "s_fb7198e76b8d",
  "reply": "……给用户看的一段话……",
  "trace": [                       // 每条 = 一步决策，按顺序渲染
    { "step": 1, "thought": "为什么做这一步", "action": "tool|ask|reply|finish|error",
      "tool": "create_session", "args": { "exam_year": 2027 },
      "result_summary": "已建会话 s_…（阶段 P2 目标筛选期）",
      "ok": true, "error": null, "code": null, "latency_ms": 0 }
  ],
  "steps": 2, "tools_used": ["create_session", "lab_start"],
  "state_patch": { "revision": 2, "phase": "P2", "selected_school_id": null },
  "terminated": "reply",           // 或 "ask" / "steps_limit" / null
  "model_used": true, "generated_by": "model",   // 或 "rules(offline)"
  "is_demo": false
}
```

已经渲染出来的状态与类名（你可以随意改类名，但**改完请同步改 `app.js` 里对应的字符串**）：

| 状态 | 当前类名 |
|---|---|
| 元信息 chip：真实模型决策 / 规则兜底 | `.tag.tag-measured` / `.tag.tag-judge` |
| 元信息 chip：步数 / 会话号 | `.chip.chip-grey` |
| 元信息 chip：提前结束 | `.chip.chip-amber` |
| 轨迹条目（正常 / 失败） | `.trace-item` / `.trace-item-bad` |
| 轨迹：步骤圆标 / 工具标签 / 耗时 / 为什么 / 参数 / 结果 / 错误 | `.trace-step` / `.tag.tag-fact` / `.trace-lat` / `.trace-thought` / `.trace-args` / `.trace-ok` / `.trace-err` |
| 对话记录行（用户 / Agent） | `.hist-row.hist-user` / `.hist-row.hist-assistant`，内层 `.hist-who` + `.hist-text` |
| 空态 | `.empty`（轨迹为空、对话为空） |
| 输入区 / 示例 chip / 操作行 / 元信息行 / 回复体 | `.agent-input` / `.chip-btn` / `.agent-actions` / `.agent-meta` / `.agent-reply` |
| 卡标题小字 | `.card-sub` |

## 4. ⚠️ 不能改的东西（改了会坏）

1. **这些 id 被静态检查与 JS 依赖**：`step-agent`、`agent_input`、`agent_meta`、`agent_reply`、`agent_card_reply`、`agent_trace`、`agent_card_trace`、`agent_history`、`agent_card_history`、`btnAgentSend`、`btnGoAgent`。
   `python scripts/frontend_check.py` 会验（当前 **38/38**）。
2. **导航按钮的 `data-step="0"` 与 section id `step-agent` 的别名映射**（在 `SECTIONS` 与检查脚本里都写死了）。
3. **`api()` 的用法**：它返回的是**已解包的内层 `data`**，不要再读 `.data.data`。
4. **零外部依赖、相对路径引用**（`href="style.css"` / `src="app.js"`）。三个文件必须**同目录发布**——只发 `index.html` 会退化成无样式无脚本的裸页面。
5. 我加面板时**没有改**你的五步交互、没有改 `switchStep` / `bindButtons` / `loadSession` 的语义，也没改配色 token。

## 5. 建议你补的视觉状态（我做的很朴素）

- **加载中**：目前只靠 `setBusy(btn, true, 'Agent 思考中…')`（真实模型一轮约 6–15s，离线 <1s）。
  值得给整个轨迹区一个骨架/进度感，否则演示时会像卡住。
- **失败态**：Agent 出错走你已有的 `showError(err)`；但**轨迹里单条工具失败**是**有意保留**的（模型读到 409 后会改口，这比假装没发生更可审计），需要一眼能看懂"这条红的是它自己试错"。
- **长轨迹**：最多 8 步（`MAX_AGENT_STEPS`），当前是纵向全部展开；屏幕上 6 分钟内要看得清。
- **窄屏**：我只加了一条 `.hist-who { flex-basis: 34px }`，其余没做响应式。
- **元信息行**：现在 chip 直接平铺，`generated_by` 是「真实模型决策 / 规则兜底」——这是**给评委看的关键信息**（诚实标注模型是否参与），建议给它更强的视觉权重。

## 6. 怎么自检 / 怎么看

```powershell
cd D:\dsh environment\dsh yield\edu-agent
$env:MOCK_LLM = "1"                                     # 离线最稳，Agent 走规则兜底，同一套工具
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" -m uvicorn backend.main:app --port 8000
# 浏览器打开 http://127.0.0.1:8000/  → 左侧「★ 一句话开始」→ 点第一个示例 chip → 发送
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" scripts\frontend_check.py   # 38/38
```

> 我这台机器上**没有浏览器插件**（`bsk` 不在 PATH），所以从头到尾**没有真正渲染过这个页面**——
> 页面观感只有你的判断有效。真实模型那一路的轨迹证据在 `docs/evidence/agent_model_e2e.txt`。

## 7. 接口文档

`docs/API.md` §4.13 有完整请求/响应字段表（含 `trace[]` 每个字段的含义）；`docs/openapi.json`（18 条路由）可直接导入 Swagger/Postman。
