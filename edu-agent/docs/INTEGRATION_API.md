# 整站接入接口

本次把 Kimi 页面接到主后端。院校、档案、资料和计划仍使用原来的 `/api/v1`；盲点实验室增加两个同源桥接接口。两组接口共享 SQLite 中同一条 session，不另建状态机或数据库。

## 页面状态与会话

`GET /api/state` 返回原始 JSON，不使用 `{ok,data}` 外壳：

```json
{
  "revision": 1,
  "runtime_mode": "mock",
  "model": null,
  "lab_state": null,
  "plan": null,
  "session_id": "s_随机值",
  "session": {"session_id": "s_随机值", "revision": 1, "profile": {}}
}
```

首次读取会创建空档案会话并按模式设置 `edu_session_mock` 或 `edu_session_live`：`HttpOnly; SameSite=Strict; Path=/`。随后由浏览器发送 Cookie。同一浏览器打开模拟和真实模式时，两份 Cookie 互不覆盖；已有旧版 Cookie 会在对应数据库中迁移。Cookie 只关联已存在的随机 session，不接收前端学习快照。页面可用返回的 `session_id` 调原 `/api/v1/sessions/{session_id}` 等接口；业务变更后重新读 `/api/state`，取得新 revision。

`lab_state` 保留 vendor 的原始公开 lab.v2 字段，例如对象形式的 `explanation`、证据及 `cycle`，不沿用旧页面的扁平转换。`plan` 是实验室的 `{tasks,deferred,...}`；`session.plan` 是主后端原有的科目学习计划，两者分别表示本轮验证安排和整体科目安排。

所有公开会话响应都移除了 `learning_state.blindspot` 的可信内部快照、宿主上下文和请求缓存。SQLite 内部仍保存完整快照。Agent 返回已经执行的动作及结果摘要，不返回 `decision.thought`。

## 实验室动作

`POST /api/action` 接受六个动作：

| action | 参数 |
| --- | --- |
| start | 无 |
| submit | question_id、answer |
| feedback | feedback：unclear / too_easy / guessed / new_example |
| time | minutes：1~1440 的整数 |
| continue | 无 |
| stop | 无 |

请求支持 Kimi 控制器现有的顶层参数：

```json
{
  "action": "submit",
  "session_id": "s_发起会话",
  "request_id": "为本次操作生成的唯一ID",
  "expected_revision": 2,
  "question_id": "q_initial_01",
  "answer": "A"
}
```

也支持 `"params":{"question_id":"q_initial_01","answer":"A"}`。同一字段不能同时出现在顶层和 params。成功返回与 `/api/state` 相同的公开状态形状；失败使用原后端的中文错误外壳与 HTTP 状态码，冲突为 409，参数问题为 400，模型不可用为 503。

新桥接动作必须在顶层携带发起时的 `session_id`，服务端在读取动作缓存或执行前与 Cookie 会话核对。会话不符时返回 409，缺失时返回 400，不创建会话、不调用模型，也不修改状态或请求缓存。旧 `/api/v1` 已在 URL 中指定会话，契约保持不变。

原来的 `/api/v1/sessions/{id}/lab/start`、`lab/answer`、`lab/action` 继续可用，仍返回 `{ok,data,error,request_id}`。它们也接受可选的 `request_id` 和主会话 `expected_revision`，与新入口共用同一个服务和请求结果表。

## 重试、版本与保存

一次用户操作生成一个新 request_id；网络丢失后重试必须沿用原 ID、原 expected_revision 和原参数。相同 ID 的成功请求直接返回 SQLite 保存的原结果，不再次批改或调用模型。相同 ID 传不同参数返回 409。旧客户端没有 request_id 时，每次请求生成新 ID，不会把后续轮次相同的反馈或 continue 合并成第一次操作。

