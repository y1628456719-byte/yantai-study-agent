# 赛道三对齐 · Agent 化改造方案

> 触发：2026-10-16 22:20 用户反馈「点开是个网页，Agent 到底在哪」
> 现状：后端与集成已全部实现（4 个 commit，12 端点 + 前端 2160 行 + 验收全绿）
> 结论：**网页是对的，不用改。要改的是「驱动方式」。**

---

## 0. 先纠正一个误会：网页不是偏离

赛道三原文是「**Agent 驱动**的**真实场景**应用」。它约束两件事：

| 约束 | 含义 | 现状 |
|---|---|---|
| **驱动** | 内核必须是 Agent（自主决策 + 工具调用 + 记忆 + 循环） | ⚠️ **不成立**，见 §1 |
| **真实场景** | 解决真实需求 | ✅ 成立（考研择校备考） |

**它不约束外壳。** ChatGPT / Claude / Cursor / Manus 全是网页。
网页是 Agent 的**标准载体**；反过来做成 CLI，路演时评委连画面都看不清。

→ **「为什么是网页」这个问题不需要改。** 要改的是下面这个。

---

## 1. 实测诊断：现在不是「Agent 驱动」

### 证据 1 · 演示全程主语是「用户」，不是「Agent」

`docs/DEMO_SCRIPT.md` 的六分钟时间轴，每一段操作都是：

```
POST /api/v1/sessions
POST /api/v1/sessions/{id}/recommendations
POST /api/v1/sessions/{id}/lab/start
POST /api/v1/sessions/{id}/lab/answer
POST /api/v1/sessions/{id}/plan/revise
```

**全是用户手点接口。** 没有任何一处是 Agent 自主决定"下一步该做什么"。

### 证据 2 · `backend/agent.py` 存在但未接入

- `backend/agent.py`（77 行，ReAct 循环）**仍引用旧的 `tools.py` / `prompts.py`**
- 业务实际走 `backend/api/*` + `backend/services/*` = **固定流水线**
- 即：Agent 循环的代码躺在仓库里，**没有一条链路走它**

### 证据 3 · PPT 叙事主动放弃了 Agent

`DEMO_SCRIPT.md` 第 4 节 P2 页原文：

> ~~「模型只写文案，分档/排序/维度取舍由硬规则与数据决定」~~

这句话**工程上完全正确**，但在赛道三现场，它等于告诉评委：
**「这产品的核心判断不靠 Agent」** —— 评委听完只会问一句"那 Agent 驱动在哪"。

### 结论

现在的产品是「**一个做得很好的择校决策系统**」，
但它的驱动是 **用户驱动 + 硬规则驱动**，**不是 Agent 驱动**。

**这不是代码 bug，是叙事与能力的位置摆错了。** 而且改起来不用推翻任何东西 —— 现有 12 个端点，正好就是 Agent 该有的工具集。

---

## 2. 核心洞察：三层分工，不要把「硬规则」和「Agent」对立起来

现方案把两者当成了对立面（"我们不用模型判断"）。**这是错的。** 正解是三层分工：

| 层 | 谁负责 | 决定什么 | 为什么这么分 |
|---|---|---|---|
| **① 决策编排层** | **Agent** ★ | 下一步做什么：调哪个工具、要不要追问、要不要重新推荐 | **这是赛道三要考的「Agent 驱动」，现在完全缺失** |
| **② 计算判定层** | 硬规则 | 冲稳保分档、排序、维度取舍 | 必须可复现、可审计，不能让模型自由发挥 |
| **③ 表述层** | 模型 | 理由文本 | 只负责把已定的事实说清楚，禁止新增数字 |

**关键**：Agent 决定「**做什么**」（路径），硬规则决定「**算什么**」（结果），模型决定「**怎么说**」（措辞）。
三件事**不冲突**。

> 一句话答辩词：**"路径由 Agent 自主，结果由规则保证，措辞由模型生成 —— 三者各司其职。"**

现在缺的只是第 ① 层。

---

## 3. 改造方案

### P0-1 · 给后端加一个 Agent 入口（工具 = 现有 12 个端点）

