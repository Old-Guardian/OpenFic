# Agent 历史消息分页

集成上游 #441（69c180a）时，保留 fork 的模型覆盖与推理强度覆盖设置，并修复空投影页导致历史无法读取的问题。

服务端按原始消息 seq 分页，然后过滤 subagent 内部消息和重复工具结果。因此 `messages=[]`、`messages_has_more=true`、cursor 非空是合法响应，并不表示没有历史。

前端处理规则：

- 首屏没有可见消息且仍有历史时，自动请求更早页面，不依赖滚动锚点。
- 一次加载会跳过连续空投影页，直到出现消息或历史结束；中间空页不改写消息数组，保留已有滚动锚点。
- 请求保持互斥；每一页必须推进 cursor 才能继续，防止异常响应造成循环。
- 失败时保留已经推进的 cursor，停止自动加载，等待用户重试。
- 会话切换、重载及卸载时通过 generation 丢弃过期响应，停止旧会话的后续请求。

`frontend/e2e/agent-message-pagination.spec.ts` 使用实际 `AgentMessages` 和 `useAgentMessagePagination`，覆盖空首屏、连续空页、空旧页与滚动锚点、失败重试、历史耗尽、异常 cursor 和会话切换。前端开发服务器运行在默认 9000 端口时，可运行：

```powershell
cd frontend
node node_modules/@playwright/test/cli.js test agent-message-pagination.spec.ts
```

测试仅访问模拟分页端点，不需要真实用户数据库。后端 `test_tasks.py` 同时验证原始消息分页、工具身份与参数的跨页一致性，以及空投影页仍返回可推进的 cursor。
