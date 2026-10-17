# 给队友 C 的接口说明（盲点实验室接入）

> 目的：你写**诊断实验室**，我们（主后端）把它当"可替换模块"接进来。
> 你不需要配任何密钥、不要写路由、不要碰 `backend/api/`；你只交付**两个同步函数**。
> 契约来自 SPEC §10.1 / §10.2 / §10.4 / §10.5 / §10.6 与 §7.11（N14–N20、N34）。

> **更新（已接入）**：你后来交付的《盲点实验室-自适应升级接入包 v2》我们**已经接进主后端**了 ——
> `vendor/blindspot_lab/`（原样 vendored，未改你内部实现）+ `backend/lab_adapter/blindspot.py`
> 做形状翻译，用 `LAB_ADAPTER=blindspot` 启用，自检 `python scripts/lab_blindspot_check.py` **18/18**。
> 因此下面的"两个同步函数"仍然有效（首版接口），但如果你继续迭代，**请只改 v2 包**：
> 我们只依赖 `AdaptiveLab` / `AdaptiveHost.execute(lab, action, request_id, expected_revision, **params)`
> 与 `lab_state` 的 v2 键（`adaptation`/`cycle`/`history`/`summary`/`judgment_change`/`resume_token`），
> 这些键在我们这边是**透传**的，不会丢。新增 action 通过新增的
> `POST /api/v1/sessions/{sid}/lab/action` 暴露（`feedback`/`continue`/`time`/`stop`）。

---

## 1. 你要交付什么

一个模块（默认路径 `lab/teammate_impl.py`，仓库根目录下新建 `lab/` 包即可）：

```python
# lab/teammate_impl.py
def start_lab(context: dict) -> dict: ...
def submit_answer(context: dict, question_id: str, answer: str) -> dict: ...
```

* **同步函数**（不是 `async def`）：主后端用 `asyncio.to_thread` 调你，超时 `LAB_TIMEOUT`（默认 120 秒）。
* 返回值**恒定**是：

```python
{"lab_state": {...}, "learning_state": {...}}   # learning_state 可以是 {} 或 None
```

* 想换模块名/路径：设环境变量 `LAB_TEAMMATE_MODULE=你的模块名`（默认 `lab.teammate_impl`）。
* 开我们的适配器：`.env` 里 `LAB_ADAPTER=teammate`。
* 参考实现：`backend/lab_adapter/mock.py`（离线 mock 走的就是同一套结构，两条路径都实现了）；骨架：`lab/example_impl.py`（可直接 `python scripts/lab_teammate_check.py lab.example_impl` 跑通 50 项自检）。
* 自测：`python scripts/lab_teammate_check.py`（会直接调你的模块，逐字段校验并打印问题）。

---

## 2. 你会收到什么：`context`

主后端组装好、直接传给你（`backend/lab_adapter/base.py: build_context`）：

```json
{
  "session_id": "s_514dc6cc6432",
  "profile": { "exam_year": 2027, "major": "信息与通信工程", "study_status": "边工作边考",
               "daily_minutes": 120, "preferred_regions": ["长春", "哈尔滨", "沈阳"],
               "is_cross_major": false },
  "selected_school_id": "jlu",
  "selected_school": { "school_id": "jlu", "school": "吉林大学", "catalogs": [ … ],
                       "records": [ … ], "meta": { "city": "长春", "province": "吉林" } },
  "material": { "material_id": "m_93a6cf284962", "title": "模电笔记：共射放大电路",
                "text": "……全文……", "subject": "ANALOG_CIRCUIT", "school_id": "jlu",
                "paragraphs": [ { "seq": 1, "locator": "第 1 段", "text": "……" } ] },
  "learning_state": { "lab": { "topic": "ANALOG_CIRCUIT", "stage": "verification",
                               "current_question_id": "ac_gain_2", "round": 1 },
                      "ability_profile": [ { "topic": "ANALOG_CIRCUIT", "status": "weak",
                                             "evidence_count": 1, "basis": "measured" } ] },
  "subject_table": { "version": "v1-draft-2026-10-16b",
                     "subjects": [ { "subject_code": "ANALOG_CIRCUIT", "name": "模拟电子技术",
                                     "aliases": ["模电"], "prerequisites": ["CIRCUIT_ANALYSIS"],
                                     "composed_of": [], "category": "major_base",
                                     "exam_codes": ["806"] } ] },
  "diagnosis_mode": "material",
  "topic": "ANALOG_CIRCUIT"
}
```

