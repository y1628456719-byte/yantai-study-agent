# 演示脚本与讲稿要点（6 分钟展示 + 2 分钟问答）

> 对象：路演现场（评委 6 分钟展示 + 2 分钟问答）。本文档同时给写 PPT 的队友提供**技术要点**与**演示顺序清单**。
> 所有数字都是本仓库真实跑出来的（`docs/evidence/real_model_e2e.txt`、`data/acceptance_report.json`），不是设想值。

---

## 0. 一句话定位（开场 20 秒）

> "它不是一个等你填表的工具。
> 你告诉它你的情况，**它自己决定下一步该问什么、该查什么、什么时候该建议你测一下**：
> 每一步都显示在屏幕上的**决策轨迹**里——调了哪个工具、为什么调、拿到了什么。
> 而它给出的每一条判断都能点回原始来源。
> **路径由 Agent 自主，结果由规则保证，措辞由模型生成。**"

三个可验证的差异点（评委最容易记住的三句）：

1. **Agent 驱动（可当场看见）**：自然语言进去，Agent 自主决定调哪个工具、要不要反问、
   要不要先做一轮小测再重新推荐；前端逐步渲染 `trace[]`（thought → tool → args → result_summary）。
2. **判断可审计**：理由分 `fact`（有官方来源 `source_index`）/ `judgement`（模型推断，标明无来源），
   自评 `self_reported` 与实测 `measured` 永不混用。
3. **否定性判断 + 决策→执行闭环**：不只给推荐名单，还给**淘汰名单**（`excluded[]`）——"推荐名单证明我们会找，淘汰名单证明我们会筛"；
   改每日时间不会清空院校/资料/作答记录，`changes[]` 逐条说明"砍了什么、为什么"。

---

## 1. 六分钟时间轴

| 时间 | 段落 | 屏幕 | 要说的一句话 |
| --- | --- | --- | --- |
| 0:00–0:20 | 开场定位 | PPT P1 | 上面那句"可审计的择校决策 Agent" |
| 0:20–1:20 | **Agent 自主开场**：一句话进去，决策轨迹出来 | 前端「★ 一句话开始」 | "我只说了一句话，**建档是它自己决定做的**——这是它当时的理由" |
| 1:20–2:30 | 主流程：推荐 → 排除名单 | 前端第 2 步 | "同一份档案，它给出推荐**和**淘汰，并告诉我 33 个维度里哪几个拿到了数据" |
| 2:30–3:50 | 实验室闭环（两条路径） | 前端第 4 步 | "资料里没有这个知识点时，它**不会拿无关段落当证据**" |
| 3:50–4:40 | 能力画像反哺择校（创新点 1） | 前端第 2 步 | "做错两道题之后，同一所学校的风险等级被上调了" |
| 4:40–5:30 | 改时间：计划重排但记录不清空 | 前端第 5 步 | "砍掉的每一项都写明原因，而且作答记录一条没丢" |
| 5:30–6:00 | 数据可信 + 诚实不足 | `/meta/sources` | "抓了 51 条官方记录；拿不到的我们写在 unknowns 里" |

**演示前 3 分钟检查清单**（照做即可）：

```powershell
# 1) 起服务（离线演示模式最稳；现场网络不可控）
cd D:\dsh environment\dsh yield\edu-agent
$env:MOCK_LLM = "1"
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" -m uvicorn backend.main:app --port 8000
# 2) 浏览器打开 http://127.0.0.1:8000/         ← 演示台（左侧 ★ 一句话开始 + 五步界面）
#    打开 http://127.0.0.1:8000/api/health      ← 应看到 schools=["heu","jlu","neu"]、mock_llm=true
#    没有前端/要手动点接口时用 http://127.0.0.1:8000/docs
# 3) 想换成队友 C 的真实实验室（录屏或答辩备用）：
#    $env:LAB_ADAPTER = "blindspot"; $env:LAB_BLINDSPOT_MODE = "mock"   # 随包样本，零凭据
#    live 模式需 LAB_BLINDSPOT_API_KEY 且网络可用（现场网络不可控，默认不要开）
```

**录屏前建议先跑一遍自检**（30 秒内出结果，全绿再开录）：

