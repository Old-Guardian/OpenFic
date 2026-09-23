# 编辑器保存与角色 Token 统计修复方案

日期：2026-09-23
状态：T0/T1/T2 已完成；T3 已实现待验收；T4 待实施。
建议分支：`fix/editor-save-and-token-count`

## 1. 目标与范围

修复三个已定位问题：

1. 进入角色页面、未编辑内容时误显示“有未保存的更改”。
2. `pnpm dev` 开发模式中，点击保存没有发起保存请求。
3. 角色列表与编辑器底部对相同内容给出不同 Token 数。

本次保留既有自动保存开关、保存延迟、Agent 锁定、离开确认和本地草稿恢复行为。列表继续统计服务端已保存描述，底部继续统计当前描述草稿；未保存编辑期间允许二者不同。名称和别名不纳入描述 Token 统计。

不新增数据库字段或迁移，不调整 API 响应结构，不新增草稿跨组件实时同步，不将此次工作扩展为全项目 Token 统计或编辑器重构。

## 2. 原因与证据

以下路径均相对仓库根目录。结论来自代码检查及本地依赖最小复现；未使用截图角色的完整原文，因此不宣称已复现截图中的 1239/895 数值。

### 2.1 初始化产生虚假 dirty 状态

- `frontend/src/components/markdown-editor.tsx` 先注册 `update` 监听，再调用 `editor.setEditable(!isLocked)`。
- 当前 Tiptap 的 `setEditable` 默认 `emitUpdate = true`，即使正文未修改，也发送 `update`。
- 监听器比较 `getMarkdown()` 与输入字符串。Markdown 解析后重新序列化可能改变列表符号、空行和末尾换行。
- `frontend/src/features/characters/components/character-editor.tsx` 的 `handleContentChange` 无条件设置 dirty 并递增版本。

最小复现中，`*` 列表变为 `-`，末尾换行被移除；事件的 `transaction.docChanged` 为 `false`，但字符串比较仍将其判为修改。关闭自动保存后，虚假 dirty 持续显示。

### 2.2 StrictMode 清理后复用失效调度器

- `frontend/src/main.tsx` 启用了 `StrictMode`。
- `frontend/src/hooks/use-auto-save.ts` 在渲染阶段创建调度器，在 Effect cleanup 中调用 `dispose()`。
- 开发模式额外执行 Effect 建立、清理、再建立；再次建立时没有创建新实例。
- `frontend/src/hooks/auto-save-scheduler.ts` 的 `dispose()` 永久标记实例失效，后续 `updateConfig()` 被忽略，`triggerSave()` 直接返回 `unchanged`。

最小复现中，正常实例可以执行保存；模拟清理后再次更新配置，手动保存不再调用保存适配器。关闭自动保存本身不会禁止手动保存。

### 2.3 Token 编码器及统计时点不一致

| 数据路径 | 当前实现 |
| --- | --- |
| 角色列表 API | `backend/app/storage/services/character_service.py` 的 `calculate_token_count` 使用 `cl100k_base` |
| 编辑器底部 | `frontend/src/lib/tiktoken-utils.ts` 默认使用 `o200k_base` |
| 保存后的列表缓存 | `characters-page.tsx` 先用前端计数更新，再失效查询、获取后端计数 |

同一段中文描述最小样本使用两种编码分别得到 33 和 23 Tokens。保存后先本地更新、再服务端刷新，还可能导致列表数值跳变。

此外，初始化序列化可能改变参与计数的字符串；列表和当前草稿本来就具有不同更新时点。这些因素需要与编码器差异分别验证。

## 3. 技术选型与实施约束

| 事项 | 确定方案 | 理由 |
| --- | --- | --- |
| 内容事件 | Tiptap `setEditable(value, false)`，配合正文事务过滤 | 阻止状态切换伪装为正文修改 |
| Markdown 同步 | 保留原始已保存字符串；外部同步不发送内容更新 | 浏览行为不应重写文档格式 |
| 保存生命周期 | React Effect 管理调度器创建和销毁，Ref 暴露当前实例 | 兼容 StrictMode 重建，避免渲染阶段启动调度副作用 |
| 保存并发 | 保留现有按 documentKey 串行队列及版本快照 | 不削弱保存中继续编辑、跨实例串行等保证 |
| 角色 Token 编码 | 前后端统一为 `o200k_base` | 与现有前端默认值及后端公共工具默认值一致 |
| Token 实现 | 前端继续使用 `js-tiktoken`，后端复用公共 tiktoken 工具 | 已安装且已有离线词表，无需新增依赖 |
| 测试 | 现有 Playwright 与 pytest，加共享 JSON 样本 | 同时验证组件行为、调度逻辑、API 和跨语言计数 |

