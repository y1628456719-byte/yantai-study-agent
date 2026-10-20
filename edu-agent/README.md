# 砚台应用：运行与开发

本目录是应用根目录，包含 FastAPI 服务、前端页面、SQLite 存储和盲点实验室适配。项目介绍见 [仓库首页](../README.md)，当前模块关系见 [架构说明](../docs/ARCHITECTURE.md)。

## 环境与安装

推荐 Windows、PowerShell、Python 3.11+；已有运行验证使用 Python 3.12。请先确认 `python --version`，再在本目录执行：

```powershell
.\Setup.ps1
```

脚本创建本目录的 `.venv`，按 `requirements-lock.txt` 安装依赖，使用脚本中配置的清华 PyPI 镜像。首次安装需要网络。仓库不含虚拟环境或 `node_modules`；页面构建产物已包含，直接运行无需 Node.js。

## 模拟演示

```powershell
.\Start.ps1 -Mode mock -Port 8790
```

打开 <http://127.0.0.1:8790/>。保持启动终端运行，按 `Ctrl+C` 停止。mock 使用固定题与模拟反馈，不连接真实模型，不生成动态题。

首次使用按页面填写备考情况、比较并确认目标，再进入学习流程。预置练习档案是独立体验案例，不是真实学生历史，也不代表 24 道不同题。“恢复默认”创建新会话，不删除原记录。

## 真实模型模式

在同一个 PowerShell 窗口中安全输入自己的 AI Ping API key，再启动：

```powershell
$secureKey = Read-Host 'AI Ping API key' -AsSecureString
$credential = [System.Net.NetworkCredential]::new('', $secureKey)
$env:AIPING_API_KEY = $credential.Password
.\Start.ps1 -Mode live -Port 8791
```

打开 <http://127.0.0.1:8791/>。该模式会向外部模型发送完成当前任务所需的上下文并消耗账号额度。不要输入不准备发送给模型服务的个人信息或材料。

`run.py` 的默认通路为 `https://aiping.cn/api/v1` / `DeepSeek-V4.1-Flash`。它在启动时读取进程环境中的 `LLM_API_KEY` 或 `AIPING_API_KEY`；Windows 当前用户已有的本地 DPAPI 凭据也可由加载器读取。新机器需要自己的配置，不能依赖开发者本机凭据。修改环境变量后需重启进程。

请直接设置进程环境变量，不把复制或填写 `.env` 作为本入口的密钥配置步骤。凭据缺失时 live 启动会失败，模型请求失败时不会静默返回 mock 结果。受限动态题在首轮之后的“继续下一轮”中产生，不是首次进入就无限生成。

## 数据与检查入口

| 位置 | 用途 |
| --- | --- |
| `data/workbench-mock.sqlite3` | 默认 mock 会话库，运行时创建，不入库 |
| `data/workbench-live.sqlite3` | 默认 live 会话库，与 mock 分开，不入库 |
| `EDU_AGENT_DB` | 可在启动前指定其他 SQLite 路径；两个模式同时运行时应使用不同路径 |
| `data/schools/`、`data/raw/` | 已接入院校的解析结果和来源快照 |
| `/api/health` | 本地健康检查及执行模式信息 |
| `/docs` | FastAPI 运行时接口文档 |

SQLite 保存会话，浏览器 cookie 用于找回当前会话。更换浏览器、清除 cookie 或点击“恢复默认”会改变当前访问的会话，不代表磁盘上的旧记录被删除。数据与凭据边界见 [SECURITY.md](../SECURITY.md)。

## 后端检查

在本目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

测试夹具配置离线模式和临时数据库；真实适配器相关测试中的受控替身不算真实模型验证。历史结果和实际调用记录见 [验证说明](../docs/VALIDATION.md)。不需要为阅读文档或启动 mock 反复运行真实模型检查脚本。

## 修改前端展示组件

普通页面位于 `frontend/`，React 展示组件源码位于 `ui/`。只有修改 React 组件时才需要安装 Node.js 及构建依赖：

```powershell
cd ui
npm ci
npm run build
```

构建更新 `frontend/enhancements.js`，不会替代业务控制器。`ui/build.mjs` 将 `/api.js` 保留为外部共享连接器，修改构建时应继续保持这一行为，避免产生独立的会话状态源。依赖版本由 `package-lock.json` 固定，组件来源及许可见 [ui/THIRD_PARTY.md](ui/THIRD_PARTY.md)。

## 常见情况

- **浏览器拒绝连接**：先看启动终端是否仍在运行、端口是否与启动参数一致。关机或重启后需要重新启动本地服务。
- **8790 或 8791 被占用**：确认原进程的用途，或给 `-Port` 指定空闲端口；不要直接结束其他项目的进程。
- **真实模式提示缺少密钥**：在准备启动服务的同一窗口设置环境变量，再启动；不要把密钥写进源码、截图或 Issue。
- **学校某专业没有推荐资料**：表示当前快照没有覆盖，不能据此判断该校是否招生；查看页面的资料缺口及来源。
- **旧文档命令不一致**：优先使用本页的 `Setup.ps1`、`Start.ps1` 入口；`docs/` 与独立模块文档中的旧绝对路径属于历史记录。
