# OpenFic Google Vertex AI 配置与部署指南

本文档为 OpenFic 在不同环境（网页部署、Docker/容器、桌面本地模式、桌面远程模式）下接入原生 Google Vertex AI（Gemini）提供详细配置说明与最佳实践。

---

## 1. 架构与认证模型

OpenFic 原生支持通过 Google Cloud Vertex AI 调用 Gemini 系列大模型，支持两种标准的 GCP 认证方式：

1. **Service Account JSON（服务账号凭据）**：
   - 上传或粘贴由 Google Cloud 控制台生成的专用服务账号 JSON 密钥。
   - 后端使用应用加密服务（Fernet AES-128-CBC + HMAC-SHA256）对凭据进行加密存储，仅在发起调用请求时临时于内存中解析为 Google Credentials 对象。
   - 杜绝生成本地私钥临时文件，API 响应与日志全面脱敏。
2. **Application Default Credentials（ADC，应用默认凭据）**：
   - 依赖被连接的 **Python 后端运行环境** 解析 ADC。
   - 支持后端机器上的 `gcloud auth application-default login`、环境变量 `GOOGLE_APPLICATION_CREDENTIALS`、GCE/GKE 关联的元数据服务（Workload Identity）等。
   - 数据库不保存任何凭据密文，仅保存项目 ID 与区域配置。

### 三种运行模式的认证与执行边界

| 模式 | 后端执行位置 | ADC 凭据来源 | Service Account 保存位置 |
| --- | --- | --- | --- |
| **网页端（连接服务器）** | 部署的服务器 / Docker 容器 | 服务器进程或容器环境的 ADC | 服务器数据目录（加密落库） |
| **桌面本地模式** | Electron 启动的本地 Python 子进程 | 本地操作系统继承的 ADC 环境 | 本地数据目录（`openfic.db`，使用本地 `.key` 加密） |
| **桌面远程模式** | 远程服务器 / 云容器 | 远程后端运行环境的 ADC | 远程后端数据目录（加密落库） |

> [!IMPORTANT]
> **ADC 属于后端环境**：在桌面远程模式或网页远程连接模式下，ADC 必须配置在**远端服务器**上，浏览器或客户端电脑上的本地登录态对远端调用无效。

---

## 2. Google Cloud 准备工作与最小权限

1. **启用 API**：
   在目标 Google Cloud 项目中启用 **Vertex AI API**：
   ```bash
   gcloud services enable aiplatform.googleapis.com --project=<YOUR_PROJECT_ID>
   ```

2. **创建服务账号并授权（最小权限原则）**：
   - 建议为 OpenFic 创建专用的服务账号，例如 `openfic-vertex@<YOUR_PROJECT_ID>.iam.gserviceaccount.com`。
   - 分配预设角色：**Vertex AI User**（`roles/aiplatform.user`）。该角色仅具备在线推理权限，不具备模型删除、端点部署或账单管理权限。
   - 如果使用 Service Account 模式，为该服务账号创建并下载 JSON 密钥。

---

## 3. 服务器与 Docker / 容器环境部署

### 3.1 采用 Service Account 模式

在容器部署时，最便捷的方式是通过 OpenFic 前端界面直接配置：
1. 打开 **设置 -> 模型连接 -> 添加连接**。
2. 提供商选择 `google-vertex`。
3. 填写 **Project ID** 与 **Location**（建议 `us-central1` 或目标模型所在区域）。
4. 认证方式选择 **Service Account JSON**。
5. 点击“选择文件”导入下载的 `.json` 密钥文件（或粘贴 JSON 内容）。
6. 点击“测试连接”验证，验证通过后保存。

### 3.2 采用 ADC 模式（Docker 容器挂载）

若使用 ADC 且后端运行在 Docker 中，需将主机凭据目录挂载进容器，并设置环境变量：

```bash
docker run -d \
  --name openfic-backend \
  -p 8000:8000 \
  -v /path/to/host/adc-key.json:/secrets/gcp-credentials.json:ro \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-credentials.json \
  openfic/backend:latest
```

若在 Google Kubernetes Engine (GKE) 上运行，推荐使用 **Workload Identity** 绑定 Kubernetes ServiceAccount 与 GCP IAM ServiceAccount，无需挂载密钥文件。

### 3.3 环境变量已知边界与避坑

> [!WARNING]
> **禁止在全局环境变量中设置 `GOOGLE_GENAI_USE_VERTEXAI=true`**：
> 某些第三方 SDK 会读取该全局变量，若在进程环境变量中设置，会导致其他使用 Gemini Developer API（`google-genai`）的连接在底层客户端被强制切到 Vertex 端点。
> OpenFic 已在代码层显式隔离各提供商的后端选择，请保持环境干净，不要在 Dockerfile 或系统环境注入此变量。