### 3.1 正文事件与初始化

1. 将公共 Markdown 编辑器的 `setEditable` 改为不发送更新事件。
2. 在 `update` 监听中判断是否有正文变更，再序列化并调用 `onContentChange`。结合当前事件提供的主事务与追加事务判断，避免遗漏插件追加的真实文档修改。
3. 保留已有字符串去重。初始化、光标移动、聚焦和 Agent 锁定状态变化不得产生 dirty 或增加 dirtyRevision。
4. 外部内容同步使用 `contentType: "markdown"` 与 `emitUpdate: false`。审查角色、世界书等相关放弃修改路径中的 `setContent`，确保基线按 Markdown 恢复且不产生新的修改事件。
5. 不在加载时将规范化结果回写后端，也不通过自动保存清除虚假 dirty。不承诺 Markdown 字节级往返无损；真实编辑后仍可沿用 Tiptap 的序列化结果。

### 3.2 保存调度器生命周期

1. 从渲染阶段移除 `new AutoSaveScheduler(...)`，在生命周期 Effect 建立时创建实例。
2. 创建 Effect 应先于配置同步 Effect 执行；新实例用最新配置初始化，后续普通重渲染仅更新配置，不能不断重建实例或重置防抖时间。
3. cleanup 捕获并销毁自己创建的实例；仅当 Ref 仍指向该实例时清空 Ref，避免旧 cleanup 误销毁新实例。
4. 保持 save/cancel/resume/whenIdle 的对外接口。回调通过 Ref 找到当前实例，保存适配器继续读取最新回调。
5. 旧实例销毁后产生的状态通知不得覆盖新实例状态；建立新实例时同步其初始状态，避免残留“保存中”或“已调度”状态。
6. 真正卸载时清理计时器且不隐式保存。已发出的请求允许结束；旧实例排队但尚未发出的任务不得在销毁后继续提交。
7. 保留全局按文档串行队列；重新挂载同一文档时，新旧实例不得并发写入。保存中继续编辑仍保留新版本 dirty。

禁止以删除 StrictMode、将 `dispose()` 变为无效操作，或无条件清除 dirty 的方式规避问题。无需“复活”已销毁实例。

### 3.3 Token 统计契约

- 编码：正常加载路径统一 `o200k_base`。
- 输入：描述 Markdown 原文，包括格式符号；不改为纯文本，也不包含名称或别名。
- 空串及纯空白：统一返回 0，与现有前端行为一致。非纯空白内容保持原文，不先 trim 再编码。
- 后端角色服务复用公共 `count_tokens`，在角色服务边界处理纯空白；不修改公共工具的全局空白语义。
- 更新现有列表计数测试的预期，但保留接口字段及类型；旧数据在下次查询时重新计数，无需批量改写描述。
- 前端启动已有词表预加载；正常路径计数一致性测试必须等待预加载完成。编码加载失败时的既有估算不作为精确计数，不能用降级结果通过一致性验收。
- 保留本次范围之外的降级策略，测试中单独标明其限制：若词表初始化失败，两端估算不保证相等；本次不设计全局降级状态 UI。
- 编辑未保存时两处可以不同；保存完成且列表查询完成、没有后续修改时，两处必须一致，刷新后也必须稳定。

## 4. 相对独立的任务与逐步验收

依赖关系：T0 → T1/T2/T3 → T4。T1、T2、T3 可分别实施和提交，无需互相等待；T4 汇总验收。建议保持一个修复分支，每个任务形成可审查的独立提交。

### T0：建立复现与验收基线

交付：

- 固定包含中文、嵌套列表、`*` 列表符号和末尾换行的角色测试数据。
- 新增 `frontend/e2e/editor-save-regressions.spec.ts`，建立仅测试使用的浏览器挂载入口，以真实 React StrictMode 挂载公共 Hook 和 Markdown 编辑器；不将入口加入产品导航或生产构建。
- 浏览器入口由现有 Vite 开发服务器加载。使用可控保存适配器记录次数、参数，并可延迟完成或返回失败；不访问用户真实项目。
- 区分纯逻辑用例与需要开发服务器的浏览器用例，记录运行前提。