```powershell
$env:MOCK_LLM = "1"
$py = "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe"
& $py scripts/acceptance.py            # SPEC §14 十条 → 10/10
& $py scripts/lab_blindspot_check.py   # 队友实验室接入 → 18/18
& $py scripts/lab_teammate_check.py lab.example_impl   # 实验室接口契约 → 50/50
& $py scripts/frontend_check.py        # 前端静态一致性（含 Agent 面板元素） → 38/38
& $py -m pytest tests -q               # 回归 → 36 passed
& $py scripts/smoke_e2e.py             # 全链路 18 步（含两次推荐对比与错误路径）
# 有密钥时再跑一次真实模型（离线自检之后、录屏之前）：
& $py scripts/agent_model_check.py     # Agent 自主决策 4 轮 → exit=0
& $py scripts/real_model_check.py      # 文案由真实模型生成 → exit=0
& $py scripts/ask_model_check.py       # 追问由模型说（规则原话保留） → exit=0
```

---

## 2. 逐段操作（可直接照读的"演示剧本"）

### 第 0 段 · 一句话交给 Agent（0:20–1:20）· **建议作为开场第一个动作**

前端「★ 一句话开始」面板，或 `POST /api/v1/agent`。**先不要手动建档**——这一段的看点就是"建档这个动作是它自己决定做的"。

```json
{"message": "我是通信工程大三，想考长春或哈尔滨的学校，但数学一直没底"}
```

屏幕上应出现（`data.trace[]`，逐条渲染）：

1. `thought`：它自己的判断，例如「用户是跨考生且数学没底，先把档案建起来再谈推荐」；
2. `tool: create_session` + `args`（它自己抽出的 `exam_year/major/preferred_regions`）；
3. `result_summary`：`已建档 s_xxxxxxxxxxxx（阶段 P1 方向探索期）`；
4. 顶部 chip：`真实模型决策`（有密钥）/ `规则兜底（未接模型）`（离线）——**离线也照常调用同一套工具**；
5. `reply`：它接着反问你一句（例如"你想考哪个专业方向"）——**这句反问是它自己决定的**。

第二句话可以试：*"那就先测一下我数学到底什么水平"* → 它会自己 `lab_start`（无资料自评路径）；
再答一个字母（例如 `B`）→ 它会自己 `lab_answer` 并推进到验证/总结。

台词：*"注意看这一列'为什么'。这不是我在点按钮，是它在决定下一步做什么；
如果它判断错了——比如还没定院校就想改计划——它会拿到 409，然后回来问我，而不是硬编一个答案。"*

> 现场网络不稳时：先 `$env:MOCK_LLM="1"`，这一段照样跑（`generated_by="rules(offline)"`，
> 同一套工具、同样的轨迹结构），录屏前用 `scripts/agent_model_check.py` 出过真实模型的证据文件即可。

### 第 0.5 段 · 顺带演示"追问是它说的"（可选 15 秒，接在第 1 段建档处）

第 1 步建档页留空年份、专业，点「保存」看 `onboarding`：

- `next_question_key`（规则定的那一项，例如 `exam_year_known`）——**问什么由规则决定**；
- `next_question`（模型润色的说法，例如"你准备考哪一届研究生？"）——**怎么问由模型说**；
- `rule_next_question`（规则原话"你打算参加哪一年的考研？"）——始终保留，可并排显示。

台词：*"顺序不能交给模型——同一个档案必须问同一件事，否则验收和复现都无从谈起；
但怎么问得像个人，交给它。模型挂了就退回左边这句话，来源会标成 `rules(degraded)`。"*
证据：`scripts/ask_model_check.py` → `docs/evidence/ask_model_e2e.txt`。

### 第 1 段 · 建档 → 第一次推荐（1:20–2:30）

操作：`POST /api/v1/sessions`

```json
{"profile":{"exam_year":2027,"major":"信息与通信工程","preferred_regions":["长春","哈尔滨","沈阳"],
 "daily_minutes":90,"background":"在职备考","english_level":"CET-6","study_status":"边工作边考"}}
```

紧接着 `POST /api/v1/sessions/{id}/recommendations`（请求体 `{}`）。屏幕上应出现：

* `profile_echo.understood_as`：**用你自己的话复述你的档案**（"2027 年报考信息与通信工程…每日可投入 90 分钟"）
  —— 这一条是为了让用户第一时间发现"它理解错了没有"。