字段说明：

| 键 | 说明 |
| --- | --- |
| `material` | 有资料路径（`diagnosis_mode="material"`）时非空；**无资料路径（N34）时为 `null`**，你只见得到 `topic` |
| `material.paragraphs[]` | 已经切好的段落，`locator` 就是"第 N 段"——引用原文时**必须**用它 |
| `learning_state` | 上一轮状态原样给你：你说的 `lab` 小节（topic/stage/current_question_id/round）+ 你的 `ability_profile` + 自评记录。**这是你的记忆**，多轮连续性靠它 |
| `subject_table` | 标准科目表（N31）。你的 `hypotheses[].subject_code` **必须**取自这里，否则后端会把它置 `null`（无法与院校考试科目对齐，创新点 1 就失效） |
| `selected_school` | 已确认的目标院校原始数据（records/catalogs），用来决定"该问什么" |
| `diagnosis_mode` | `material`（资料驱动）或 `self_assessment`（量表+小测驱动） |
| `topic` | 本轮指定科目；为 `null` 时你自己按 `selected_school` 的考试科目挑一个 |

---

## 3. 你必须返回什么：`lab_state`（§7.11）

```json
{
  "stage": "diagnosis",
  "question": { "question_id": "ac_qpoint_1", "stem": "……题干……",
                "options": [ {"key": "A", "text": "……"}, {"key": "B", "text": "……"} ],
                "topic": "ANALOG_CIRCUIT", "subject_code": "ANALOG_CIRCUIT" },
  "feedback": "错在哪：把静态工作点当成了小信号参数，属于「概念混淆」",
  "hypotheses": [ { "label": "模拟电子技术掌握情况", "subject_code": "ANALOG_CIRCUIT",
                    "status": "tentative", "reason": "资料显示你对 Q 点计算不熟" } ],
  "evidence": [ { "kind": "material", "material_id": "m_93a6cf284962",
                  "quote": "……资料原文片段……", "locator": "第 3 段" } ],
  "explanation": "正确思路：先算 Q 点，再看交流通路……",
  "next_action": "answer",
  "is_demo": false
}
```

### 逐字段要求

| 字段 | 规则 |
| --- | --- |
| `stage` | 只允许 `diagnosis` / `explanation` / `verification` / `summary`，其他值后端直接报错（500 → 503） |
| `question` | `next_action="answer"` 时**必填**且要有 `question_id`；一套题结束（`next_action="complete"`）时可为 `null` |
| `question.options[]` | 每题至少 2 个选项，写 `{"key","text"}`；前端回传的是 **`key`** |
| `feedback` | 第一层：**错在哪**（N17），要具体到错误类型：概念混淆 / 计算失误 / 审题偏差（N18） |
| `explanation` | 第二层：**正确思路**（N17）。不要把它和 `feedback` 写成一坨 |
| `hypotheses[]` | 你的判断。`label` 必填；`status` ∈ `tentative`/`supported`/`revised`（N16：只有 1 条以上证据才允许离开 `tentative`；出现反向证据必须改 `revised`）。**禁止置信度百分比**（N15）——要表达"多确定"就用证据条数 |
| `evidence[]` | `kind` ∈ `material`（引用资料，必须给 `quote` + `locator`，N14）/ `answer`（引用作答，必须给 `question_id`）。可以再带 `basis` |
| `next_action` | `answer`（还要答题）/ `complete`（本轮诊断结束） |
| `is_demo` | 真实实现恒为 `false`（我们的 adapter 会强制写 `false`） |
| `learning_state` | **自由结构**，后端原样存下来、原样还给你，不解释你的内容。只要放回你下一轮需要的东西就行（比如 `lab` 小节的 stage/current_question_id、`ability_profile`） |

