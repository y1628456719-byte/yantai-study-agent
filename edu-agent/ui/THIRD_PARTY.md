# 展示组件来源与改动

本目录是现有 FastAPI + 原生 JavaScript 页面的 React 展示岛。诊断、作答、反馈、排程、状态校验继续由原有控制器和服务处理。构建产物已放入 frontend，运行网站无需 Node；修改本目录后在 ui 运行 `npm ci`、`npm run build`。

- React Bits [SpotlightCard](https://reactbits.dev/components/spotlight-card)：复制官方 TS 默认版与 CSS，配置浅色、关闭 ambient、flare 和 grain，减少动效偏好时使用静态容器。原版许可为 MIT + Commons Clause，全文保留于 licenses/react-bits-LICENSE.md。仅作为本应用的一部分使用。
- Magic UI [Border Beam](https://magicui.design/docs/components/border-beam)：复用官方运动路径算法，将 Tailwind 类替换为本地 CSS；只在真实请求忙碌且用户允许动效时挂载。MIT，见 licenses/magicui-LICENSE.md。
- Vercel AI Elements [Task](https://elements.ai-sdk.dev/components/task)：复用官方折叠展示结构，导入改为本地 Radix Collapsible 和 cn；提供真实 button 触发器和本地 CSS。只展示服务返回的工具执行结果。
- Vercel AI Elements [Inline Citation](https://elements.ai-sdk.dev/components/inline-citation)：选择性复用 InlineCitationSource、InlineCitationQuote 两个展示组件；未引入未使用的轮播、悬停和 AI SDK。Apache-2.0，见 licenses/ai-elements-LICENSE。

React、React DOM、Motion、Radix Collapsible、Lucide、esbuild 的准确版本由 package-lock.json 固定。构建产物保留依赖许可注释；上游组件的下载原件与 SHA-256 记录在 design-work/20261017-modern-demo/references。

水墨背景是本次由 Codex 内置图像生成工具生成的 PNG：frontend/assets/scholar-ink-landscape-v1.png。工具未提供模型版本选择或回执，不将 image2.5 版本视为已验证事实。背景固定，不做平移、缩放、视差或纹理动画。