* 三所学校（东北大学 / 吉林大学 / 哈尔滨工程大学），每所带 `fit_reasons[]`、`risks[]`、`unknowns[]`、`sources[]`。
* `dimension_hits` / `dimension_missing`：**"33 个决策维度里，本次有 10 个拿到了数据，23 个如实列为缺失"**。
* `excluded[]`：淘汰名单（本档默认档案下为空，可现场改档案演示：把 `is_cross_major=true` 且某校
  `accepts_cross_major=false`，该校就会进 `excluded` 并给出依据）。

台词：*"请注意三条：理由分成事实和判断两类；每个事实都带来源编号；缺失的维度它直接说缺，不编。"*

### 第 2 段 · 实验室闭环（2:30–3:50）

**路径 A（有资料，`basis=measured`）**：

1. `POST /sessions/{id}/materials` 贴一段《模拟电子技术》笔记（题目自带示例文本），响应里有 `preview`
   和前 200 字确认，`locator` 已按段落存好。
2. `POST /sessions/{id}/lab/start` `{"material_id":"<id>","topic":"ANALOG_CIRCUIT"}` → 返回 `lab_state`：
   `stage=diagnosis`、一道题、`hypotheses[]`、`evidence[]`（`kind=material`，带 `quote` + `locator`）。
3. 故意答错 → `stage=verification`、`hypotheses[].status=revised`、`feedback`（错在哪 + 错误归类）
   与 `explanation`（正确思路）**分两层**，然后追一道**同知识点**的题。
4. 答对 → `stage=summary`、`next_action=complete`，`learning_state.ability_profile` 出现
   `{topic:"ANALOG_CIRCUIT", status:"weak", evidence_count:2, basis:"measured"}`。

**路径 B（无资料，`material_id: null`）** —— 这是很多人不做的一条路：

1. `POST /sessions/{id}/lab/start` `{"material_id":null,"topic":"MATH_FOUNDATION","mode":"self_assessment"}`
   → 先出**自评量表题**（`self_*`）。
2. 作答后 `learning_state.self_assessment` 记录 `{score, scale:5, basis:"self_reported",
   note:"自评只作先验，不作为排除院校的依据"}`，随后**自动追加一道实测小题**取 `measured` 证据。

台词（诚实性亮点，建议原话）：*"资料里没有检索到相关表述时，它不拿无关段落充当证据——
它会说'这份资料里没有直接相关的表述，我直接出一题取实测结果'。"*

### 第 3 段 · 能力画像反哺择校（3:50–4:40，创新点 1）

操作：**同一个会话**，把上面的诊断做完（至少答错一题拿到 `measured` 证据），
再调一次 `POST /sessions/{id}/recommendations`。

预期变化（本仓库实测）：

| | 第一次推荐 | 诊断后第二次推荐 |
| --- | --- | --- |
| `ability_flags` | `[]` | `[{topic:"ANALOG_CIRCUIT", status:"weak", evidence_count:2, basis:"measured", direction:"hit", affected_schools:["jlu"]}]` |
| 吉林大学 | `risks` 1 条 | `risks` 2 条（新增"该校考试科目与你的薄弱项重合"） |
| `dimension_hits` | 6 | 7（`ability_match` 进入命中集） |

台词：*"三层分工：**Agent 负责决策路径**——它自己判断'该先做一轮小测再推荐'；
**硬规则负责计算判定**——分档（冲/稳/保）由复试线与目标分算出，可复现可审计；
**模型负责表述**——只写理由文本，禁止新增数字。
薄弱项命中会把风险档**上调一档**，并且说明依据是几条实测证据。"*

补充说明（评委可能追问）：`POST /recommendations` 允许重复调用且同一 `revision` 下结果一致（幂等），
第二次调用若已有 `ability_profile`，响应**必须**带 `ability_flags`。

### 第 4 段 · 改时间：计划重排但记录不清空（4:40–5:30）

操作：`POST /sessions/{id}/plan/revise` `{"daily_minutes":45}`（原来 90）。

响应要点：

* `previous_daily_minutes=90`、`daily_minutes=45`；
* `changes[]`：`[{action:"dropped", task:"初试科目：思想政治理论", reason:"在给定初试科目中权重最低（weight=1）…"}]`
  —— **每条都指向具体诊断结论或权重，不是"因为重要所以砍掉"**；
* 关键不变量：`selected_school_id` 不变、资料条数不变、`learning_state` 一字未改。

台词：*"它砍了政治和英语，理由写在屏幕上；但它没有清空你已经选的学校、传的资料、做过的题——
这是我们对'决策→执行闭环'的定义。"*

