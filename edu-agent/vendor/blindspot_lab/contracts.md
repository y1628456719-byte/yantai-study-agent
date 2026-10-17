# 内部模块契约 lab.v1

可选 `lab.v2` 自适应扩展与本地验收页见 [ADAPTIVE.md](ADAPTIVE.md)。本文件主体为保留的v1契约，尚未替代团队公共HTTP协议；旧状态保留，新会话显式选择v2。

这是实验室内部契约，尚未获队友公共API确认。模块不定义HTTP路由、不连接数据库、不修改前端或其他团队文件。成功返回字典，失败统一抛出`LabError`。

## 输入

入口：`Lab.start_lab(context)`、`Lab.submit_answer(context, question_id, answer)`。两者签名与总方案一致；模型在构造`Lab`时注入，不放进JSON。

| 字段 | 要求 |
| --- | --- |
| `schema_version` | 固定`lab.v1` |
| `session_id`、`request_id` | 非空字符串，最多200字符；同一逻辑请求重试复用request_id |
| `revision` | 主后端本次读取的会话版本，非负整数 |
| `profile` | 可缺失或null；如提供daily_minutes则为1至1440的整数 |
| `target` | 已选school_id，另有subject或topic；首版subject仅支持`逻辑` |
| `material` | material_id、title、text、source_url、is_demo；text最多12000字符，不截断 |
| `topic` | 固定为随包知识点；其他内容返回范围错误 |
| `learning_state` | 首次null或缺失；后续为服务端保存的完整快照 |
| `runtime_mode` | 明确为`mock`、`live`或`replay`；进行中的轮次禁止切换 |

可从`blindspot_lab.lab.content.demo_context()`取得完整首次输入。运行证据中的`examples/outputs/revised.json`包含`initial_context`及四次成功返回；主后端每次将保存的learning_state和最新revision放回context。

首版绑定`demo_logic_v1`与完全一致的材料正文。自行更换名称、资料或主题不会获得任意资料诊断能力；材料ID、正文、演示标记或范围不满足时拒绝执行。

## 成功返回

| 字段 | 用途 |
| --- | --- |
| `schema_version`、`session_id` | 契约与会话匹配 |
| `base_revision` | 主后端原子保存时比较的版本 |
| `next_revision` | 有变更时为base_revision+1，恢复时等于base_revision |
| `state_changed` | 是否需要保存新的模块状态并递增会话版本 |
| `lab_state` | 当前可公开展示的卡片 |
| `learning_state` | 完整服务端内部快照；不要让浏览器提交任意版本覆盖 |
| `events` | 本次真实计算事件，包含event_id、type、status、summary、runtime_mode |

内部快照保存run_id、材料ID及正文版本摘要、题库摘要、目标范围、阶段展示状态、所有作答、选题序列、假设历史、最近请求ID及完整性摘要。摘要使用`content.digest()`的规范JSON序列化后SHA-256，属于损坏检测，**不是签名或客户端可信凭证**。

内部revision记录上次模块结果的会话版本，允许主后端其他动作令共享revision继续增加。模块能拒绝低于快照的revision；完全自洽的旧页面只能由主后端原子版本检查拒绝，不能把此责任放给无存储模块。

## 前端字段

| lab_state字段 | 展示约定 |
| --- | --- |
| `stage`、`substage` | diagnosis/initial → diagnosis/distinguish → verification → summary；讲解与验证题同屏 |
| `question` | question_id、stem、options；options统一为id/text对象；完成后null |
| `feedback` | 上次answer_id、is_correct、简短文字；开始时null |
| `hypotheses` | id、label、status、reason、evidence_ids；状态为tentative/supported/revised/inconclusive |
| `judgment_change` | before、after、trigger_answer_id；从作答记录定位为何变化 |
| `evidence` | 材料或作答证据；引用由ID查找 |
| `explanation` | text、evidence_ids；至少引用一段当前材料 |
| `summary` | verification_correct、result、uncertainty、evidence_ids |
| `next_action` | answer或complete |
| `is_demo`、`runtime_mode` | 分别显示“演示材料”和“模拟/实时/回放”；live标签不代替真实运行验收 |