**做法**：复活 `backend/agent.py`，把 `backend/tools.py` 重写为**包装现有 12 个端点**的工具注册表。

```
Agent 的工具集（就是现有能力，只换调用方）
├── create_session(profile)             → POST   /sessions
├── update_profile(patch)               → PATCH  /sessions/{id}/profile
├── get_session()                       → GET    /sessions/{id}
├── recommend()                         → POST   /sessions/{id}/recommendations
├── select_school(school_id, note)      → PUT    /sessions/{id}/target
├── list_materials()                    → GET    /sessions/{id}
├── add_material(title, text)           → POST   /sessions/{id}/materials
├── delete_material(material_id)        → DELETE /sessions/{id}/materials/{mid}
├── lab_start(material_id, topic)       → POST   /sessions/{id}/lab/start
├── lab_answer(question_id, answer)     → POST   /sessions/{id}/lab/answer
├── revise_plan(daily_minutes)          → POST   /sessions/{id}/plan/revise
└── list_sources()                      → GET    /meta/sources
```

**关键设计约束：不改任何现有接口。** Agent 只是这些接口的**另一个调用方**。

| | 表单入口（已有） | Agent 入口（新增） |
|---|---|---|
| 交互 | 点按钮、填表单 | 自然语言 |
| 谁决定下一步 | 前端写死的五步 | **Agent 自主** |
| 演示稳定性 | 高 | 中（有 mock 兜底） |
| 用途 | 主流程演示 | **证明「Agent 驱动」** |

**同一套后端、同一份数据、同一个会话** —— 两个入口共用 `session_id`。

**新增 1 个端点**：
```
POST /api/v1/sessions/{id}/agent
请求：{"message": "我是通信工程大三，想考长春或哈尔滨的学校，但数学一般"}
响应：
{
  "reply": "...",
  "trace": [
    {"step": 1, "thought": "档案里没有 exam_year 和 preferred_regions",
     "tool": "create_session", "args": {...}, "result_summary": "session 已建，revision=1"},
    {"step": 2, "thought": "可以出推荐了", "tool": "recommend", "result_summary": "3 所候选 + 1 所淘汰"}
  ],
  "state_patch": {"revision": 2}
}
```

**验收**：
1. 用户说「我想考长春或哈尔滨的学校」→ Agent **自主**调用 `create_session` + `recommend`，**不要求用户填表单**
2. 档案缺 `preferred_regions` 时 → Agent **主动追问**，而不是报错
3. 单轮对话内，12 个工具中至少被**自主**调用 3 个；完整演示流程覆盖 ≥6 个
4. 模型不可用时返回 `MODEL_UNAVAILABLE`，**不静默降级**（沿用现有约定）

---

### P0-2 · 把 Agent 的决策轨迹显示出来（"能看见的 Agent"）

**做法**：前端在 Agent 入口旁加一个「决策轨迹」面板，把 `trace[]` 逐步渲染。

演示时屏幕上是这样的：

```
① 读档案 → 发现缺 preferred_regions 和 exam_year
② 反问用户 → 得到「2027 年 / 长春、哈尔滨」
③ 调 create_session → session 已建
④ 调 recommend → 3 所候选，1 所进淘汰名单（复试线高于目标区间）
⑤ 发现 jlu 初试科目 806 与档案无交集 → 建议先做一轮小测
⑥ 用户做完小测 → 重新 recommend → jlu 风险等级上调一档
```

**为什么这条最值钱**：它是**唯一能当场看见的 Agent 行为**。
前五步是"用户点按钮"时看不见的（后端跑完直接吐结果），第六步的"风险等级变了"也只能靠嘴解释。
有了轨迹面板，**"Agent 自己决定"这件事变成了屏幕上的事实**。

**验收**：录屏中能看见 `trace` 逐步增长，每一步有 `tool` 名 + `args` + `result_summary`。

---

### P0-3 · 修正答辩叙事（零工时，收益最大）

**改 `docs/DEMO_SCRIPT.md` 第 4 节 P2 页**，把：

> ~~「模型只写文案，分档/排序/维度取舍由硬规则与数据决定」~~

改为：