### 第 5 段 · 数据可信与诚实不足（5:30–6:00）

打开 `GET /api/v1/meta/sources`：三类来源级别（official / official_summary / inferred）、
逐校 `categories[]` 与 `latest_year`、总记录数。

然后**主动说不足**（这一页建议直接做成 PPT 最后一页）：

1. **哈工程没有自划复试线**（官方明确"在国家线基础上划定"，只公布专项计划线）→ 该校 `score_line_total` 为
   `null`，不硬凑分档；因此本次演示可能出现"只有冲/保两档、没有稳档"。
2. **东北大学复试线官方页面正文是图片**（正文仅 257–261 字、0 张表）→ 我们人工转录并标注
   `extraction:"image_ocr_manual"` + 来源图片 URL，**没有伪造表格**。
3. **33 个维度里有 22–23 个拿不到**（报录比、推免占比、复试差额、拟录取最低分、真题公开、学科评估…）
   → `dimension_missing` 逐条列出，且专门区分"官方未公布"与"我们不判断"。
4. **实验室当前是 mock**（`is_demo:true`，11 个科目的离线题库）；队友 C 的真实模块已按
   `docs/FOR_TEAMMATE_C.md` 的接口接入（`LAB_ADAPTER=blindspot` 一行切换，18/18 自检，失败不会静默降级），
   其 `live` 模型模式因现场网络不可控默认不开。

台词：*"评委标准里写着'请展示真实完成的功能，如实说明作品的不足'——这一页就是我们的答案。"*

可点开的两份证据文件（都在仓库里，不是临时截图）：

* `docs/evidence/agent_model_e2e.txt`：Agent 决策层的**真实模型**四轮轨迹（自主建档 → 自主开小测 → 遇到 409 改口）；
* `docs/evidence/real_model_e2e.txt`：真实模型生成的推荐理由与计划 `changes`。

---

## 3. 录屏脚本（提交物需要"演示录屏"）

建议录 **4 段、总长 ≤ 3.5 分钟**，优先用**离线演示模式**（`MOCK_LLM=1`）以免现场网络抖动：

| 段 | 时长 | 内容 | 关键画面 |
| --- | --- | --- | --- |
| 0 | 45s | **一句话交给 Agent**（不手动建档）→ 决策轨迹逐条出现 | `thought`（为什么）→ `tool: create_session` → 结果摘要；顶部「真实模型决策 / 规则兜底」chip |
| 1 | 60s | 建档 → 推荐 → 点开一条 `fit_reasons` 的来源 | 理由 → `source_index` → `sources[]` 的 URL |
| 2 | 60s | 资料 → 诊断 → 答错 → 追问 → 答对 → `ability_profile` | `feedback` 与 `explanation` 分两层；`locator` 指向资料段落 |
| 3 | 40s | 再做一次推荐（`ability_flags` 出现、风险上调）→ 改时间 → `changes[]` | 两次推荐对比 + 记录未清空 |

录屏命令（离线，一条命令跑全套并在控制台打印要点）：

```powershell
cd D:\dsh environment\dsh yield\edu-agent
$env:MOCK_LLM = "1"
# 全链路冒烟（18 步，含两次推荐对比与错误路径）
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" scripts\smoke_e2e.py
# —— 以下两条需要 .env 里的真实密钥（真实调用模型，约 7 秒/次）——
# Agent 决策层：一句话入口的真实模型 4 轮轨迹
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" scripts\agent_model_check.py
# 文案由真实模型生成的端到端（推荐理由 + 计划 changes）
& "D:\dsh environment\dsh yield\.venvs\edu-agent\Scripts\python.exe" scripts\real_model_check.py
```

`scripts/acceptance.py`（§14 十条验收，10/10）与 `scripts/lab_teammate_check.py`（实验室接口自检，50/50）
也可以作为"工程可信度"的补充画面，各 10 秒即可。

---

## 4. 给写 PPT 队友的技术要点（6 页）