### 两条路径的硬规则（N34 / §10.4）

* **路径 A（有资料，`material` 非空）**：结论要有依据 → `evidence[].kind="material"`，且 `quote`/`locator` 必须能在资料里定位到。证据的 `basis` 记为 `measured`。
* **路径 B（无资料，`material=null`）**：先出**自评量表**题（`question_id` 形如 `self_<TOPIC>`，选项是 1–5 分），自评结论只能 `basis="self_reported"`；再出**实测小问题**，实测部分 `basis="measured"`。
  * 自评结果**不得**作为"排除某所学校"的依据，只能作为"了解你"的线索（后端会把它放进 `unknowns`）。
  * `evidence_count` 必须真实：**数出来的**证据条数，不许凑数。
* 路径 B 的 `learning_state.ability_profile[]` 里，`basis` 必须逐条标 `measured` 或 `self_reported`——前端要分开显示"实测"和"自评"。

---

## 4. 失败怎么处理（§10.5）

* 直接 `raise` 就好。我们会捕获异常 → HTTP **503 `LAB_UNAVAILABLE`**，并把你的异常文本放进 `error.message` 展示给用户。
  → **请把异常信息写成中文、面向用户**（例如 `raise RuntimeError("诊断模块暂时不可用，请稍后重试")`），别把堆栈抛出来。
* **不允许**在失败时静默返回 mock 结果：答辩时"看起来能用、其实是假数据"是致命的。宁可 503。
* 超时（`LAB_TIMEOUT`，默认 120 秒）我们也当失败处理。所以：单次调用别做长任务；批量解析/训练请提前离线算好。

## 5. 如果需要模型能力

SPEC §11：**实验室不单独配密钥**。要用模型就调我们实现好的入口：

```python
from backend.llm import generate_json

schema = { "type": "object", "required": ["verdict", "why"],
           "properties": {"verdict": {"type": "string"}, "why": {"type": "string"}} }
out = generate_json([{"role": "system", "content": "你是考研诊断助教…"},
                     {"role": "user", "content": "学生这样答：…"}], schema)
# out == {"verdict": "...", "why": "..."}；失败会抛 backend.llm.LLMError
```

它会自动带超时、结构校验、失败重试，并在没有密钥时走离线 mock（你的模块被调时不会进 mock 分支：`MOCK_LLM=1` 时后端整体走 mock adapter，轮不到你）。
**你的返回值仍然要经过 §3 的结构校验**，模型说什么不算数——假设状态、证据、`next_action` 由你决定。

---

## 6. 我们这边的验收方式

你交付后我跑：

1. `scripts/lab_teammate_check.py`：字段级校验（stage/options/hypotheses.subject_code/evidence/next_action，并打印整棵树）。
2. `LAB_ADAPTER=teammate` 下走完整链路：`lab/start` → `lab/answer`（连续多轮）→ `GET /sessions/{sid}` 确认 `learning_state` 连续、`ability_profile` 出现 `basis=measured` 的条目。
3. 故障演练：模块不在时必须是 503 `LAB_UNAVAILABLE`、中文提示，不能变成 200。
4. 演示链路：模电资料 → 诊断（答错）→ `verification` 追问 → 结论 `revised` → 二次推荐时该科目出现在 `ability_flags`。

时间上：接口先按这个冻结版本做，**字段如需增删请先跟我说**——前端（队友 B）已经按 §3 的结构在渲染了。
