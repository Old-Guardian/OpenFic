# OpenFic

![GitHub Repo stars](https://img.shields.io/github/stars/syrizelink/OpenFic)
![License](https://img.shields.io/badge/License-Apache_2.0-red)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![GitHub Release](https://img.shields.io/github/v/release/syrizelink/OpenFic?logo=githubactions&logoColor=white&color=yellow)
![Release Downloads](https://img.shields.io/github/downloads/syrizelink/OpenFic/total?logo=github&logoColor=white&label=Release%20downloads&color=yellow)
![PyPI - Version](https://img.shields.io/pypi/v/openfic?logo=pypi&logoColor=white&color=green)
[![交流群](https://img.shields.io/badge/交流群-1105304435-12B7F5?logo=qq&logoColor=white)](https://qun.qq.com/universal-share/share?ac=1&authKey=XxKBo33K1IAy%2FejDsGPWOn51pCNk1Bu1%2F2dtldtWCWSdPGor4tZkaboxgrGkz2BS&busi_data=eyJncm91cENvZGUiOiIxMTA1MzA0NDM1IiwidG9rZW4iOiJCY1NuV2s5d1B2QmI2R0ZiMldMbDE4MVRPV1puMFJlWjZIRlZrRjk4WGUwY2wvdUlaWEFPZ1cvV0lLbWl6d3JwIiwidWluIjoiMjUzMjEyNDQwNCJ9&data=B2VDUuvIYSScsKPwMeFB6txn6fj8I18zG6EKsmsrZDwPpmNCoJ7r5NTLtmUUf58MK3Lw9evkkPg28EglHJNONA&svctype=4&tempid=h5_group_info)

![OpenFic Banner](./banner.svg)

中文 | [English](./README_EN.md)

**OpenFic** 是一款专为小说创作打造的跨平台、用户友好、AI Native 的一站式 Vibe Writing 工具，构建设定、设计角色、定制工作流，让Agent适应你的写作流程，而非反之。

![Demo Screenshot](./demo.png)


## 何时使用

> [!Tip]  
> *OpenFic 的设计理念是让 Agent 深度参与小说创作过程，而不是替你一键生成没有灵魂的文字，它首先是用户友好的小说写作工具，其次才是面向写作的 AI Agent 系统。*

#### 它适合这些场景：

- 正在写中长篇小说，需要长期维护世界观、角色、伏笔和章节信息
- 希望 Agent 协助你发散思路、检查前后文、补全细节
- 提供完整的设定、文风和剧情走向，希望 Agent 帮助你将灵感转化为文字
- 你有自己的写作流程，希望按需求自定义 Prompt、Agent 和工作流
- 看重本地数据保存、上下文管理和可持续的创作协作

#### 它不适合这些场景：

- 输入一句提示词，然后自动得到一整本小说，这是不切实际的
- 主要需要短篇文案、社媒内容或一次性的通用文本生成
- 你不打算维护复杂的设定信息，也不需要长期上下文和写作流程管理


## 特性

- 🚀**开箱即用**：使用 Docker 或 pip 快速安装，或是直接使用桌面版，无需复杂配置
- ✒️**专为写作打造**：面向小说写作优化和设计的编辑器，提供便捷、舒适的码字体验
- 🤝**全面的模型支持**：无缝集成来自多种提供商的模型，或是任何兼容 OpenAI API 的模型
- 📱**响应式UI**：专为多平台适配设计的界面，在桌面端、移动端和浏览器上享受无缝体验
- 🧩**定制化工作流**：高度可配置的 Agent 系统，自由的修改任何 Prompt，构建属于你的工作流
- 🤖**人机协同创作**：与 Agent 深度集成的辅助创作，发散思维、构建情节、协同编辑，而非抽卡式的一键生成
- 💾**本地持久化**：所有项目数据均保存在本地，零云存储依赖，确保隐私数据安全
- 🧠**语义化检索**：基于向量的 Agentic RAG，让 Agent 能够在百万字级别的项目中高效检索过往信息
- ⚖️**成本优先**：多层上下文管理，智能压缩、动态截断、稳定缓存，尽可能降低使用成本


## 本 Fork 新增功能

> 本仓库基于 [syrizelink/OpenFic](https://github.com/syrizelink/OpenFic) Fork，在完整保留上游功能的基础上，额外新增了以下能力。

### 🔷 原生 Google Vertex AI（Gemini）接入

- **原生 Gemini 调用**：`google-vertex` 提供商可直接调用 Vertex AI 上的 Gemini 对话模型，支持流式输出、工具调用、结构化输出与用量统计，不再走 OpenAI 兼容层
- **双认证模式**：支持 ADC（应用默认凭据，从后端运行环境解析）与 Service Account JSON 两种方式；凭据加密保存，全程不落明文，也不会写入日志、Agent 检查点或运行记录
- **连接级项目与区域**：每个连接独立配置 GCP Project ID 与 Location（含 `global`），连接之间互不干扰
- **保存前实机验证**：可对指定模型发起一次最小调用完成连接验证，失败时返回脱敏的分级错误提示
- **多端一致**：浏览器、桌面本地模式、桌面远程模式共用同一套 Provider、认证与调用链路
- **暂不包含**：Vertex Embedding（向量检索）为第二阶段规划，尚未接入；`google-vertex-anthropic` 尚未原生支持

> 配置与部署说明见 [docs/guides/VERTEX_CONFIGURATION_GUIDE.md](./docs/guides/VERTEX_CONFIGURATION_GUIDE.md)。

### 🎚️ 智能体级模型思考强度

- **独立配置**：每个内置或自定义智能体（Build、Plan、Explore、Composer、Auditor、Writer、Reviewer、Actor 及自建智能体）可在模型之外单独设置思考强度
- **七档策略**：跟随会话 / 父智能体（Inherit）、关闭（Off）、低、中、高、超高、最大
- **主智能体**：智能体默认配置对新建会话生效，同时保留聊天窗口的会话级临时覆盖；覆盖只影响当前会话，切换主智能体或新建会话自动恢复默认
- **子智能体**：可继承调度它的主智能体当前实际使用的强度，也可显式关闭或覆盖为固定档位
- **会话快照**：已打开及历史会话沿用创建时解析出的真实配置，修改智能体默认值不会静默改写正在进行的会话
- **Provider 自适应**：档位按目标模型能力自动适配（如三档接口会把「超高 / 最大」归一为「高」），无需手动换算


## 快速开始

### 🐳 Docker（推荐）

如果使用容器方式安装进行自托管是推荐的安装方式。

```bash
docker run -d -p 8000:8000 -v "openfic:/data" --name openfic ghcr.io/syrizelink/openfic:latest
```


### 🐍 Python pip

> [!Warning]  
> 在开始前，确保你已经安装了Python3.12+

#### 1. 安装OpenFic

```bash
pip install openfic
```

#### 2. 启动服务

```bash
openfic serve
```


### 🖥桌面应用（源项目）

前往 [Release Page](https://github.com/syrizelink/OpenFic/releases) 下载桌面应用，在你的系统上原生运行，而无需额外步骤。

## 贡献

欢迎提交任何形式的贡献！如果你有想法、建议或代码改进，欢迎提交 Issue 或 Pull Request。

- **报告 Bug**：如果你发现了任何问题，请在 Issues 中描述详细情况
- **提出功能需求**：有更好的功能想法？在 Issues 中分享你的需求
- **提交代码**：Fork 本仓库，修改代码后提交 Pull Request

## Star History

<a href="https://www.star-history.com/?repos=syrizelink%2FOpenFic&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=syrizelink/OpenFic&type=date&theme=dark&legend=top-left&sealed_token=JHQpP1A05gPA9RleC2GLLnXJ5mg_nQHq_VosoaeQPU2yPGneRUJNEyxaEy--2atezknlCUb5HxLE0HB31gJAOr1ezJZHYW92VUSlWh0Ej0bkt4Q3AWVUHQ" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=syrizelink/OpenFic&type=date&legend=top-left&sealed_token=JHQpP1A05gPA9RleC2GLLnXJ5mg_nQHq_VosoaeQPU2yPGneRUJNEyxaEy--2atezknlCUb5HxLE0HB31gJAOr1ezJZHYW92VUSlWh0Ej0bkt4Q3AWVUHQ" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=syrizelink/OpenFic&type=date&legend=top-left&sealed_token=JHQpP1A05gPA9RleC2GLLnXJ5mg_nQHq_VosoaeQPU2yPGneRUJNEyxaEy--2atezknlCUb5HxLE0HB31gJAOr1ezJZHYW92VUSlWh0Ej0bkt4Q3AWVUHQ" />
 </picture>
</a>

## Repobeats

![Repobeats](https://repobeats.axiom.co/api/embed/a3b67d74bb71044ef2385d65bc469090ee3e0fe6.svg "Repobeats analytics image")

## 致谢

- [SillyTavern](https://github.com/SillyTavern/SillyTavern) - 灵感来源
- [oh-story-claudecode](https://github.com/worldwonderer/oh-story-claudecode) - 内置写作Skill参考

## 社区

[LINUX DO](https://linux.do/)

交流群：[1105304435](https://qun.qq.com/universal-share/share?ac=1&authKey=XxKBo33K1IAy%2FejDsGPWOn51pCNk1Bu1%2F2dtldtWCWSdPGor4tZkaboxgrGkz2BS&busi_data=eyJncm91cENvZGUiOiIxMTA1MzA0NDM1IiwidG9rZW4iOiJCY1NuV2s5d1B2QmI2R0ZiMldMbDE4MVRPV1puMFJlWjZIRlZrRjk4WGUwY2wvdUlaWEFPZ1cvV0lLbWl6d3JwIiwidWluIjoiMjUzMjEyNDQwNCJ9&data=B2VDUuvIYSScsKPwMeFB6txn6fj8I18zG6EKsmsrZDwPpmNCoJ7r5NTLtmUUf58MK3Lw9evkkPg28EglHJNONA&svctype=4&tempid=h5_group_info)


## 许可证

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
