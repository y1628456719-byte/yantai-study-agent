# 主后端 API 契约（给前端 / 队友 B）

> 版本：SPEC v1.1 · 后端实现已通过 SPEC §14 十条验收（`scripts/acceptance.py`，10/10）
> 机器可读版本：[`docs/openapi.json`](openapi.json)（可直接导入 Swagger / Postman / Apifox）

前端**只调这个后端**：不直连模型、不读 `data/`、不碰 `.env`（密钥只在服务端）。
本文里的所有请求/响应都是**真实跑出来的**（`data/acceptance_examples.json` 是原始记录）。

---

## 1. 通用约定

| 项目 | 约定 |
| --- | --- |
| 前缀 | `/api/v1`（健康检查与版本号不在前缀里：`/api/health`、`/api/version`） |
| 编码 | 全部 `application/json`（资料用 JSON 文本字段传，不用 multipart） |
| 响应外壳 | **恒定四键**：`{"ok": bool, "data": object\|null, "error": object\|null, "request_id": "12 位 hex"}` |
| 失败 | `ok=false`、`data=null`、`error={"code","message","retryable"?}`，并配相应的 HTTP 状态码；**绝不把失败包装成空成功** |
| 错误文案 | `error.message` 是**可直接展示给用户的中文**，不含堆栈（N25） |
| 排障 | 每个响应都带 `request_id`，服务端日志同值，报问题时把它给我 |
| 会话版本 | 每次写操作成功都会让快照 `revision` +1；前端可用它做"数据是否变了"的判断 |
| 幂等 | `POST /recommendations`（同 revision 下结果一致）、`DELETE /materials/{id}`（重复删除仍返回成功） |

错误码表（HTTP 状态码 → `error.code`）：

| HTTP | code | 什么时候出现 | 前端建议 |
| --- | --- | --- | --- |
| 404 | `SESSION_NOT_FOUND` | 会话不存在/已清理 | 回到首页重新建档 |
| 404 | `MATERIAL_NOT_FOUND` | 删除一个本来就不存在的资料 | 不用处理：响应仍是 `ok=true`（幂等） |
| 400 | `INVALID_ARGUMENT` | 参数不合法（未知院校、空资料、question_id 不匹配…） | 直接展示 `message` |
| 409 | `TARGET_NOT_SELECTED` | 还没确认目标院校就调 `plan/revise` | 引导先做「确认院校」 |
| 503 | `LAB_UNAVAILABLE` | 实验室适配器不可用（`LAB_ADAPTER=teammate` 但模块没接上） | 提示稍后重试，不要静默降级 |
| 503 | `MODEL_UNAVAILABLE` | 配了密钥但模型网关不通/输出不合法；`retryable=true` | 显示"模型暂时不可用"+重试按钮 |
| 409 | `CONFLICT` | 会话版本冲突（并发写） | 重新拉一次会话再操作 |

---

## 2. 端点总览

| 方法 | 路径 | 用途 | 关键规范点 |
| --- | --- | --- | --- |
| POST | `/api/v1/agent` | **一句话入口：Agent 自主决策**（见 4.13） | 返回 `trace[]` 决策轨迹；工具集就是下面这些端点 |
| POST | `/api/v1/sessions` | 建档（可只给 `exam_year`） | N3 `missing_profile_fields`、N29 `onboarding` |
| GET | `/api/v1/sessions/{sid}` | 拉完整会话快照（**前端主数据源**） | §7.12 十大字段 |
| PATCH | `/api/v1/sessions/{sid}/profile` | 改档案任意子集 | 传 `null` 表示清空该字段；返回新快照 |
| POST | `/api/v1/sessions/{sid}/recommendations` | 生成/刷新院校推荐 | N2/N4/N5/N6/N7/N8/N27/N32；**可重复调用** |
| PUT | `/api/v1/sessions/{sid}/target` | 确认目标院校 | N9 `user_note`、N28 写 `decisions` |
| PUT | `/api/v1/sessions/{sid}/shortlist` | 缩小候选校范围 | N10（SPEC 只定义了字段，这里补一个端点） |
| POST | `/api/v1/sessions/{sid}/materials` | 录入资料（JSON 文本） | N11 段落 `locator`、N12 `school_id/subject`、N13 `preview` |
| GET | `/api/v1/sessions/{sid}/materials` | 列出当前未删除资料 | 便利端点（快照里也有） |
| DELETE | `/api/v1/sessions/{sid}/materials/{mid}` | 删除资料 | 被引用过则**软删除**、幂等 |
| POST | `/api/v1/sessions/{sid}/lab/start` | 开始/继续诊断 | N34 `material_id=null` 走无资料路径 |
| POST | `/api/v1/sessions/{sid}/lab/answer` | 作答 | 返回统一 `lab_state` |
| POST | `/api/v1/sessions/{sid}/lab/action` | 实验室的其它动作（新增·非破坏性） | 仅在 `LAB_ADAPTER=blindspot` 时有额外 action（见 4.12） |
| POST | `/api/v1/sessions/{sid}/plan/revise` | 改每日时间、重排计划 | N21 `changes`、N22 理由指向诊断结论；**不清空任何历史** |
| GET | `/api/v1/meta/sources` | 数据来源与覆盖率 | 答辩页用 |
| GET | `/api/v1/meta/subjects` | 标准科目表 | 创新点 1 的中间件 |
| GET | `/api/health` | 探活 + 当前配置 | `schools/lab_adapter/mock_llm/model_key_configured` |
| GET | `/api/version` | 契约版本 | `{"spec":"v1.1","api_prefix":"/api/v1"}` |