> **「三层分工：Agent 负责决策路径（调什么工具、要不要追问、要不要重推）；
> 硬规则负责计算判定（分档、排序），保证可复现可审计；
> 模型负责表述，禁止新增数字。**
> 例：同一份档案，Agent 自主判断『该先做小测再推荐』，于是现场看到风险等级被上调。」

**改开场定位**（0:00–0:20），把主语从"工具"改成"它"：

> "它不是一个等你填表的工具。
> 你告诉它你的情况，**它自己决定下一步该问什么、该查什么、什么时候该建议你测一下**。
> 而且每一条判断都能点回原始来源。"

**改 P1 页**的定位词：`可审计的择校决策 Agent` → **`自主决策 + 可审计的择校 Agent`**

---

### P1-4 · `phase_detector` 交给 Agent 决策

现在 `backend/services/phase_detector.py`（7.9 KB）是**硬编码规则表**。
改为 `generate_json` + schema 输出 `next_question`，**规则表保留为模型失败时的 fallback**。

**验收**：同一份 `known_facts`，模型能给出合理追问；模型失败时**降级不报错**（走规则表）。

---

### P2-5 · 实时抓取 = 工具调用演示（可选，时间不够就砍）

把创新点 5 从「管道可扩展」升级为「**Agent 会自己找数据**」：
给一个 URL → Agent 自主决定抓哪个页面、怎么抽、存哪。
**工时约 3 h，只在 P0 全部做完且有余量时做。**

---

## 4. 明确不做什么（划清边界）

| 不做 | 原因 |
|---|---|
| **不改前端形态** | 网页是 Agent 的标准载体，改成别的反而降分 |
| **不删硬规则** | 分档必须可复现、可审计，这是现有最大优点 |
| **不让模型算分** | 禁止新增数字，这条红线保持 |
| **不把自由对话直接暴露给用户** | 会跑偏、不可控。Agent 入口必须是**任务导向**，不是闲聊机器人 |

---

## 5. 工作量与优先级（T-17h）

| 项 | 工时 | 优先级 | 卡点 |
|---|---|---|---|
| **P0-3 改文档叙事** | 20 min | 🔴 **最优先**（零风险、零依赖） | 无 |
| **P0-1 Agent 入口 + tools 重写** | 3–4 h | 🔴 高 | 需模型 key；无 key 时用 mock 走通 |
| **P0-2 轨迹面板** | 2 h | 🔴 高 | 依赖 P0-1 的 `trace[]` |
| P1-4 phase 决策交模型 | 1 h | 🟠 中 | — |
| P2-5 实时抓取当工具调用 | 3 h | ⚪ 低 | 时间不允许直接砍 |

**最小可交付组合 = P0-3 + P0-1 + P0-2**（约 6 h）
做完这个，答辩时能指着屏幕说：「**这是 Agent 自己决定调的工具，这是它自己决定追问的问题，这是它自己决定重新推荐的时刻。**」

---

## 6. 一句话总结

> **网页是对的。缺的是让 Agent 成为演示里的主语。**
> 把现有 12 个端点变成 Agent 的工具集，把它调用工具的过程显示出来，
> 再改掉 PPT 里那句"模型只写文案"。
>
> 代码不用推翻，**能力全都在，只是需要一个 Agent 站在前面调用它们。**

---

## 7. 实现验证（2026-10-16 22:55 复核）

> 结论：**P0-1 / P0-2 / P1-4 均已实现，实现质量高于本方案要求。**
> 本节记录复核方式与证据，**答辩时可原样引用**。

### 7.1 实现位置

| 项 | 文件 | 规模 | 判定 |
|---|---|---|---|
| 决策编排层 | `backend/agent.py` | **503 行**（原 77 行） | ✅ 真 ReAct 多步循环 |
| Agent 入口 | `backend/api/agent.py` | 38 行 | ✅ 走进程内 ASGI，复用同一套服务层 |
| 工具集 | `backend/tools.py` | 363 行 | ✅ 包装 12 个端点 |
| 回归测试 | `tests/test_agent.py` | — | ✅ |
| 自检脚本 | `scripts/agent_model_check.py` | — | ✅ |

**架构正确性**：`api/agent.py` 的 docstring 写明 ——

