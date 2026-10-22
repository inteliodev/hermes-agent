---
title: "Intelio fork settings"
description: "Client scope, Langfuse traces, and Claude task routing on the intelio fork"
sidebar_position: 3
---

# Intelio fork settings

These keys exist only on the intelio fork. Each one is off until you set it
in that profile's `config.yaml`. Secrets stay in the profile `.env`.

## Client scope

`intelio.client` is one of `intelio`, `PRC`, `Alignment`, `HHP`, or `ARLP`.
An empty `intelio.client` leaves the scope block out of the prompt.
`intelio.scope_rules` is extra scope text appended to that block.
`intelio.jarvis_readonly` blocks mutating tools. The same block applies when
the active profile is named `jarvis`.

Enable the `intelio-scope` plugin for the profile. After compaction the
plugin re-injects the client block, the scope rules, and the Jarvis
read-only rule.

```yaml
intelio:
  client: ""
  scope_rules: ""
  jarvis_readonly: false
```

## Langfuse

`langfuse.enabled` is false by default. Traces also need
`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the profile `.env`.
`langfuse.host` defaults to `http://127.0.0.1:3000`. `LANGFUSE_HOST`
overrides it. `langfuse.redact_categories` defaults to finance, email, and
texts; an explicit empty list keeps message text.

The compose file is `deploy/langfuse/docker-compose.yml`. Published ports
bind to localhost only. Enable the `intelio-langfuse` plugin only after
that stack is up.

```yaml
langfuse:
  enabled: false
  host: "http://127.0.0.1:3000"
  redact_categories:
    - finance
    - email
    - texts
```

## Claude task routing

`claude_task_routing.enabled` is false by default. When it is on and
`claude_task_routing.small_model` names a Claude model, classification and
summarization use that model through the existing Anthropic accounts.
`claude_task_routing.large_model` is documentation only. The session model
stays the reasoning model. No new provider is added. An explicit auxiliary
task model still wins.

```yaml
claude_task_routing:
  enabled: false
  small_model: ""
  large_model: ""
```