---

## 3. 会话快照（前端的主数据源）

`POST /sessions`、`GET /sessions/{sid}`、`PATCH /profile`、`PUT /target`、`PUT /shortlist` 都返回**同一个结构**：

```json
{
  "ok": true,
  "data": {
    "session_id": "s_514dc6cc6432",
    "revision": 6,
    "profile": { "exam_year": 2027, "major": "信息与通信工程", "daily_minutes": 120,
                 "preferred_regions": ["长春", "哈尔滨", "沈阳"], "study_status": "边工作边考",
                 "target_score": null, "is_cross_major": false },
    "onboarding": {
      "phase": "P3", "phase_name": "备考执行期", "phase_confidence": "inferred",
      "next_question": null, "missing_critical": [], "missing_profile_fields": [],
      "next_question_source": "rules", "next_question_key": null,
      "rule_next_question": "条件都齐了，要不要现在就生成一份院校推荐？",
      "reasons": ["方向已明确、已有候选院校…"],
      "known_facts": { "exam_year_known": true, "has_direction": true,
                       "has_candidate_schools": true, "has_materials": true,
                       "study_status": "边工作边考" }
    },
    "selected_school_id": "neu",
    "shortlist": [],
    "materials": [ { "material_id": "m_93a6cf284962", "title": "信号与系统复习提纲",
                     "character_count": 264, "is_demo": true,
                     "school_id": null, "subject": null } ],
    "learning_state": { "lab": { "topic": "ANALOG_CIRCUIT", "stage": "verification",
                                 "current_question_id": "ac_gain_2", "round": 1 },
                        "ability_profile": [ { "topic": "ANALOG_CIRCUIT", "status": "weak",
                                               "evidence_count": 2, "basis": "measured" } ],
                        "self_assessment": [], "notes": [] },
    "plan": { "daily_minutes": 30, "items": [ { "task": "…", "minutes": 15, "reason": "…" } ],
              "changes": [ { "action": "dropped", "task": "…", "reason": "…" } ], "dropped": 5 },
    "recommendations": { "… 最近一次推荐结果（结构见 §5）…" },
    "decisions": [ { "time": "2026-10-16T22:12:03", "choice": "选择 东北大学 作为目标院校",
                     "reason": "想留在东北，离家近", "alternatives": ["jlu", "heu"] } ],
    "is_demo": false,
    "created_at": 1792157394.51, "updated_at": 1792157394.52
  },
  "error": null,
  "request_id": "f75658596045"
}
```

前端渲染要点：

* `onboarding.phase_confidence == "inferred"` 时，文案要写"**我们推断**你现在处于…"，`"confirmed"` 才写"你之前说过…"（N29）。
* `learning_state.ability_profile[]` 里 `basis` 只有 `measured` 才算实测结论；`self_assessment[]` 的自评**不能**用来宣传"能力画像"（§10.4）。
* `materials[].is_demo`、`lab_state.is_demo`、快照 `is_demo` 都要在界面上**显式标注"演示数据"**（N26）。

