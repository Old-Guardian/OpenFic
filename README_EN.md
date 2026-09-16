# OpenFic

![GitHub Repo stars](https://img.shields.io/github/stars/syrizelink/OpenFic)
![License](https://img.shields.io/badge/License-Apache_2.0-red)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![GitHub Release](https://img.shields.io/github/v/release/syrizelink/OpenFic?logo=githubactions&logoColor=white&color=yellow)
![Release Downloads](https://img.shields.io/github/downloads/syrizelink/OpenFic/total?logo=github&logoColor=white&label=Release%20downloads&color=yellow)
![PyPI - Version](https://img.shields.io/pypi/v/openfic?logo=pypi&logoColor=white&color=green)
[![交流群](https://img.shields.io/badge/交流群-1105304435-12B7F5?logo=qq&logoColor=white)](https://qun.qq.com/universal-share/share?ac=1&authKey=XxKBo33K1IAy%2FejDsGPWOn51pCNk1Bu1%2F2dtldtWCWSdPGor4tZkaboxgrGkz2BS&busi_data=eyJncm91cENvZGUiOiIxMTA1MzA0NDM1IiwidG9rZW4iOiJCY1NuV2s5d1B2QmI2R0ZiMldMbDE4MVRPV1puMFJlWjZIRlZrRjk4WGUwY2wvdUlaWEFPZ1cvV0lLbWl6d3JwIiwidWluIjoiMjUzMjEyNDQwNCJ9&data=B2VDUuvIYSScsKPwMeFB6txn6fj8I18zG6EKsmsrZDwPpmNCoJ7r5NTLtmUUf58MK3Lw9evkkPg28EglHJNONA&svctype=4&tempid=h5_group_info)

[中文](./README.md) | English

**OpenFic** is an all-in-one, cross-platform, AI-native writing tool built for fiction authors. It helps you build world, design characters, and shape custom workflows, so the Agent fits your writing process instead of forcing you into its own.

![Demo Screenshot](./demo.png)

## When to Use OpenFic

> [!Tip]  
> *OpenFic is designed for Agent-assisted writing, not one-click novel generation. It is first and foremost a writing tool for fiction, and then an AI Agent system built around that workflow.*

#### It works well when you:

- are writing a mid-length or long-form novel and need to keep track of worldbuilding, characters, foreshadowing, and chapter details
- want an Agent to help with brainstorming, continuity checks, and detail expansion
- already have your own setting, tone, and plot direction, and want help turning ideas into actual prose
- want to customize prompts, Agents, and workflows around your own writing process
- care about local data storage, context management, and sustainable long-term collaboration

#### It is probably not a good fit when you:

- expect to type one prompt and get a complete novel automatically
- mainly need short-form marketing copy, social posts, or generic one-off text generation
- do not plan to maintain detailed project material or long-term writing context

## Features

- 🚀 **Ready out of the box**: install with Docker or pip, or use the desktop app directly, with minimal setup
- ✒️ **Built for writing**: an editor designed around fiction writing, with a comfortable long-form writing experience
- 🤝 **Broad model support**: works with many providers, including any model compatible with the OpenAI API
- 📱 **Responsive UI**: designed for desktop, mobile, and browser use without breaking the workflow
- 🧩 **Custom workflows**: a highly configurable Agent system that lets you adapt prompts and workflows to your needs
- 🤖 **Human-AI co-writing**: Agents help with brainstorming, plotting, and editing, instead of replacing the writing process with one-click generation
- 💾 **Local persistence**: all project data stays on your machine, with no cloud storage dependency
- 🧠 **Semantic retrieval**: Agentic RAG built on vector search, so Agents can retrieve information efficiently even in projects with millions of words
- ⚖️ **Cost-aware context handling**: layered context management, smart compression, dynamic truncation, and stable caching to reduce usage cost

## Fork Enhancements

> This repository is a fork of [syrizelink/OpenFic](https://github.com/syrizelink/OpenFic). It keeps every upstream feature and adds the capabilities below.

### 🔷 Native Google Vertex AI (Gemini) support

- **Native Gemini calls**: the `google-vertex` provider can call Gemini chat models on Vertex AI directly, with streaming, tool calling, structured output, and usage stats — no OpenAI-compatible layer involved
- **Two auth modes**: ADC (Application Default Credentials, resolved from the backend environment) or a Service Account JSON. Credentials are stored encrypted and never persisted in plaintext, and never written to logs, agent checkpoints, or run records
- **Per-connection project and region**: each connection configures its own GCP Project ID and Location (including `global`), fully isolated from other connections
- **Validate before saving**: run a minimal live call against a chosen model to verify the connection, with redacted, categorized error messages on failure
- **Consistent across modes**: browser, desktop local mode, and desktop remote mode share the same provider, auth, and call path
- **Not yet supported**: Vertex Embeddings (vector retrieval) are planned for a second phase; `google-vertex-anthropic` is not natively supported yet

> See [docs/guides/VERTEX_CONFIGURATION_GUIDE.md](./docs/guides/VERTEX_CONFIGURATION_GUIDE.md) for setup and deployment details.

### 🎚️ Agent-level reasoning effort

- **Per-agent configuration**: every built-in or custom agent (Build, Plan, Explore, Composer, Auditor, Writer, Reviewer, Actor, and your own) can set its reasoning effort independently of its model
- **Seven options**: Inherit (follow the session / parent agent), Off, Low, Medium, High, Extra High, Maximum
- **Primary agents**: the agent default applies to new sessions, with a temporary per-session override from the chat window; an override affects only the current session and resets when you switch agents or start a new session
- **Subagents**: inherit the primary agent's actual effort, turn it off explicitly, or override it with a fixed tier
- **Session snapshots**: open and historical sessions keep the configuration resolved at creation time, so changing agent defaults never silently rewrites an in-progress session
- **Provider-aware**: tiers adapt to each model's capabilities (e.g. three-tier endpoints fold Extra High / Maximum into High) with no manual conversion

## Quick Start

### 🐳 Docker (Recommended)

If you are self-hosting, Docker is the recommended way to run OpenFic.

```bash
docker run -d -p 8000:8000 -v "openfic:/data" --name openfic ghcr.io/syrizelink/openfic:latest
```

### 🐍 Python pip

> [!WARNING]  
> Before you start, make sure Python 3.12+ is installed.

#### 1. Install OpenFic

```bash
pip install openfic
```

#### 2. Start the server

```bash
openfic serve
```

### 🖥 Desktop App

Download the desktop app from [the Release Page](https://github.com/syrizelink/OpenFic/releases) and run it natively on your system.

## Contributing

Contributions of any kind are welcome. If you have ideas, suggestions, or code improvements, feel free to open an Issue or submit a Pull Request.

- **Report bugs**: open an Issue with as much detail as possible
- **Suggest features**: share your ideas in Issues
- **Submit code**: fork the repository, make your changes, and open a Pull Request

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed contribution guidelines.

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

## Acknowledgements

- [SillyTavern](https://github.com/SillyTavern/SillyTavern) - inspiration
- [oh-story-claudecode](https://github.com/worldwonderer/oh-story-claudecode) - reference for the built-in writing Skill

## Community

[LINUX DO](https://linux.do/)

交流群：[1105304435](https://qun.qq.com/universal-share/share?ac=1&authKey=XxKBo33K1IAy%2FejDsGPWOn51pCNk1Bu1%2F2dtldtWCWSdPGor4tZkaboxgrGkz2BS&busi_data=eyJncm91cENvZGUiOiIxMTA1MzA0NDM1IiwidG9rZW4iOiJCY1NuV2s5d1B2QmI2R0ZiMldMbDE4MVRPV1puMFJlWjZIRlZrRjk4WGUwY2wvdUlaWEFPZ1cvV0lLbWl6d3JwIiwidWluIjoiMjUzMjEyNDQwNCJ9&data=B2VDUuvIYSScsKPwMeFB6txn6fj8I18zG6EKsmsrZDwPpmNCoJ7r5NTLtmUUf58MK3Lw9evkkPg28EglHJNONA&svctype=4&tempid=h5_group_info)

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