区分题作答前，evidence只含作答证据（开始时为空），公开反馈和假设原因由程序限制为不含定义的短文本。第二次提交后才公开材料证据与讲解。旧版已提前展示定义的未完轮次须保留历史并显式重启。

材料证据含material_id、原文quote及locator.start/end/line。start包含、end不包含，是Python Unicode字符索引，**不是JavaScript UTF-16索引**；本份样本在BMP范围内，但扩展材料应通过后端转换定位。作答证据含answer_id、question_id、answer、is_correct。

标准答案由服务端题库维护；模型在用户作答后取得对应题干、选项、已选文字及判分依据。未来题只给模型ID与role，不给题干或答案。公开题卡不返回correct_option_id、rationale或error_tags。主后端只把lab_state交前端；内部完整快照不能视为公共接口。

## 错误与重试

```python
try:
    result = lab.submit_answer(context, question_id, answer)
except LabError as exc:
    body = exc.to_dict()
    # 主后端把body映射到自己唯一的HTTP错误封套；不要保存候选或递增revision。
```

| 错误码 | 含义与处理 |
| --- | --- |
| `INVALID_INPUT` | 字段、快照、答案或执行模式无效；修正请求 |
| `MATERIAL_UNSUPPORTED` | 资料/主题超出首版范围；不替换成演示材料 |
| `MATERIAL_CHANGED` | 正文或审核题库变化；restart_required=true，保留旧轮次 |
| `SCOPE_CHANGED` | 主题或已选目标变化；restart_required=true |
| `QUESTION_MISMATCH` | 提交的不是当前题；回读当前状态 |
| `ANSWER_ALREADY_SUBMITTED` | 新请求再次提交已作答题；不推进 |
| `STALE_REVISION` | 版本冲突；回读后由用户决定新请求 |
| `REQUEST_CONFLICT` | 一个request_id携带不同操作或参数 |
| `MODEL_UNAVAILABLE` | 缺适配器、超时或服务失败；保留状态后可合理重试 |
| `MODEL_OUTPUT_INVALID` | 输出结构、候选状态或题目不允许；一次修复仍失败 |
| `EVIDENCE_INVALID` | 引用、定位或关键作答依据无效；拒绝保存 |

模型输出只允许feedback、hypotheses、explanation、next_question_id；额外工具指令、任意新题和模型提供的标准答案都不会执行。模型正常返回长度最多12000字符，字段另有长度限制。传输错误不做结构修复，结构或引用错误最多修复一次。

幂等缓存由主后端维护，建议键为(session_id, request_id)，同时比较原操作、版本与参数摘要。相同请求直接返回以前保存的结果，不重新调用模块或模型；不同参数复用ID须报冲突。并发计算可以发生，但只能一个原子保存获胜；重复模型成本应由真实服务与主后端按需进一步约束。

## 恢复、重启和时间修改

刷新直接回读保存的lab_state；调用start_lab时也只返回已有轮次，events为空且state_changed=false。模块不能验证真实数据库、进程重启后的数据保留或网页渲染；参考存储只用JSON序列化和新对象检验契约。

显式重启由主后端创建动作：先保留旧learning_state与轮次历史，再用learning_state=null调用start_lab，得到新run_id，最后原子保存。材料仍须受支持。不要通过清空状态悄悄丢弃旧诊断。

`build_learning_plan(learning_state, daily_minutes)`只返回daily_minutes、tasks、deferred与total_minutes。每项任务含task、minutes、reason、evidence_ids、shortened，正分钟且总和不超预算。未分配任务明确延后。主后端修改每日时间时仅更新profile和共享revision，保留已选目标、材料与作答。