> 「工具调用走进程内 ASGI（`backend/tools.py`），因此校验、错误码、持久化、revision
> 全部复用同一套服务层逻辑，**Agent 不拥有第二份实现**。」

这正是 P0-1 要求的「Agent 只是这些接口的**另一个调用方**」。
**没有为了做 Agent 而复制一套业务逻辑** —— 这一点很多团队会做错。

### 7.2 决定性证据：真实模型四轮轨迹

文件：`docs/evidence/agent_model_e2e.txt`
环境：`base_url=https://aiping.cn/api/v1   model=DeepSeek-V4.1-Flash   mock=False`

| 轮 | 用户说 | Agent 自主决策 | 工具链 |
|---|---|---|---|
| 1 | 我是通信工程大三，想考长春或哈尔滨的学校，但数学一直没底 | 自主建档，并判断"数学没底留到下一步用小测处理" | `create_session` |
| 2 | 那就先测一下我数学到底什么水平 | 判断"档案 0 份资料 → 走无资料模式启动自测" | `lab_start` |
| 3 | B | **自己想到了中间步骤**："状态里没有 question_id，先读一次会话取回来再提交，避免用错题目 id" | `get_session` → `lab_answer`（**steps=2**） |
| 4 | 每天只能学一个小时，帮我安排一下 | `revise_plan` 报 409 → **模型自己改口换路**：改用 `update_profile`，再追问考研年份 | `update_profile` |

每轮均标：`generated_by=model` · `is_demo=False` · `model_used=True` · `terminated=reply`

**第 3 轮是全案最值钱的一条证据**：`get_session` 这个中间步骤**规则表里根本没有**，
是模型自己为了拿 `question_id` 想出来的 → 证明这是**多步规划**，不是单步响应。

**第 4 轮是第二条**：工具报错后自己换一条路 —— 这是 Agent 的核心特征（**错误恢复 + 重新规划**）。

> 答辩时可直接说：**"注意第 3 轮 —— 它自己决定先查一下题目 id 再提交，这一步没人教它。"**

### 7.3 一个原方案担心的坑，已被处理

§3 P0-1 隐含一个风险：**离线模式（`MOCK_LLM=1`）是一套 17 条规则的 if-else 链**
（`_offline_decide`，242 行）。若演示时用它，**"Agent 自主"就会变成"规则自主"**。

实现方**已经处理**：

- 顶部有明确 chip：`真实模型决策` / `规则兜底（未接模型）`
- 响应字段 `generated_by` = `model` / `rules(offline)`，`is_demo` 如实标注
- `DEMO_SCRIPT.md` 写明：现场断网切离线时**照常调用同一套工具、同样的轨迹结构**，
  并**预先跑出真实模型的证据文件**（`docs/evidence/agent_model_e2e.txt`）

**答辩时的正确说法**：

> "现场为了不受网络影响跑的是离线兜底；但这是**真实模型**跑出来的轨迹证据（打开文件），
> 每一轮都标着 `generated_by=model`。离线那份是**同一套工具、同样的轨迹结构**，
> 只是决策源换成了规则 —— 而且我们会明确标出来，不会假装。"

### 7.4 剩余待办

| 项 | 状态 |
|---|---|
| P0-3 答辩叙事 —— `DEMO_SCRIPT.md` 已改（chip + 证据文件 + 问答预案）；**PPT 是否同步需确认** | 🟠 待确认 |
| 演示策略：**录屏用真实模型，现场用离线兜底 + 出示证据文件** | 🟠 待定 |
| P2-5 实时抓取当工具调用演示 | ⚪ 可砍 |

### 7.5 演示层面的一条提醒

左侧仍保留「建档 → 推荐 → 资料 → 诊断 → 计划」五步骤条（表单入口），**这是对的**（双入口设计）。

但 **演示时必须从"一句话开始"的 Agent 入口走，不要从步骤条走** ——
步骤条是"流程驱动"的视觉隐喻，会让评委的第一印象先落在"这不还是五步流程吗"。

---

## 7. 实施状态（本方案落地记录）

> 本节是事后补的记录，上面的诊断原文保持不动（便于对照「当初判断 → 实际做法」）。

### 已完成