| 页 | 标题 | 三个要点（可直接抄） |
| --- | --- | --- |
| P1 | 问题与定位 | 择校难在判断而非信息；我们要做**可审计的决策**而不是"AI 荐校"；赛道 03 真实场景应用 |
| P2 | 系统架构 | FastAPI + SQLite 单机可复现；16 个业务端点统一 `{ok,data,error,request_id}`；**三层分工：Agent 决定调什么工具（`trace[]` 可回放）、硬规则决定算什么（分档/排序，可复现可审计）、模型只说怎么讲（禁止新增数字）** |
| P3 | 创新点 1：能力画像反哺择校 | 诊断得到的 `ability_profile` 按 `subject_code` 与院校 `exam_subjects` 求交 → 命中薄弱项则风险上调一档；实测：`risks` 1→2、`dimension_hits` 6→7 |
| P4 | 创新点 2/3：可审计 + 否定性判断 | 每个维度 `{value, year, source_index}`；理由 `kind:fact\|judgement`；`excluded[]` 淘汰名单附依据 |
| P5 | 工程与数据 | 三所 985/211 官方数据（吉大 PDF 附件 80 页、哈工程 32 页、东北大学 19 学院 91 专业）；博达 CMS 附件、内嵌 PDF、图片分数线等真实坑；51 条记录可溯源 |
| P6 | **Agent 是怎么做事的** | 一句话进 → 模型自己在 13 个工具里选（建档/推荐/开小测/定校/改计划/查来源）；每次调用都留 `thought`+`args`+结果；拿到 409 会改口而不是硬编；模型不可用时**如实降级为规则兜底或报 503**，不假装成功 |
| P7 | 诚实不足与后续 | 分档缺哈工程自划线（官方无）；22 个维度官方未公布；实验室离线 mock 与队友真实模块（`LAB_ADAPTER=blindspot`）一行切换；前端已交付 `frontend/`（含 Agent 面板） |

---

## 5. 故障预案

| 现象 | 处理 |
| --- | --- |
| 真实模型超时/失败（现场网络） | 直接展示 `MODEL_UNAVAILABLE` 的中文提示与 `retryable:true`，然后切 `MOCK_LLM=1` 重跑——**这本身就是验收项 §14.6** |
| 端口 8000 被占 | `--port 8010`，健康检查 URL 同步改 |
| 控制台中文乱码（Windows GBK） | 所有演示走前端/`/docs`，不要用 PowerShell 打印中文；脚本输出已改为写 UTF-8 文件 |
| 只想要最稳的一条链路 | `scripts/acceptance.py`（离线，10/10，30 秒内跑完） |

---

## 6. 问答预案（评委最可能问的 7 个问题）

1. **"数据是你们抓的还是编的？"** → 现场打开 `/api/v1/meta/sources`：逐条 URL、`fetched_at`、来源级别；
   图片转录项标注 `extraction:"image_ocr_manual"` 与原图 URL；`docs/DATA_SOURCES.md` 有逐条清单。
2. **"为什么只有三所学校？"** → 三校覆盖"有自划线 / 无自划线 / 分数线为图片"三种**数据形态**，
   管道是适配器可扩展的；候选校里大连理工专业目录入口不存在、西电与长春理工 DNS 不可达（如实记录）。
3. **"模型会不会胡说？"** → 分档、排序、维度取舍都不经过模型；模型只写理由文本，且提示词禁止新增数字；
   结构校验失败会重试并在失败时返回 `MODEL_UNAVAILABLE`，**不返回半成品**。
4. **"自评能当证据吗？"** → 不能。`basis=self_reported` 与 `measured` 分字段存放，自评只进 `unknowns`，
   **不作为淘汰院校的依据**（SPEC §10.4）。
5. **"改了时间记录会不会丢？"** → 不会，现场演示：`selected_school_id`、资料数、`learning_state` 全部不变。
6. **"前端呢？"** → 队友 B 按 `docs/API.md`（含 `docs/openapi.json`，可直接导入 Postman/Swagger）对接；
   后端保证契约稳定，前端不直连模型、不持有密钥。前端已交付：`frontend/`（零依赖单页，
   `http://127.0.0.1:8000/` 直接打开，含 Agent 面板）。
7. **"这不就是套了个聊天框吗？"** → 现场指着 `trace[]`：Agent 选的是**我们自己的 16 个业务端点**
   （建档、推荐、开诊断、定院校、改计划、查来源），每次调用都有 `thought`（为什么）、`args`（传了什么）、
   `result_summary`（拿到了什么）；分档/排序/维度取舍**不经过模型**，模型只决定"下一步做什么"和"怎么说"。
   还能现场演示它的**失败改口**：没定院校就让它改计划 → 409 `TARGET_NOT_SELECTED` → 它会回来问目标院校
   （证据文件 `docs/evidence/agent_model_e2e.txt`）。
