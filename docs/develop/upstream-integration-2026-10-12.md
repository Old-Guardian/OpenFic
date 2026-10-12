# 上游 8 个提交集成验收（2026-10-12）

集成分支：`integration/upstream-2026-10-12`。本次通过 merge 纳入
`upstream/main` 的 `4de9dd4c472e4cd7238c74156fff7dd36face22c`，保留完整上游提交历史。
目标 main 仍为 `435d736a7b7d94ac98a7908019e32e745200679e`，本次没有合并或推送 main。

## 冲突处理

前面三个修复提交 `f2efa56`、`624932a`、`28884fc` 保留；继续合并剩余上游内容时，
共解决 16 个文件冲突，并处理 Git 不会报告的重复迁移编号问题。

| 文件或范围 | 最终处理 |
| --- | --- |
| `backend/app/api/routers/model_providers.py`、`backend/tests/api/test_model_provider_validation.py` | 保留 fork 的 `(request, session, service)` 签名、Vertex 验证和旧凭据读取，保留已适配的 24 项测试及上游错误详情格式化。 |
| `backend/tests/storage/test_dashboard_metadata_indexes.py`、`docs/develop/performance/dashboard.md` | 保留 dashboard 的 1029 迁移及测试说明；移除合并再次带入的 `1027_dashboard_metadata_indexes.py`，已有 fork 1027/1028 不改编号。 |
| `frontend/src/components/index.ts` | 同时导出 fork 的 AliasInput 和上游 InfoTooltip。 |
| `frontend/src/components/provider-id-select.tsx` | 接入移动端选择抽屉；两个 Vertex 标记放入桌面和移动端共用的选项内容。 |
| `frontend/src/features/assistant/components/agent/agent-messages-scroll.ts`、`agent-messages.tsx` | 接入上游滚动解锁和消息布局；保留空首屏自动加载、更早空页跨页与历史锚点恢复。 |
| `frontend/src/features/assistant/components/assistant-sidebar.tsx` | 接入上游侧栏布局及会话继续状态，保留自动合并的 fork 功能。 |
| `frontend/src/features/assistant/hooks/use-agent-message-pagination.ts` | 保留已修复的分页控制器：空页推进、防重复请求、防 cursor 停滞、重试及过期会话响应丢弃。 |
| `frontend/src/features/assistant/hooks/use-agent-session.ts` | 接入继续会话、generation/round 保护；保留模型/推理强度覆盖状态及 callback 依赖。 |
| `frontend/src/features/assistant/hooks/use-subagent-session.ts` | 接入上游子会话状态和轮次保护。 |
| `frontend/src/features/settings/components/connection-form-dialog.tsx` | 同时保留 fork 验证依赖和上游移动端焦点处理。 |
| `frontend/src/features/settings/components/connections-settings.tsx` | 合并响应式连接信息布局，保留 Vertex 项目、地区、认证模式以及 OAuth 状态。 |
| `frontend/src/features/settings/components/editor-settings.tsx` | 合并上游提示布局，保留 fork 自动保存开关。 |
| `frontend/src/main.tsx` | 保留 Theme 内单个 Toaster 和 EditorLeaveConfirmDialog。 |

编辑器隔离测试页补齐 AppShellContext，以匹配移动端选择组件的新依赖；原有保存断言保持不变。
分页测试页新增真实组件回归，覆盖流式输出期间向上滚动解锁、返回底部恢复跟随，以及移动端选择抽屉和两个 Vertex 标记。

## 验证

- 完整后端测试：**2525 passed，4 warnings**。覆盖迁移链、部署库迁移往返、连接验证、会话继续和轮次保护。
  使用隔离数据目录，不操作真实用户数据库；测试子进程设置 `UV_NO_SYNC=1`、`UV_OFFLINE=1`
  和工作区内的 uv 缓存，复用已有依赖。
- 后端 ruff、ty、`uv lock --check --offline` 通过；Alembic 只有 `1029 (head)`。
- 前端 lint/typecheck、59 个改动文件格式检查、生产构建和 precache 通过。
- Playwright **117 passed**：分页、滚动跟随解锁、移动端选择、dashboard、推理强度、
  编辑器自动保存与生命周期、角色 token、别名、工具消息及 Vertex 契约回归通过。
- 桌面主进程与渲染进程 TypeScript 检查、eslint 和编译构建通过。
  桌面测试 **20 passed，1 failed**；失败为 Windows 创建相对符号链接报 `EPERM`。
  沙箱外复测仍失败，相关 `tar-extract.ts` 和 `archive.test.mjs` 相对集成前没有改动。
- 将最终后端源码与已编译前端资源复制到隔离目录，使用打包资源路径构建
  **0.12.1 sdist 和 wheel 成功**。完整源码构建中的 `pnpm install` 因本地 pnpm/store
  与已有 node_modules 不一致、非交互环境拒绝重建而未完成；没有删除或重装现有依赖。

没有执行需要真实外部模型调用的 E2E，也没有发布、推送或修改系统符号链接权限。
后端套件的 4 个 AsyncMock 未 await 警告和构建的 bundle 体积提示保留在日志中。

## 本地验收证据

- `tmp/remaining-merge-final-tests.log`、`tmp/remaining-merge-final-tests.xml`
- `tmp/remaining-merge-ruff.log`、`tmp/remaining-merge-ty.log`、`tmp/remaining-merge-final-types.log`
- `tmp/remaining-merge-browser-accepted.log`
- `tmp/remaining-merge-frontend-build.log`、`tmp/remaining-merge-precache.log`
- `tmp/remaining-merge-desktop-tests.log`、`tmp/remaining-merge-archive-unsandboxed.log`、`tmp/remaining-merge-desktop-build.log`
- `tmp/package-validation-20261012/package-build.log`、`tmp/remaining-merge-packages/`

临时日志、测试数据和安装缓存保留在忽略的 `tmp/` 中；原始审查记录见
`docs/plan/upstream-review-2026-10-12.md`，本文件是最终集成状态的验收记录。