| 项 | 状态 | 落点 | 证据 |
|---|---|---|---|
| **P0-1 Agent 入口** | ✅ 已实现 | `backend/api/agent.py`（`POST /api/v1/agent`）、`backend/agent.py`（决策循环）、`backend/tools.py`（工具层，13 个工具） | `scripts/agent_model_check.py` 真实网关 4 轮 `exit=0`；`tests/test_agent.py` 12 条；离线探针 9 场景全通 |
| **P0-2 决策轨迹** | ✅ 已实现 | `trace[]`（`{step,thought,action,tool,args,result_summary,ok,error,code,latency_ms}`）+ 前端「★ 一句话开始」面板（`frontend/index.html` / `app.js` / `style.css`） | `scripts/frontend_check.py` 38/38；起服务后 `/`、`/app.js`、`/style.css` 200 |
| **P0-3 叙事修正** | ✅ 已做 | 本节 + `docs/DEMO_SCRIPT.md` 开场与数据可信段 + `README.md` | 文档 diff |
| **P1-4 追问交模型** | ✅ 已实现 | `backend/services/modelsvc.py:build_next_question`、`backend/services/onboarding.py:recompute(ask_model=True)`、`phase_detector` 新增 `next_question_key` | `scripts/ask_model_check.py` 真实网关 3 种档案 `exit=0`（`docs/evidence/ask_model_e2e.txt`）；`tests/test_onboarding_ask.py` 5 条 |

### 与方案原文的差异（诚实清单）

- **工具数量 13 个，不是 12 个**：把 12 个契约端点拆成模型可用的动作粒度（例如 `update_profile` 与 `create_session` 分开），另外补了 `list_sources`（数据来源，答辩时问「哪来的」用得上）。
- **`self assessment` 路径的参数名**：方案里写 `lab_start(topic=, mode='self_assessment')`，实际 schema 里 `material_id` 可以为 `null`，与 SPEC N34 一致。
- **模型返回的枚举字段一律不采信**：`revise_plan` 的 `action`（保留/砍掉）由后端规则裁定，模型只写理由——否则会出现「模型说保留了政治，但计划里根本没有政治」。
- **轨迹里的工具报错是有意保留的**：Agent 读到 409/400 之后会改口（例如没定院校就改计划 → 先反问用户），这比"假装没发生"更可审计。自检脚本因此只禁止**重复调用同一个失败工具**，不禁止单次失败。
- **前端默认落在第 1 步建档**：Agent 面板在左侧导航的「★ 一句话开始」，第 1 步顶部也有入口按钮。默认不改用户熟悉的流程，避免演示时手忙脚乱。
- **P1-4 的做法是「问什么由规则定，怎么问由模型说」**：`phase_detector` 仍是纯规则函数（阶段与 `next_question_key` 必须可复现，验收 §14.7 依赖它），只有**追问那句话**交给模型润色；规则原话始终保留在 `onboarding.rule_next_question`，模型失败/返回空则退回原话并把来源标成 `rules(degraded)`（追问失败绝不打断建档）。只有 `POST /sessions` 与 `PATCH /profile` 会花这次模型调用——加资料、选院校这些场合保持纯规则，不为一句追问多付延迟。

### 未做（如实说明，答辩可主动讲）

- **P2-5 未做**：没有把爬虫做成实时工具（`crawler/` 仍是离线管线，数据落地为 `data/schools/*.json`）。做一半的实时抓取在路演现场风险太高。
- **不是多智能体**：本项目是「单 Agent + 多工具」，赛道 02 的「多 Agent 协作」我们没有声称——所答赛道是 **03 Agent 驱动的真实场景应用**。
- **决策层没有向量检索/长期记忆**：`tools.py` 里没有 embedding、没有向量库；跨会话记忆只有 SQLite 里的会话快照（`session_store`）。"记忆"是**显式的会话状态**，不是向量召回。

---


> **网页是对的。缺的是让 Agent 成为演示里的主语。**
> 把现有 12 个端点变成 Agent 的工具集，把它调用工具的过程显示出来，
> 再改掉 PPT 里那句"模型只写文案"。
>
> 代码不用推翻，**能力全都在，只是需要一个 Agent 站在前面调用它们。**