---

## 4. 逐端点

### 4.1 `POST /api/v1/sessions` 建档

请求（字段全可选，但演示时至少要给 `exam_year`）：

```json
{"profile": {"exam_year": 2027, "major": "信息与通信工程", "background": "本科通信工程，在职备考，每天 2 小时",
             "preferred_regions": ["长春", "哈尔滨", "沈阳"], "daily_minutes": 120,
             "study_status": "边工作边考", "is_cross_major": false,
             "undergrad_major": "通信工程", "target_major": "信息与通信工程",
             "target_score": 330}}
```

`profile` 允许的键：`exam_year`、`major`、`background`、`preferred_regions[]`、`daily_minutes`、`preferences`、`undergrad_school`、`undergrad_tier`、`gpa_band`、`english_level`、`target_score`、`study_status`、`is_cross_major`、`undergrad_major`、`target_major`、`phase`、`phase_confidence`、`known_facts`。

* 返回：会话快照（§3）。
* N3：`onboarding.missing_profile_fields` 列出**还缺的关键项**（如 `exam_year`）。
* N29：`onboarding.phase`（P0–P4）由后端按已有信息推断，`next_question` 给出"下一个该问什么"；**不要**自己写死"大三"这类假设。
* **P1-4 追问**：`onboarding.next_question_key` 是规则选定的"这一轮必须问的那一项"（SPEC §5.4 的降级顺序，可复现）；
  `onboarding.next_question` 是**给用户看的说法**——建档与改档案时由模型润色，`next_question_source` 取值
  `model` / `rules(offline)`（离线演示）/ `rules(no_key)` / `rules(degraded)`（模型失败已退回规则）。
  `onboarding.rule_next_question` **永远保留规则原话**，`next_question_error` 只在降级时出现。
  前端渲染建议：直接用 `next_question`，不要自己拼文案；演示"AI 在追问"时可以把两者并排显示。
  注意：`POST /sessions` 与 `PATCH /profile` 因此可能多花一次模型调用（无密钥/离线时不会）。

### 4.2 `PATCH /api/v1/sessions/{sid}/profile` 改档案

请求体是 `profile` 的任意子集；`daily_minutes`、`target_score`、`study_status` 等随时可改。
**注意**：传 `null` = 显式清空该字段（不是"没传"）；只传要改的键即可。返回更新后的完整快照。

### 4.3 `POST /api/v1/sessions/{sid}/recommendations` 推荐

请求体固定为 `{}`（用户的偏好都在会话里），响应：

```json
{ "ok": true, "data": {
  "recommendations": [
    { "school_id": "neu", "school_name": "东北大学",
      "metrics": {
        "score_line_total": {"value": 315, "year": 2026, "source_index": 5},
        "score_line_single": {"value": 45, "year": 2026, "source_index": 5},
        "admission_scale": {"value": 41, "year": 2027, "source_index": 12},
        "exam_subjects": ["101 思想政治理论", "201 英语（一）", "301 数学（一）", "841 通信专业基础"],
        "exam_subject_codes": ["POLITICS", "ENG1", "MATH1", "COMM_BASIC_841"],
        "reference_books_count": {"value": 0, "year": 2027, "source_index": 12},
        "duration_years": {"value": 3, "year": 2027, "source_index": 12},
        "degree_type": {"value": "全日制学术学位", "year": 2027, "source_index": 12},
        "tuition": {"value": 24000, "year": 2027, "source_index": 12},
        "city": "沈阳",
        "preference_match": {"value": true, "detail": "沈阳 在你选择的地域（长春、哈尔滨、沈阳）", "kind": "judgement"},
        "tier": "冲",
        "line_vs_national": null, "admission_ratio": null, "recommend_ratio": null
      },
      "fit_reasons": [ {"text": "2026 年复试线 315 分（工学[08]）", "kind": "fact", "source_index": 5},
                       {"text": "单科线 满分 100 的科目 45 / 满分 >100 的科目 75", "kind": "fact", "source_index": 5} ],
      "risks": [ {"text": "招生规模 12 人偏小，分数波动风险大", "kind": "fact", "source_index": 12} ],
      "unknowns": ["复试线−国家线：缺国家线数据源"],
      "sources": [ {"index": 5, "title": "东北大学2026年硕士研究生复试基本分数线",
                    "url": "https://yz.neu.edu.cn/2026/0313/c5932a451030/page.htm",
                    "fetched_at": "2026-10-16T20:41:00+08:00", "applicable_year": 2026} ],
      "tier_note": "复试线 315 高于你的目标 330−10 分区间，按硬规则记为「冲」" }
  ],
  "excluded": [ {"school_id": "xxx", "school_name": "某某大学",
                 "reason": "该校明确「不招收同等学力考生」，与你的报考身份冲突", "source_index": 3} ],
  "dimension_hits": ["score_line_total", "admission_scale", "exam_subjects"],
  "dimension_missing": ["score_line_single", "admission_ratio"],
  "profile_echo": { "exam_year": 2027, "major": "信息与通信工程", "preferred_regions": ["长春", "哈尔滨", "沈阳"],
                    "target_score": 330, "understood_as": "2027 年报考…（模型用一句话复述你的条件）" },
  "tier_rule": "规则：复试线 − 目标分 330（差值 >10 为冲，10~−10 为稳，<−10 为保）",
  "ability_flags": [ { "topic": "ANALOG_CIRCUIT", "topic_name": "模拟电子技术",
                       "status": "weak", "evidence_count": 2, "basis": "measured",
                       "direction": "hit", "affected_schools": ["jlu"] } ],
  "ability_profile_echo": [ { "topic": "ANALOG_CIRCUIT", "status": "weak", "evidence_count": 2, "basis": "measured" } ],
  "model_texts": { "understood_as": "…", "ranked": [ {"school_id": "neu", "verdict": "冲刺", "text": "…"} ],
                   "generated_by": "model", "model_error": null },
  "model_used": true,
  "model": null,
  "is_demo": false
}}
```