---

## 4. 桌面端本地模式配置

### 4.1 使用 Service Account 文件导入

1. 打开桌面应用，进入 **设置 -> 模型连接**。
2. 新建或编辑 `google-vertex` 连接。
3. 选择 **Service Account** 认证模式，点击“选择文件”。
4. 文件选择器支持中文路径与空格路径；应用限制文件不超过 64 KiB 并自动提取 `project_id`。
5. 保存后，密钥密文保存在本地 `openfic.db` 中。

### 4.2 使用本地 ADC 环境

1. 在终端中执行 Google Cloud CLI 登录：
   ```bash
   gcloud auth application-default login
   ```
   凭据通常保存在：
   - Windows: `%APPDATA%\gcloud\application_default_credentials.json`
   - macOS / Linux: `~/.config/gcloud/application_default_credentials.json`
2. **完全退出桌面应用并重新启动**：
   因为桌面后端子进程在启动时继承父进程的环境变量，新配置的环境需重启应用后才能生效。
3. 在连接表单中选择 **Application Default Credentials (ADC)**，填写目标项目 ID 与区域，测试连接并保存。

### 4.3 数据备份与迁移注意事项

- 桌面端的**数据备份**功能（生成 `.tar.gz` 压缩包）会同时打包 `openfic.db` 与加密密钥文件 `.key`。
- **备份包含解密密钥**：包含 Service Account 密文的备份压缩包应视同敏感数据妥善保管。
- **还原与密钥匹配**：还原备份时必须确保 `.key` 文件完整恢复。如果仅拷贝数据库而遗失 `.key`（或密钥不匹配），密文将无法解密，系统会安全报错 `vertex_credentials_invalid` 并提示重新配置。
- **ADC 不随备份迁移**：ADC 凭据不属于应用数据目录，不会被打包进备份，迁移到新电脑后需在新电脑上重新配置 ADC。

---

## 5. 桌面端远程模式配置

当桌面客户端连接至远程 OpenFic 服务器时：

1. **凭据提交机制**：
   - 桌面客户端选择本地 Service Account JSON 时，前端会读取文件内容并以文本形式上传至远程服务器。
   - 杜绝将本地文件系统路径（如 `C:\Users\...`）提交给远程服务器，杜绝路径无效或注入安全隐患。
2. **远程 ADC**：
   - 若选择 ADC 认证，ADC 必须存在于远程服务器进程所在环境。
3. **切换服务器**：
   - 切换连接的服务器时，客户端会立即清除所有草稿凭据与连接验证状态，防止状态串用。

---

## 6. 验证与错误排查指南

在连接表单中点击“测试连接”时，后端会发送受限的最小模型调用（`max_tokens=16`，超时 30 秒）。若测试失败，界面会展示以下标准脱敏错误码及针对性排查方向：

| 错误码 | 错误分类 | 常见原因与解决方案 |
| --- | --- | --- |
| `vertex_config_invalid` | 配置无效 | Project ID、Location 格式不合法或缺失；ADC 模式下传入了矛盾的凭据数据。请核对项目 ID 与区域。 |
| `vertex_credentials_missing` | 凭据缺失 | 后端环境中未找到有效 ADC。请在后端机器运行 `gcloud auth application-default login` 或配置 `GOOGLE_APPLICATION_CREDENTIALS`。 |
| `vertex_credentials_invalid` | 凭据无效 | 上传的 Service Account JSON 格式损坏、RSA 私钥无法解析或已被吊销；或备份还原后 `.key` 密钥不匹配导致解密失败。请重新下载并上传有效凭据。 |
| `vertex_permission_denied` | 权限不足 (403) | 账号缺少 `roles/aiplatform.user` 角色；或目标项目未启用 `aiplatform.googleapis.com` API。请在 GCP 控制台核对权限。 |
| `vertex_model_unavailable` | 模型不可用 (404) | 指定的模型 ID 不存在，或该模型在选定 Location（区域）中尚未开放。请尝试更换区域（如 `us-central1`）或核对模型 ID。 |
| `vertex_rate_limited` | 配额超限 (429) | 触发了 Google Cloud 项目配额（RPM/TPM）或并发调用上限。请稍后重试或在 GCP 控制台申请提升配额。 |
| `vertex_timeout` | 请求超时 | 连接 Google Cloud 端点网络超时（超过 30 秒）。请检查服务器对外访问网络或代理配置。 |
| `vertex_response_blocked` | 响应被拦截 | 响应因模型安全策略被拦截，或模型未返回有效输出。请更换测试提示词或检查内容安全设置。 |
