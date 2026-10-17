# 自适应版本：运行与接入

`AdaptiveLab` 在原三题闭环后接入反馈、策略调整和新题验证。`Lab` / `lab.v1` 入口保留。首轮三题，之后每轮一次针对性讲解和一道未见验证题，最多三轮。这个版本仍只支持随包自编逻辑材料。

## 亲手运行

在解压后包含 `blindspot_lab` 文件夹的目录运行；开发机目录为 `D:\莱森`。

```powershell
python -m blindspot_lab.examples.adaptive_server --mode mock --port 8765
```

打开 <http://127.0.0.1:8765>。页面明确显示模拟模式，不消耗模型额度。

真实模型使用服务端环境变量 `AIPING_API_KEY`，或开发机已经授权的本用户加密凭据；凭据不随包交付。

```powershell
python -m blindspot_lab.examples.adaptive_server --mode live --port 8766 --model DeepSeek-V4.1-Flash
```

打开 <http://127.0.0.1:8766>。模型请求均为 `max`，不自动切换模型、加速或降级。等待期间按钮锁定；模型失败保留服务端原状态。运行方式由启动命令确定，页面不接触密钥。

本地服务只监听回环地址，只提供验收页与两个工具接口，不开放文件目录。按浏览器 Cookie 隔离会话，采用内存参考存储。刷新页面可以恢复；关闭或重启服务后不会持久保存。它不是生产后端，队友仍需接入真实数据库、登录与公共路由。

## 四项行为

1. **验证进入下一轮**：首轮选 `A / A / A` 可观察到初始判断被修正、验证失败；点击继续后，系统更换方法并选一题未出现过的题。新答案再次改变结果。答对仅支持本次迁移，不能说明长期掌握。
2. **反馈进入状态**：“没懂”采用分步拆解，“换个例子”改变表达，“太简单”提高后续题的迁移难度，“我是猜的”把最近一次答对标为仍需确认。自报不改判分，不自动消耗轮次。反馈在同一版本快照下绑定最近作答；过期页面请求由主后端拒绝。
3. **任务随证据调整**：失败与答对后的练习不同；时间缩短先保留最小验证预算。当前估算用时为首轮每次作答1分钟、后续每轮讲解1分钟和验证2分钟；不能当作真实计时。剩余不足完整新轮次时暂停，调大时间可恢复。计划不要求把全部预算填满。
4. **可检查的页面**：显示题目、讲解、结果、变化记录、反馈和材料定位。区分题完成前不展示材料定义与教学解析；题库答案留在服务端。真实模型的自由表述仍需语义核对，引用校验不能证明解释必然正确。

停止优先级：主动停止 → 待答题 → 三轮上限 → 预算不足 → 题库耗尽。预算不足可恢复；主动停止需新建会话，旧记录保留至服务关闭。题库只含六道追加自编题，不自由出题，也不跨主题继承判断。

## 后端适配

```python
from blindspot_lab import AdaptiveLab, adaptive_context
from blindspot_lab.examples.adaptive_host import AdaptiveHost

lab = AdaptiveLab()  # mock；live须显式注入GenerateJSONAdapter
host = AdaptiveHost(adaptive_context())
result = host.execute(lab, "start", "request_1", 0)
qid = result["lab_state"]["question"]["question_id"]
result = host.execute(lab, "submit", "request_2", 1,
                      question_id=qid, answer="A")
```

`AdaptiveHost` 是接入约定的参考实现，负责请求去重、原子版本比较与一次性保存，不能直接当作团队生产存储。真实模型适配方式与旧版一致。`AdaptiveLab.act(context, action, **params)` 是纯计算入口，成功才返回下一状态；不改写输入。公共 HTTP 字段仍由队友主后端确认。

| action | 参数 | 行为 |
| --- | --- | --- |
| start | 无 | 开始或恢复当前会话 |
| submit | question_id, answer | 当前题单选提交 |
| feedback | feedback | unclear / too_easy / guessed / new_example |
| time | minutes | 1至1440整数；保存新时间并重排任务 |
| continue | 无 | 仅首轮结束或后续验证结束后，显式开始新轮次 |
| stop | 无 | 保留证据并停止 |

每个请求带 `request_id`、`expected_revision`。同ID相同参数重试返回原结果；不同参数报冲突。旧版本或计算后并发冲突均不应用候选。所有动作都应由主后端在同一事务中保存状态、公开视图、任务与幂等结果。

`lab.v2` context 形状沿用 v1；`learning_state` 为 v2可信快照，包含原 `core`、后续作答、用户反馈、策略变化、预算估算与上限。完整快照仅留服务端。v1快照不能直接当v2输入，需保留历史另建会话，不做隐式迁移。

前端接收 `lab_state`、`plan`、当前版本和运行模式。新增展示字段：`adaptation`（策略、原因、证据ID）、`cycle`（轮次、可答/可继续、暂停原因、预算估算）、`history`（策略变化）、`summary.result_status`（needs_practice / needs_confirmation / tentative_transfer）。`judgment_change`保存当前判断的before/after、trigger_action及对应的trigger_evidence_id、trigger_answer_id或trigger_feedback_id，完整变化另存服务端judgment_history。证据中的反馈独立于作答，不能合并成正确率。

`summary.evidence_ids`除作答外，也引用导致当前“仍需核对”状态的有效反馈。新题完成后，旧猜测不再影响新答案；反馈保留在完整证据与历史中。统计答题数与正确率时仅计算作答，不能把反馈引用算作一次答题。

错误统一为 `LabError`，包括 `CONTINUE_UNAVAILABLE`（details.reason）、`TIME_BUDGET_EXHAUSTED`、`SESSION_STOPPED`、`STALE_REVISION`、`REQUEST_CONFLICT`、`MODEL_UNAVAILABLE`。失败不清空旧题和记录；没有静默模拟回退。

## 验证边界

本地测试、真实模型合成路径、浏览器验收和独立审查分别记录。真实网络调用成功只说明这批自编样本能在该模型上跑通，不能证明真实学生学习效果。整站前端、队友主后端、生产数据库和任意学习资料尚需联调验证。

旧交付包保留作为v1基线；升级包与最终验证记录以本次独立审查后的清单为准。