前端硬性要求（SPEC N4/N5/N6/N8/N27/N32）：

1. `fit_reasons` / `risks` / `unknowns` / `sources` 是**四个独立字段**，必须分开渲染（不要把风险混进"推荐理由"）。
2. 理由/风险条目的 `kind`：`fact` 带 `source_index`（可点回原文）；`judgement` **没有** `source_index`，界面上要标注"我们的判断"，与事实分开。
3. `metrics.tier`（冲/稳/保）由**硬规则**算出，模型无权修改；`tier_rule` 是可展示的算式；`tier_note` 说明本次为什么这么分档。三校里没有复试线的那所，`tier` 会是 `null` 且理由进 `unknowns`——**这是如实呈现，不要在前端补默认值**。
4. `excluded` 是"我们淘汰了谁、为什么"（答辩亮点，**必须展示**）。
5. `dimension_hits` / `dimension_missing` 用来显示"本次推荐用上了 N 个维度，其中 M 个数据缺失"（N32）。
6. `ability_flags` 是"你的薄弱项和这所学校考的东西撞上了，风险上调"（创新点 1）；只有 `basis=measured` 的证据才会产生它。
7. `model_used=false` 表示本次用**规则文案**（离线演示模式 / 无密钥），界面要如实标注，不要说成"模型生成"。

### 4.4 `PUT /api/v1/sessions/{sid}/target` 确认院校

```json
{"school_id": "neu", "user_note": "想留在东北，离家近"}
```

* 返回会话快照。
* N9：`user_note` 会存进会话，并作为后续学习计划的依据之一。
* N28：每次确认/修改都会往 `decisions[]` 追加一条（`choice` / `reason` / `alternatives`）。
* 未知 `school_id` → 400 `INVALID_ARGUMENT`。

### 4.5 `PUT /api/v1/sessions/{sid}/shortlist` 候选范围

```json
{"school_ids": ["jlu", "neu"]}
```

返回快照。传空数组 `[]` = 恢复全量候选。已确认的目标院校不会被它清掉。

### 4.6 `POST /api/v1/sessions/{sid}/materials` 录入资料

```json
{"title": "模电笔记：共射放大电路", "text": "…正文（建议按段落换行）…",
 "source_url": "https://example.com/note", "is_demo": true,
 "school_id": "jlu", "subject": "ANALOG_CIRCUIT"}
```

响应：

```json
{"material_id": "m_93a6cf284962", "title": "模电笔记：共射放大电路", "character_count": 264,
 "is_demo": true, "school_id": "jlu", "subject": "ANALOG_CIRCUIT",
 "preview": "前 200 字…", "paragraph_count": 4}
```