原结果可能早于当前会话。浏览器对同一 session 只采用不低于当前 revision 的状态；收到较旧的已确认动作结果时，先读取当前 `/api/state`，不发布旧预算或旧计划。先前会话的在途响应也不能把页面切回旧会话。保存回调捕获发起 session ID，只清理该会话的草稿；已经切换会话时，不修改新会话的输入或错误提示。

浏览器记录会话采用代次：请求发出后若已经采用另一会话，迟到的不同 session 响应会被丢弃，包括“从 A 发起、读到 B、但 C 已被采用”的情况。旧版待重试记录只能用存档外层的 session ID 补齐请求体，保留原请求 ID、版本和动作参数；没有归属或内外会话不一致的记录不会发送。

expected_revision 是**主会话版本**，不是实验室内部版本。服务先计算候选结果，再以 SQLite `BEGIN IMMEDIATE` 事务检查主版本；完整快照、主档案时间、主 revision 和请求结果在一个事务中保存。冲突或失败不应用候选结果。不同进程也由 SQLite 写锁裁定；同进程的共享连接额外使用锁防止交叉事务。并发同一 ID 可能在缓存提交前重复发出模型请求，但只有一个结果应用；已提交后的重试不再调用模型。

## 时间入口

实验室 time 动作同时更新 `session.profile.daily_minutes` 和可信宿主的 profile。原档案 PATCH、原 plan/revise 更新每日时间时，也在同一个保存事务中同步宿主：使用 vendor time 动作保留历史、作答与预算逻辑，不调用模型。已停止的实验只更新宿主档案时间，仍保持停止状态。

已经生成的整体计划在每日时间、目标院校或报考方向变化时，也在同一事务内按当前条件重排，标记 `generated_by=rules`，不调用模型。执行任务总时长不超过每日预算；预算不足 10 分钟时全部暂缓，并返回原因。动作响应中的 `session.plan` 取实际落库后的内容。原请求 ID 重放仍返回当时保存的完整结果，不把旧结果重新写回当前会话。

长耗时的推荐、计划及建档追问在模型返回后检查读取时的主 revision；如果其他操作已推进会话，返回 409，防止旧结果覆盖新时间或新证据。计划和对应决策记录一起提交。

## 模式、网络与模型

`LAB_ADAPTER=blindspot` 启用真实模块；`LAB_BLINDSPOT_MODE=mock` 使用模块模拟结果；`live` 注入 vendor `AipingClient + GenerateJSONAdapter`，使用 `DeepSeek-V4.1-Flash`、`reasoning_effort=max`、thinking enabled。密钥仅从 `AIPING_API_KEY` 或用户已有 Windows DPAPI 文件在服务端读取，不出现在页面或源码中。单次模型调用最多 120 秒，包含结构修复的动作外层允许两个调用时长及少量余量。失败不降级为模拟结果。

主推荐/Agent 模型通路同样对已授权 AI Ping 模型请求 max/thinking，并只记录模型、耗时、token 计数等安全元数据。上游错误正文、模型输入正文、思考内容和密钥不回显到错误或日志。

启动绑定 `127.0.0.1`。移除了 wildcard CORS，全部写请求及 `/api/state` 检查本机 Host 和同源 Origin；没有 Origin 的本机脚本仍可调用。`testserver` 只在 TestClient 的测试连接中允许。页面和 API 必须使用同一个地址与端口。

## 已验证范围

新增测试覆盖模拟完整闭环、原 API 连续轮次、扁平请求、幂等、同 ID 参数冲突、旧版本拒绝、并发 CAS、外部 SQLite 写入冲突、重启恢复、失败不写入、各时间入口同步、慢计划并发保护、内部快照投影、同源限制、模型注入、最高档 payload 和错误脱敏。

这些测试使用模拟模式或注入传输，不属于真实模型验证。真实模型整站验证和浏览器交互验收由主交付记录单独报告。实验室仍只支持随包自编逻辑材料与题库；上传资料没有被用于实验室出题，逻辑样例表现不映射成通信等其他学科的实测能力，也没有真实学生效果结论。
