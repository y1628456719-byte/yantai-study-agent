# 盲点实验室

新增自适应版本入口见 [ADAPTIVE.md](ADAPTIVE.md)：支持反馈、有限多轮验证、时间调整和本地可操作验收页。下面保留的三题路线说明对应 `Lab / lab.v1`；旧真实调用与独立审查只覆盖当时的v1版本，升级版本的当前验证见 `verification/adaptive-final.json`。

一个可独立运行的 Python 学习模块，提供“首题诊断 → 区分题 → 引用讲解与新题 → 验证总结”的三题闭环。使用自编演示材料，首版仅支持“充分条件与必要条件的方向判断”。

模块模拟路径已验证，AI Ping的DeepSeek-V4.1-Flash也已完成支持、修正与不确定三条真实三题闭环。Kimi、GLM的尝试分别记录，不能视为全部通过；团队主后端、数据库和网页尚未联调。`is_demo`标记数据来源，`runtime_mode`标记执行方式，两者分别展示。

## 运行

Python 3.10 及以上，无第三方依赖；本机使用 Python 3.12.10。以下模拟命令无需配置密钥或初始化 Git。在 PowerShell 中运行：

```powershell
Set-Location -LiteralPath 'D:\莱森'
python -m blindspot_lab.examples.demo --scenario revised
python -m unittest discover -s blindspot_lab/tests -v
python -m blindspot_lab.examples.verify
```

第三条命令保存测试日志、四条完整操作路线和源文件哈希，写入本模块的`verification/`与`examples/outputs/`。它不会连接远端模型。

| 路线 | 首题、区分题、验证题选项 | 可观察的结果 |
| --- | --- | --- |
| `supported` | A、B、B | 两次反向选择，方向混淆猜测获得本轮支持 |
| `revised` | A、A、B | 直接概念题答对，削弱初始猜测；转向复查复杂表述 |
| `inconclusive` | A、D、A | 仍无法区分原因；换题失败，建议继续练习 |
| `correct` | B、A、B | 本轮未观察到明确困难，继续迁移验证 |

`--full`显示完整 JSON；`--output blindspot_lab/examples/outputs/manual.json`可另存输出。脚本按给定路线自动提交答案，属于规则模拟演示，不代表真实学生或实时模型。

## 主后端接入

```python
from blindspot_lab import Lab, LabError, build_learning_plan
from blindspot_lab.lab.models import GenerateJSONAdapter

# generate_json由主后端提供，并负责模型、凭据及真实网络超时。
# 签名：generate_json(messages, schema, timeout_seconds) -> dict或JSON字符串
lab = Lab(model=GenerateJSONAdapter(generate_json), timeout_seconds=30)
result = lab.start_lab(context)
result = lab.submit_answer(context, question_id, answer)
```

两个入口都是同步函数；主后端如使用异步路由，应在其工作线程中调用。异步模型函数可通过`AsyncGenerateJSONAdapter`显式桥接，它在模型调用线程中创建独立事件循环。不要跨事件循环复用已绑定其他循环的异步客户端；具体复用方式应在接入真实服务时核对。未经包装的协程会被拒绝。

模块级`start_lab(context)`和`submit_answer(context, question_id, answer)`便于明确指定`mock`模式的独立测试；真实运行应复用注入服务后的`Lab`实例。实例不保存学习状态，但持有有界模型调用器。

主后端从可信存储组装 context，以`base_revision`做原子比较，一次性保存模块状态、展示状态、事件和请求结果。成功后使用`next_revision`；`state_changed=false`表示恢复已有轮次，不应额外递增版本。前端只接收`lab_state`和允许公开的事件，不接收服务端题库或内部快照。

参考存储位于`examples/host_storage.py`，只用于验证幂等、版本冲突和 JSON 恢复约定，不能替代队友的数据库实现。详细字段与错误见`contracts.md`。

## 模型与证据边界

区分题提交前，公开卡片只展示简短反馈、暂时假设和作答依据，不展示材料定义与教学解析。首轮模型即使主动讲解，程序也会用受控短文本替换公开反馈；区分题提交后才展示引文与讲解。材料在其他页面是否已被学生阅读仍需整站确认，判断同时保留即时学习或猜测等替代解释。