验收：三个问题分别有可重复失败的断言；保存问题必须在真实 StrictMode 挂载下复现，不能仅手动调用 scheduler.dispose() 代替组件测试。

#### T0 基线记录（2026-09-23）

- 状态：已建立可重复失败的基线；三个断言在当前实现下预期失败，待 T1/T2/T3 分别修复。
- 固定样本：`frontend/e2e/fixtures/character-save-baseline.json`，包含中文、嵌套 `*` 列表和末尾换行。
- 浏览器入口：Vite 开发服务器的 `/__editor-save-regressions__/`，由 `frontend/vite.config.ts` 的仅开发插件提供；`frontend/e2e/editor-save-regressions.harness.tsx` 在真实 React StrictMode 下挂载公共 `MarkdownEditor` 和 `useAutoSave`。保存适配器只记录内存中的调用与参数，可立即完成、延迟完成或返回失败，不请求真实后端或项目。
- 运行前提：安装前端依赖和 Playwright Chromium；纯计数用例另需安装 `backend/.venv` 依赖。浏览器用例先在 `frontend` 运行 `pnpm dev`，另一个终端运行 `pnpm exec playwright test e2e/editor-save-regressions.spec.ts`；Windows PowerShell 若禁用脚本，可将 `pnpm` 换成 `pnpm.cmd`。纯计数用例可不启动 Vite，单独运行 `pnpm exec playwright test e2e/editor-save-regressions.spec.ts -g "character service"`。
- 本机实际验证命令（均在 `frontend`）：`node node_modules/vite-plus/bin/vp dev`、`node node_modules/@playwright/test/cli.js test e2e/editor-save-regressions.spec.ts`、`node node_modules/oxlint/bin/oxlint . --type-aware --type-check`、`node node_modules/vite-plus/bin/vp build`。静态检查和生产构建通过，构建产物不含测试入口。
- 首次执行结果：初始化用例观察到 2 次内容回调，`*` 变为 `-` 且末尾多出换行，断言要求 0 次回调而失败；手动保存用例在 StrictMode 下等待 5 秒仍为 0 次适配器调用；计数用例中角色服务为 56、前端精确计数为 44。三项均是业务断言失败，不是入口加载、词表降级或后端请求失败。

### T1：消除初始化与状态切换导致的虚假修改

主要修改公共 `markdown-editor.tsx`，必要时修正相关编辑器的基线恢复调用。

验收：

- 初次加载含格式差异的 Markdown，内容回调次数为 0，页面显示已保存。
- 自动保存关闭时反复进入角色页面，不产生更新请求或离开确认。
- 锁定、解锁、聚焦、光标移动不改变 dirty。
- 输入、删除、粘贴、格式操作以及撤销/重做产生真实正文更新，不被新过滤条件吞掉。
- 放弃修改恢复基线的正文与显示格式，不被误读为 HTML，不留下 dirty。

#### T1 验收记录（2026-09-23）

- 状态：已完成。公共 Markdown 编辑器忽略 `setEditable` 的空更新，只在正文事务变化时回调；Placeholder 刷新引起的单个尾随空段落按编辑器内部维护处理。其他追加事务仍参与正文变更判断。
- 角色和世界书放弃修改时以 Markdown 类型、`emitUpdate: false` 恢复保存基线。
- 隔离浏览器入口在真实 StrictMode 下挂载公共编辑器、实际 `CharacterEditor` 与 `EntryEditor`；自动保存设置固定关闭，不连接真实项目。运行 `node node_modules/@playwright/test/cli.js test e2e/editor-save-regressions.spec.ts -g "T1:"`，6 项通过，覆盖初始回调为 0、锁定/聚焦/光标不脏、输入/删除/粘贴/格式/撤销/重做有更新、角色重复挂载与角色/世界书放弃修改恢复列表格式。完整产品路由回归留给 T4。
- 原有 `editor-auto-save-state.spec.ts` 51 项通过；`node node_modules/oxlint/bin/oxlint . --type-aware --type-check` 与生产构建通过。T2 的 StrictMode 保存断言仍为 0 次调用，T3 的计数断言仍为后端 56、前端 44，均保持预期失败。

### T2：修复 StrictMode 下的保存生命周期

主要修改公共 `use-auto-save.ts`；仅在必要时调整调度器状态通知隔离，不重写串行队列。

验收：

