# About this OpenFic fork

This is a personal fork of [syrizelink/OpenFic](https://github.com/syrizelink/OpenFic).

For the general feature list, setup instructions, and usage guide, see the [upstream README](https://github.com/syrizelink/OpenFic#readme). This page only lists changes made in this fork.

[Upstream releases](https://github.com/syrizelink/OpenFic/releases) · [中文](./README.md)

## Changes in this fork

### Models and providers

- The built-in `google-vertex` provider is now natively supported: it can call Gemini chat models on Vertex AI directly, with streaming, tool calling, and structured output, without going through an OpenAI-compatible interface.
- A connection can authenticate with ADC (Application Default Credentials, read from the backend environment) or a Service Account JSON, and configures its own GCP project and region.
- Credentials are stored encrypted, never persisted in plaintext, and never written to logs, agent checkpoints, or run records.
- A connection can be validated before saving by running a minimal call against a chosen model, with redacted error messages on failure.
- Browser, desktop local mode, and desktop remote mode share the same connection and call path.
- Vertex Embeddings (vector retrieval) are not supported yet, and `google-vertex-anthropic` still has no native support.

### Agent settings

- Every built-in or custom agent can set its reasoning effort independently, with seven options: Inherit (follow session / parent agent), Off, Low, Medium, High, Extra High, and Maximum.
- A primary agent's setting is the default for new sessions; a change made in the chat window applies only to the current session and resets when you switch agents or start a new session.
- A subagent can inherit the primary agent's actual effort, turn thinking off, or fix it at one tier.
- Sessions keep the configuration resolved when they were created, so changing agent defaults never affects a session in progress.
- Tiers adapt to each model's capabilities (e.g. three-tier models fold Extra High / Maximum into High).

## Other notes

The project remains licensed under the [Apache License 2.0](./LICENSE). This fork does not publish its own releases and has Issues disabled; changes are tracked in this repository's commit history. Please report upstream problems to the [upstream repository](https://github.com/syrizelink/OpenFic/issues).