* N11：后端按段落切分并保存 `locator`（"第 3 段"），实验室给证据时要能定位回原文。
* N12：`subject` 用**标准科目表的 `subject_code`**（前端可从 `GET /meta/subjects` 拿下拉项）；`school_id` 可选。
* N13：`preview` 给前端做"录入确认"卡片，不用再拉全文。
* 空 `text` → 400 `INVALID_ARGUMENT`。

### 4.7 `DELETE /api/v1/sessions/{sid}/materials/{mid}` 删除资料

```json
{"deleted": true, "material_id": "m_93a6cf284962", "soft": true,
 "note": "该资料已被诊断引用，已做软删除：列表里不再显示，但历史证据仍可溯源"}
```

* 幂等：删一个不存在的 id 也返回 `ok=true`（`soft=false` + 说明）。
* 被 `learning_state` 的证据引用过的资料**软删除**（保留 `material_id` 快照，证据链不断）。

### 4.8 `POST /api/v1/sessions/{sid}/lab/start` 开始诊断

```json
{"material_id": "m_93a6cf284962", "topic": "ANALOG_CIRCUIT", "mode": "material"}
```

* N34（创新点 5）：无资料也能诊断 → `{"material_id": null, "topic": "MATH_FOUNDATION", "mode": "self_assessment"}`。
* `mode` 取值 `material` / `self_assessment`；`material_id` 为 `null` 时后端自动走 `self_assessment`。
* `topic` 用标准科目 `subject_code`；不传则由后端按目标院校的考试科目挑一个。
* 响应 `data` **就是** `lab_state`（结构见 §5），不是 `{lab_state, learning_state}`；`learning_state` 从 `GET /sessions/{sid}` 取。

### 4.9 `POST /api/v1/sessions/{sid}/lab/answer` 作答

```json
{"question_id": "ac_qpoint_1", "answer": "A"}
```

* `answer` 传**选项的 `key`**（`options[].key`），不是整段文案。
* `question_id` 必须等于上一次 `lab_state.question.question_id`，否则 400 `INVALID_ARGUMENT`（提示里带当前应作答的 id，前端据此刷新）。
* 响应同样是 `lab_state`。

### 4.10 `POST /api/v1/sessions/{sid}/plan/revise` 改时间、重排计划

```json
{"daily_minutes": 45}
```

```json
{"previous_daily_minutes": 120, "daily_minutes": 45,
 "changes": [ {"action": "dropped", "task": "初试科目：思想政治理论",
               "reason": "每日时间从 120 分钟降到 45 分钟，按优先级暂缓本项（诊断结论与作答记录均保留，时间恢复后会自动重新排入）"} ],
 "plan": [ {"task": "薄弱专项：模拟电子技术", "minutes": 20, "reason": "2 条实测证据指向该知识点"} ],
 "generated_by": "model", "model_error": null, "revision": 9}
```

* N21：`changes` 说明"**砍了什么、为什么**"（`action` ∈ `dropped`/`kept`）。
* N22：`plan[].reason` 必须指向具体结论（薄弱知识点 / 目标院校初试科目）。
* **不清空**：改时间不会丢已选院校、资料、作答与能力画像（§14.5 验收项）。
* 没确认目标院校 → 409 `TARGET_NOT_SELECTED`。

### 4.11 元数据

`GET /api/v1/meta/sources`（答辩用）：

```json
{"crawl_time": "2026-10-16T22:20:00+08:00",
 "coverage": [ {"school_id": "jlu", "school_name": "吉林大学",
                "categories": ["admission_guide", "program_catalog_pdf", "score_line"],
                "latest_year": 2027, "category_years": {"score_line": 2026}, "record_count": 7} ],
 "total_records": 51,
 "levels": {"official": 51, "official_summary": 0, "inferred": 0},
 "school_ids": ["heu", "jlu", "neu"], "notes": ["…"]}
```

`GET /api/v1/meta/subjects`：`{"version": "v1-draft-2026-10-16b", "categories": {...}, "subjects": [{subject_code, name, aliases, prerequisites, composed_of, category, exam_codes}]}`（30 个科目）。

---

### 4.12 `POST /api/v1/sessions/{sid}/lab/action` 实验室其它动作（新增·非破坏性）

