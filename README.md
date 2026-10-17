# 砚台开发交接包

先读 HANDOFF.md，再在 edu-agent 目录运行 Setup.ps1、Start.ps1。

本包包含首次备考推荐入口、左侧档案/参考入口和受限逻辑动态练习。本机已有源代码检查记录在 validation，当前功能与限制以 HANDOFF 为准。旧方案保留在项目文档中，旧统计和命令不代表本版状态。

请解压到新目录。默认 Start.ps1 -Mode mock -Port 8790 是离线演示；真实模式使用 -Mode live -Port 8791，并在启动前提供自己的 AIPING_API_KEY。密钥、个人会话数据库、虚拟环境、私人课程资料、node_modules 与恢复备份不在包内。新机器安装、团队联调与学习效果尚未验收。
