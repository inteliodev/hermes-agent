# Upgrade intelio/pinned to Hermes v0.22.3

This branch merges `intelio/pinned` (`e16f94de`) with upstream `v0.22.3`
(`5b147ab036d6d19131bff8d20701b5001eab844a`). It is a pull request. It does not
deploy, tag, or move `intelio/pinned`.

## What stayed

- Claude account-pool failover: bench a rate-limited account, honor reset
  headers, emit `tool.progress` for `provider.account`, and the tests in
  `tests/agent/test_claude_immediate_failover.py` and
  `tests/agent/test_pool_revert_after_cooldown.py`.
- v0.22.3 live MCP progress (`notifications/progress` in
  `tools/mcp_tool_handlers.py`). `tool.progress` SSE events, including the
  account notice, are unchanged for the intelio UI.
- Runtime data paths. Profiles, skills, memories, and sessions still resolve
  through `get_hermes_home()`. Nothing in this change relocates them.

## Conflict resolved

`gateway/platforms/api_server.py` was the only conflict.

- `GET /v1/skills` uses v0.22.3's public `find_all_skills` / `sort_skills`.
  `include_editorial` is gone upstream, so the call is
  `sort_skills(find_all_skills(skip_disabled=False))`.
- `_tool_progress` keeps the v0.22.3 signature and the intelio
  `provider.account` branch that enqueues `tool.progress` with
  `tool_name: "_provider_account"`.

`tests/gateway/test_api_server.py` patches `find_all_skills` to match that call.

## New behavior, all off until configured

| Config | Default | Effect |
|---|---|---|
| `intelio.client` | `""` | One of `intelio`, `PRC`, `Alignment`, `HHP`, `ARLP`. Empty disables the scope block. |
| `intelio.scope_rules` | `""` | Extra scope text appended to the client block. |
| `intelio.jarvis_readonly` | `false` | Also on when the active profile name is `jarvis`. Blocks mutating tools. |
| `langfuse.enabled` | `false` | Self-hosted traces. Also needs `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the profile `.env`. |
| `langfuse.host` | `http://127.0.0.1:3000` | Override with `LANGFUSE_HOST` if needed. |
| `langfuse.redact_categories` | finance, email, texts | Explicit `[]` keeps message text. |
| `claude_task_routing.enabled` | `false` | Small Claude model for classification and summarization. |
| `claude_task_routing.small_model` | `""` | Required when routing is on. Uses the existing Anthropic pool. |
| `claude_task_routing.large_model` | `""` | Documentation only. The session model stays the reasoning model. |

Enable the plugins on the profile you are rolling out (they are opt-in):

```yaml
plugins:
  enabled:
    - intelio-scope
    - intelio-langfuse   # only if Langfuse is up
```

`intelio-scope` re-injects the client block from `post_compaction` via the next
`pre_llm_call`, and registers the same block as a volatile system-prompt
section so a rebuilt prompt after compaction still carries it.

Prompt caching:

- Anthropic requests mark the stable system prefix, the span through the skills
  index, and the tool definitions. The volatile tail (memory, profile,
  timestamp) is unmarked. At most four `cache_control` breakpoints.
- Official `api.openai.com` gets `prompt_cache_key` from the profile name plus
  the session. Other hosts keep the content hash.
- A session stays on its Claude account until that account is rate-limited.
  Preferred-account restoration after a cooldown still runs, and the pin
  follows it.
- Each API turn logs `cache_read_tokens` and `cache_write_tokens`, including zeros.

Langfuse compose: `deploy/langfuse/docker-compose.yml`. Every published port is
`127.0.0.1`. `TELEMETRY_ENABLED` defaults to false. Replace every `# CHANGEME`
value before the first boot. Do not commit real keys.

Evals: `python evals/intelio_grader/grade.py --self-test` and
`npx promptfoo eval -c evals/intelio_grader/promptfooconfig.yaml` from that
directory. The promptfoo provider replays reference outputs. It does not call
a paid model. CI is `.github/workflows/intelio-evals.yml`.

## Safe rollout

Do this for one profile. Leave every other profile on the current pin until
that profile is healthy.

1. Do not restart the gateway yet. Do not run `hermes update` on the VPS.
   `intelio/pinned` moves only when this pull request is merged on purpose.
2. Confirm Hermes is idle. No active turn, no in-flight gateway run, no cron
   fire in progress. `hermes status` (and the gateway status for that profile)
   should show idle. If anything is mid-turn, wait.
3. Pick one profile. Set `intelio.client` for that profile only. Leave
   `langfuse.enabled` and `claude_task_routing.enabled` false for the first boot
   unless you have already started the localhost Langfuse stack and want traces.
4. Enable `intelio-scope` in that profile's `plugins.enabled`.
5. Check idle again. Then restart only that profile's gateway.
6. Send one short turn. Confirm `tool.progress` account notices still arrive,
   the log line `cache_read_tokens=` / `cache_write_tokens=` is present, and a
   second turn on a healthy account stays on the same account label.
7. Roll the remaining profiles only after that one is healthy. Idle-check
   before each gateway restart.

## Rollback

1. Do not merge this pull request, or revert the merge on `intelio/pinned` if
   it was already merged.
2. Confirm Hermes is idle (same check as rollout). A restart during a turn
   drops that turn.
3. Point the process back at `intelio/pinned` commit `e16f94de`. Do not
   `hermes update`.
4. Restart only the profile you moved. The profile's skills, memories, and
   sessions are in the profile home and are not migrated by this change, so
   the old code reads them as before.
5. Leave `langfuse.enabled` and `claude_task_routing.enabled` false. Stopping
   the Langfuse compose (`docker compose -f deploy/langfuse/docker-compose.yml
   down`) does not touch Hermes data. Do not use `down -v` unless you intend
   to delete the local Langfuse volumes.