模型请求包含所有已答题的题干、选项、用户选择和已揭晓的判分依据。未来题只传ID与阶段，不传题干、选项或标准答案。讲解使用已答题或一般规则，第三次作答也会更新最终判断与引用。

程序确定阶段、判分、允许的候选假设和题目集合；模型组织反馈、原因与讲解，并只能返回集合中的题目ID。当前每步只有一个允许的后续题目，未实现开放式自适应选题。判断分支由作答驱动，模拟模型直接返回受约束模板。

引文从真实材料提取，使用 Python 字符索引定位；模型引用证据ID，不能改写引文。程序检查ID、原文和定位是否一致。这能阻止编造引用，不能自动证明自由生成的解释语义正确；真实模型接入后仍须检查含义与题目一致。

每次提交最多一次正常调用和一次结构修复。超时、服务失败或第二次校验失败会抛出`LabError`，输入状态不变。没有静默降级。`live`必须注入标记为`live`的适配器；用假服务通过适配器测试仍然只是模拟测试。

调用器使用最多两个后台调用线程限制同一实例的未退出请求。Python不能强制终止正在运行的同步网络函数；模块超时后会忽略其结果，但远端请求可能仍在执行。因此主后端传入的服务必须落实网络超时，不能将模块返回超时理解为已撤销远端请求。

`ReplayModel`只回放匹配的材料、作答与约束摘要，模式始终标为`replay`；回放输出重新绑定本轮作答ID。回放记录应从可信来源导入，不代表新的模型推理。

## AI Ping真实调用

新增的`lab/aiping.py`只连接已核对的[官方文本端点](https://aiping.cn/docs/API/text-models)，不改Codex或Antigravity的默认配置。允许模型为DeepSeek-V4.1-Flash、Kimi-K3、GLM-5.3，均请求max思考强度；不自动换模型或购买加速。

凭据优先从`AIPING_API_KEY`读取。本机授权密钥保存在`%LOCALAPPDATA%\BlindspotLab\aiping-lab.dpapi`，由Windows当前用户的DPAPI加密，未写入项目。其他机器可自行设置环境变量；本机加密文件不能当作团队共享配置。

```python
from blindspot_lab import Lab
from blindspot_lab.lab.aiping import AipingClient
from blindspot_lab.lab.models import GenerateJSONAdapter

client = AipingClient(model="DeepSeek-V4.1-Flash")
lab = Lab(model=GenerateJSONAdapter(client.generate_json), timeout_seconds=120)
# context.runtime_mode须为live；不同执行模式要新建轮次。
```

明确执行远端调用的命令：

```powershell
python -m blindspot_lab.examples.live_check --live --models DeepSeek-V4.1-Flash --scenario revised
```

此命令会消耗平台额度，用预设答案测试真实模型，记录阶段返回、请求ID、耗时和usage，并绑定代码及题库哈希，不记录密钥或reasoning_content。结果位于`verification/live/`，`completed`才表示该模型的这条模块路线通过，旧失败尝试保留在`attempt1/`。普通测试与verify命令不会发起远端调用。

Antigravity CLI已在本机检出Gemini 4 Argon和Gemini 3.8 Flash；任务内只读包装器位于`examples/antigravity_review.ps1`。CLI拒绝Gemini的max参数，实际采用其可用列表最高的high预设。CLI进程成功、审查正文可用和独立审查通过分别记录，不能相互替代。

## 文件与交付

- `lab/`：状态、证据、模型适配与时间计划函数。
- `fixtures/`：自编演示材料、服务端答案和审题说明。
- `examples/outputs/`：运行生成的完整输入与每阶段输出、结构化错误。
- `tests/`：分支、全部答案组合、引用、失败、恢复与参考存储检查。
- `verification/`：本地运行证据、源文件版本与独立审查记录。
- `HANDOVER.md`：接手入口、接入清单、已验证范围。

根目录没有团队 Git 仓库，未初始化、提交、推送或发布。所有开发文件位于`D:\莱森\blindspot_lab`，未改写已有报告和材料评估文件。