- StrictMode 挂载后，关闭自动保存，编辑并点击保存或按 Ctrl/Cmd+S，均能提交当前内容。
- 无修改时点击保存不发起请求；自动保存关闭时等待超过原延迟仍无自动请求。
- 开启自动保存时一次编辑按既有延迟保存，不因普通重渲染推迟，不因 StrictMode 重复提交。
- 保存过程中继续编辑，旧请求完成不能将新内容标记为已保存。
- 请求失败保留 dirty，重试成功后清除；Agent 锁定期间不能提交。
- 卸载取消待执行自动保存；重新挂载同一文档可保存，且同文档在途请求数不超过 1。
- 切换文档不将 A 的内容提交给 B；旧实例回调不污染新实例状态。
- 原有调度器、离开确认及放弃修改测试继续通过。

#### T2 验收记录（2026-09-23）

- 状态：已完成。`useAutoSave` 在 Effect 中创建并销毁调度器；StrictMode 重建时创建新实例，Ref 只指向当前实例，旧实例的状态通知被隔离。实例初始状态和配置变动后的状态均同步到 Hook 返回值。
- 测试入口保留真实 StrictMode 下公共 Hook 与 Markdown 编辑器的按钮、Ctrl/Cmd+S 路径，并新增可控 Hook 生命周期挂载：保存适配器支持立即成功、延迟完成、失败、排队和按文档记录并发。
- `node node_modules/@playwright/test/cli.js test e2e/editor-save-regressions.spec.ts -g "T1:|T2:"`：16 项通过，其中 T2 10 项覆盖关闭开关与无修改、开启后的既有 1500ms 延迟、重渲染、在途编辑、失败重试、Agent 锁定、卸载重挂、同文档串行、旧排队任务取消与跨文档隔离。
- `editor-auto-save-state.spec.ts` 原有调度器、离开确认及放弃修改 51 项通过；前端类型检查、lint 与生产构建通过。T3 的角色 Token 计数断言仍预期失败。

### T3：统一角色 Token 编码与验证样本

主要修改角色后端服务与对应测试；前端正常计数默认编码不需要改变。

交付：

- 新增 `tests/fixtures/character-token-count-cases.json`，由前后端测试共同读取。
- 样本包含中文、英文、中英混排、嵌套列表、代码块、emoji、空串、纯空白和末尾换行；分别保存完整输入与已核对的预期计数。
- 新增 `frontend/e2e/character-token-count.spec.ts`，扩展 `backend/tests/core/test_tiktoken.py` 和 `backend/tests/api/test_characters.py`。

验收：

- 两端使用同一份样本得到相同的精确计数；预期值固定在样本中，不在测试时用被测函数生成。
- 角色列表 API 返回 `o200k_base` 计数，名称和别名变化不影响描述计数。
- 修改、保存、列表重新请求后数字一致，没有前端临时值被不同编码的后端值覆盖的跳变。
- 未保存时列表保持已保存值，底部反映草稿值；放弃后恢复一致。
- 不更改角色描述原文、不迁移数据库、不影响其他实体主动指定编码的行为。

#### T3 实施记录（2026-09-23）

- 状态：已实现待验收。角色服务改为复用公共 `count_tokens`，正常路径使用其默认 `o200k_base` 编码；仅角色服务在调用前将空串及纯空白描述计为 0，其他非空白 Markdown 保留原文计数，公共工具和世界书计数保持原行为。
- `tests/fixtures/character-token-count-cases.json` 固定 10 个完整输入和预期值，覆盖中文、英文、中英混排、嵌套列表、代码块、emoji、空串、纯空白、末尾换行和带首尾空白的非空内容。前后端测试读取同一份文件，预期值不在测试运行时生成。
- 后端核心测试覆盖服务委托、空白边界与全部共享样本；角色列表 API 测试覆盖全部样本、名称和别名变化、描述原文保持、保存后重新查询计数稳定。前端精确计数测试等待词表预加载；隔离浏览器用例使用实际角色列表与编辑器组件验证草稿、放弃修改、保存和模拟列表刷新。
- 定向验证：后端核心与新增 API 测试 21 项通过；`character-token-count.spec.ts` 与原编辑器回归合计 19 项通过；前端类型检查、lint 和生产构建通过。后端 `test_tiktoken.py` 与 `test_characters.py` 全量运行 37 项通过，另有 2 项原有的时间戳/排序用例失败（同一时间戳导致更新时间断言不成立，收藏排序与预期不符）；这些用例未涉及此次修改的计数路径，留待独立排查。