只在 `LAB_ADAPTER=blindspot`（队友 C 的自适应升级包 v2）时才有额外动作；`mock`/`teammate` 会对未知
`action` 返回 400，行为与接入前完全一致。

```json
{"action": "feedback", "answer": "A"}
{"action": "continue"}
{"action": "time", "extra_minutes": 5}
{"action": "stop", "reason": "user_finished"}
```

| action | 参数 | 用途 |
| --- | --- | --- |
| `feedback` | `answer` | 只看批改反馈、暂不推进（对应 lab.v2 的 `feedback`） |
| `continue` | — | 在预算内继续追问（`CONTINUE_UNAVAILABLE` → 503 中文提示） |
| `time` | `extra_minutes` | 追加时间预算（`TIME_BUDGET_EXHAUSTED` 之后恢复） |
| `stop` | `reason` | 主动结束本轮，`lab_state.stage="stopped"` |

同样返回 `lab_state`（`data` 就是它本身）。`LAB_ADAPTER=blindspot` 时 `lab_state` 会额外带
`adaptation` / `cycle` / `history` / `summary` / `judgment_change` / `resume_token`；这些字段在
`mock`/`teammate` 下不存在，前端做可选渲染即可。

---

### 4.13 `POST /api/v1/agent` Agent 自主决策入口（P0-1）

**一句话交给它，它自己决定调哪些工具。** 这是「Agent 原生应用」的入口；下面 4.1–4.12 的所有端点
同时被它当作工具使用（工具名与端点对应，参数经后端校验后走同样的服务层代码）。

请求（`session_id` 与 `history` 都可省略）：

```json
{"message": "我是通信工程大三，想考长春或哈尔滨的学校，但数学一直没底",
 "session_id": null,
 "history": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}]}
```

响应 `data`：

```json
{"session_id": "s_eb7e8dc5ad67",
 "reply": "先问一个最关键的：你想考哪个专业方向？",
 "trace": [
   {"step": 1, "thought": "用户是跨考且在犹豫，先把档案建起来再谈推荐",
    "action": "tool", "tool": "create_session",
    "args": {"profile": {"exam_year": 2027, "major": "信息与通信工程"}},
    "result_summary": "已建档 s_eb7e8dc5ad67（阶段 P1 方向探索期）",
    "ok": true, "error": null, "code": null, "latency_ms": 12}],
 "steps": 1,
 "tools_used": ["create_session"],
 "state_patch": {"session_id": "s_eb7e8dc5ad67", "profile": {"phase": "P1"}},
 "artifacts": {},
 "terminated": null,
 "model_used": true,
 "generated_by": "model",
 "is_demo": false}
```

| 字段 | 含义 | 前端怎么用 |
| --- | --- | --- |
| `reply` | Agent 给用户的自然语言回答（可直接展示） | 聊天气泡 |
| `trace[]` | **决策轨迹**：每步的 `thought`（为什么）→ `action`/`tool`/`args`（做什么）→ `result_summary` 或 `error`+`code`（结果） | 「决策轨迹」面板；把 `thought` 单独一行显示，这是"能看见的 Agent" |
| `ok:false` 的轨迹 | 工具报错（如没定院校就改计划 → `TARGET_NOT_SELECTED`） | **正常展示，不要隐藏**：Agent 会据此改口，这是可审计性 |
| `generated_by` | `model` / `rules(offline)` / `rules(no_key)` | 离线时显式标注「规则兜底（未接模型）」，不假装是模型 |
| `terminated` | 提前结束的原因（步数上限 `max_steps`、模型不可用等），正常为 `null` | 有值时给一条提示 |
| `model_used` / `is_demo` | 是否真实模型 / 是否为演示数据 | N26 标注 |

约定：
- `steps ≤ MAX_AGENT_STEPS`（默认 8），到上限用 `terminated:"max_steps"` 如实返回，不假装做完；
- 模型不可用时**不静默降级成假成功**：无密钥走 `rules(offline)`（离线演示模式，工具照常调用、结果真实），
  **配了密钥但调用失败**则返回 503 `MODEL_UNAVAILABLE`（`retryable:true`）；
- 同一个 `session_id` 多轮对话时，前端把上一轮的 `{role,content}` 放进 `history` 即可（后端也会读会话快照，二者不冲突）；
- 轨迹里 `args` 只放后端工具层接受的参数，**不包含密钥/系统提示词**。

