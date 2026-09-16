# OpenFic 分支说明

这是 [syrizelink/OpenFic](https://github.com/syrizelink/OpenFic) 的个人维护分支。

OpenFic 的基础功能、部署方式和使用说明请直接查看[上游 README](https://github.com/syrizelink/OpenFic#readme)。这里仅记录本分支与上游的不同之处。

[查看上游版本](https://github.com/syrizelink/OpenFic/releases) · [English](./README_EN.md)

## 主要改动

### 模型与提供商

- 内置的 `google-vertex` 提供商改为原生接入：可直接调用 Vertex AI 上的 Gemini 对话模型，支持流式输出、工具调用与结构化输出，不再依赖 OpenAI 兼容接口。
- 连接支持两种认证方式：ADC（应用默认凭据，从后端运行环境读取）和 Service Account JSON；每个连接单独配置 GCP 项目与区域。
- 凭据加密保存，不会以明文落库，也不会写入日志、Agent 检查点或运行记录。
- 保存连接前可以对指定模型发起一次最小调用进行验证，失败时返回脱敏的错误提示。
- 浏览器、桌面本地模式和桌面远程模式共用同一套连接与调用逻辑。
- 暂未支持 Vertex 的向量检索（Embedding）；`google-vertex-anthropic` 仍不提供原生调用。

### 智能体设置

- 每个内置或自定义智能体都可以单独设置思考强度，共 7 档：跟随会话 / 父智能体、关闭、低、中、高、超高、最大。
- 主智能体的设置作为新会话的默认值；在聊天窗口中临时修改只对当前会话生效，切换主智能体或新建会话后恢复默认。
- 子智能体可以继承主智能体当前实际使用的强度，也可以关闭思考或固定为某一档。
- 已创建的会话沿用创建时解析出的配置，修改智能体默认值不会影响正在进行的会话。
- 档位会按目标模型的能力自动适配（例如三档模型会把「超高 / 最大」归一为「高」）。

## 其他

许可证仍为 [Apache License 2.0](./LICENSE)。本分支不单独发布版本，也未开启 Issues，改动以本仓库的提交记录为准；上游项目的问题请在[上游仓库](https://github.com/syrizelink/OpenFic/issues)反馈。
