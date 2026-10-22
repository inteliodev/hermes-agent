---
title: "Intelio fork settings"
description: "Client scope, knowledge packs, routines, and Claude task routing on the intelio fork"
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
plugin re-injects the client block, the scope rules, the Jarvis
read-only rule, and that client's knowledge pack.

`intelio.knowledge` defaults to true. With a known `intelio.client`, the
scope block appends that client's versioned pack (decisions, SOPs,
templates, contacts, policies) from `intelio/knowledge/<client>/`. The same
text is re-injected after compaction. Set `intelio.knowledge` to false to
leave the pack out. A profile overlay is used only when
`$HERMES_HOME/knowledge/<client>/pack.json` already exists. The eval grader
treats each pack's desk code and token as ground truth, and treats another
client's token or desk code in an answer as a leak.

`intelio.coordinator` is false by default. `intelio.owner` defaults to
Hayden. Enable the `intelio-coordinator` plugin to add the front-door
prompt. It routes one task to one client specialist and hands the result
back. A task that names two clients is refused, and no pack is loaded until
one client is named.

`intelio.routines_enabled` is false by default. `intelio.digests` is false
by default and is the per-profile opt-in for the weekday morning digest.
Enable the `intelio-routines` plugin, set `intelio.client`, then set the
flag on that profile. The host gateway's cron ticker installs that profile's
jobs on boot, including a secondary profile, before the first due scan. A
session is not required. `intelio.digests` adds one weekday 08:00 draft for
that client only. `intelio.routines_enabled` schedules the other routines
already stored for that same client. A profile that has not enabled the
plugin gets no new job.

Delivery is `local`. The draft is saved in the profile's cron output and
copied to `$HERMES_HOME/intelio/digests/<client>/`. Nothing is texted or
emailed, and approving a draft still does not send. A digest reads that
profile's client pack and that profile's `intelio/digest-sources.json`.
Another client's pack token or desk code is left out. Notes whose category
is Finance or Messages and whose owner is Hayden, or whose owner is blank,
are left out. A profile with no `intelio.client` gets no digest.

`intelio.improve` is false by default. Enable the `intelio-improve` plugin
so a message that starts with `correction:` proposes a skill or memory
update. The proposal is not applied and nothing is sent.

```yaml
intelio:
  client: ""
  scope_rules: ""
  jarvis_readonly: false
  knowledge: true
  coordinator: false
  owner: Hayden
  routines_enabled: false
  digests: false
  improve: false
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