### T4：跨编辑器集成回归与合并验收

依赖 T1、T2、T3 完成。

验收：

- 开发模式验证真实 StrictMode，生产构建验证正常生命周期，两种环境分别记录结果。
- 角色、世界书、笔记覆盖公共 Markdown 编辑器；章节、笔记、世界书、角色覆盖公共保存 Hook。
- 四类编辑器均验证自动保存开关、手动保存、保存失败、离开时保存/放弃/取消；章节与笔记额外确认本地草稿恢复没有回归。
- 角色页面完整执行“进入 → 编辑 → 保存 → 切换 → 再进入 → 刷新”，核对内容、状态和 Token。
- 前端类型检查、lint、生产构建，以及相关前后端测试通过。
- 合并说明包含修复范围、测试命令及结果、未覆盖项；不得将纯逻辑测试通过写成浏览器场景通过。

## 5. 测试分层与执行方式

| 层级 | 核心断言 | 工具/位置 |
| --- | --- | --- |
| 调度逻辑 | 防抖、串行、取消、失败、版本快照 | 现有 `editor-auto-save-state.spec.ts` |
| 真实组件 | StrictMode 重建、无正文变化事件、真实输入和快捷键 | 新增 `editor-save-regressions.spec.ts` |
| 计数契约 | 同输入同输出、空白约定、编码固定 | 共享 JSON + Playwright/pytest |
| 后端接口 | 保存及重新查询后的 token_count | `backend/tests/api/test_characters.py` |
| 页面集成 | 已保存状态、离开确认、缓存刷新和数字稳定性 | 隔离测试项目中的浏览器用例及人工回归 |

浏览器用例优先等待可观察状态和请求完成；断言“不自动保存”时使用受控计时或明确超过对应防抖延迟的观察窗口。不依赖真实 Agent 或外部模型调用。

现有 Playwright 配置的 baseURL 为 `http://127.0.0.1:9000`，没有自动启动 webServer。浏览器测试前需要启动前端；真实页面集成还需要独立测试后端。不得直接使用包含用户数据的生产环境。

在 `frontend` 目录执行（新增文件实施后才可运行对应命令）：

```powershell
pnpm exec playwright test e2e/editor-auto-save-state.spec.ts
pnpm exec playwright test e2e/editor-save-regressions.spec.ts e2e/character-token-count.spec.ts
pnpm type-check
pnpm lint
pnpm build
```

开发服务器另开终端，在 `frontend` 目录执行 `pnpm dev`。后端启动方式沿用 `docs/develop/develop-commands.md`，但数据目录必须指向测试环境。生产构建使用项目支持的预览/部署方式，明确配置后端连接后执行页面回归，不能假定开发代理自动适用于预览。

在 `backend` 目录执行：

```powershell
uv run pytest tests/core/test_tiktoken.py tests/api/test_characters.py tests/api/test_character_aliases.py
```

保留失败 trace、必要的请求记录与测试摘要。每个任务只运行相关检查；全部修复完成后集中执行一次综合回归，避免重复全量测试。

## 6. 风险与完成标准

最高风险是调度器生命周期调整：若每次渲染重建，可能改变防抖、并发或在途状态；必须用普通重渲染和延迟请求场景验收。其次是正文事件过滤过严，可能丢失插件追加事务产生的真实编辑。

统一编码后，既有角色的列表数字可能下降或变化，这是计数口径修正，不代表正文丢失。Token 一致性以词表正常加载、相同字符串及相同保存时点为前提。

计划总工作量约 5–10.5 小时，按 1–1.5 个工作日预留；测试入口搭建及跨编辑器回归是主要不确定项。

逐项勾选后才可合并：

- [ ] T0：三个失败基线可重复，测试数据隔离。
- [ ] T1：初始化、锁定与基线恢复不制造虚假修改，真实编辑正常。
- [ ] T2：StrictMode 与普通生命周期保存正常，并发与离开保护无回归。
- [ ] T3：编码、输入范围与空白语义一致，共享样本和 API 测试通过。
- [ ] T4：四类编辑器及开发/生产模式回归通过，检查结果已记录。

实施中若发现超出这三个原因的新问题，应记录独立触发条件和影响范围，再判断是否是本次验收的必要修复，不顺带扩大为全局重构。