---

## 5. `lab_state` 契约（N14–N20）

```json
{ "stage": "diagnosis | explanation | verification | summary",
  "question": { "question_id": "ac_qpoint_1", "stem": "…", "options": [{"key": "A", "text": "…"}],
                "topic": "ANALOG_CIRCUIT", "subject_code": "ANALOG_CIRCUIT" },
  "feedback": "错在哪（N17 第一层）",
  "hypotheses": [ {"label": "模拟电子技术掌握情况", "subject_code": "ANALOG_CIRCUIT",
                   "status": "tentative | supported | revised", "reason": "…"} ],
  "evidence": [ {"kind": "material | answer", "material_id": "m_…", "quote": "原文片段",
                 "locator": "第 3 段", "basis": "measured | self_reported"} ],
  "explanation": "正确思路（N17 第二层）",
  "next_action": "answer | complete",
  "is_demo": true }
```

前端要遵守：

* `next_action == "answer"` 才渲染作答区，回答后把 `question_id` 原样回传。
* 证据条数就是"证据"：**禁止**用百分比/置信度表达（N15）。
* `basis=self_reported` 的条目要加"自评"标记，且**不能**用来做"排除院校"的理由（§10.4）。
* 假设从 `tentative` → 有实测证据才变 `supported`/`revised`（N16/N20）：界面上"待验证/已验证/已修正"三态要分得清。
* `is_demo=true` 时整块要打"演示模式"标记（N26）。

---

## 6. 离线演示模式 vs 真实模型

| 场景 | 表现 | 前端怎么显示 |
| --- | --- | --- |
| `MOCK_LLM=1`（默认，无密钥也能跑） | `model_used=false`、`model_texts.generated_by="rules(offline)"`、`lab_state.is_demo=true` | "离线演示模式：结论由硬规则生成" |
| 有密钥、网关正常 | `model_used=true`、`generated_by="model"` | 正常展示；理由里的数字仍来自数据，不是模型编的 |
| 有密钥但网关挂了 | HTTP 503 `MODEL_UNAVAILABLE`（`retryable=true`），中文提示 | 显示可重试的错误，**不要**自动切回离线假装成功 |
| 有密钥但模型输出不符合结构 | 同上 503（后端会带错误信息重试 1 次，仍失败就报错） | 同上 |

模型只写**文案**（`understood_as`、每校一句 `text`、`plan.changes` 的理由），不参与维度取舍与分档排序（§7 硬性要求）。后端把结构契约（JSON 骨架）随请求一起发给模型，见 `backend/llm.py: schema_contract()`。

---

## 7. 本地起服务

```powershell
# 1) 依赖（必须用清华镜像，直连 pypi 会卡住）
.venvs\edu-agent\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 起后端（离线演示模式，不需要密钥）
$env:MOCK_LLM=1; .venvs\edu-agent\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000

# 3) 想联真实模型：复制 .env.example 为 .env 并填 key，然后把 MOCK_LLM 设为 0
```

* 交互式文档：`http://127.0.0.1:8000/docs`
* 前端联调：后端已开 CORS（允许所有来源）；`frontend/` 目录存在时，`/` 会挂静态首页，`/static` 是静态资源前缀。

---

## 8. 一次完整会话的调用顺序（照抄即可）

1. `POST /sessions`（档案）→ 拿 `session_id`，看 `onboarding.next_question` 决定还问什么
2. `PATCH /sessions/{sid}/profile`（补信息，可多次）
3. `POST /sessions/{sid}/recommendations` → 展示三校对比 + `excluded` + `dimension_hits/missing`
4. `POST /sessions/{sid}/materials`（录入资料，可多次）
5. `POST /sessions/{sid}/lab/start` → `lab/answer`（循环到 `next_action=complete`）
6. `POST /sessions/{sid}/recommendations`（**第二次**：能力画像反哺，看 `ability_flags` 与风险上调）
7. `PUT /sessions/{sid}/target`（确认院校）→ `PUT /shortlist`（可选）
8. `POST /sessions/{sid}/plan/revise`（改时间 → 看 `changes`，确认历史没被清空）
9. `GET /sessions/{sid}` 随时拉快照；`GET /meta/sources`、`GET /meta/subjects` 给"数据来源"页
