# Quickstart

Three steps: get a key, store it in your shell, call a tool. All examples target the hosted server at **https://leftbrain.idlesync.in**; a self-hosted server uses the same routes on your own host.

## 1 · Get a key

[Sign in with GitHub](/login) and create a key on the Keys page. The free tier gives every key 5,000 calls/day and 60 requests/minute. You'll see the key once — copy it immediately.

## 2 · Store it

:::os
### windows
```powershell
$env:LB_KEY = "lblz_…"
# persist across sessions:
[Environment]::SetEnvironmentVariable("LB_KEY", "lblz_…", "User")
```
### macos
```bash
export LB_KEY="lblz_…"
# persist: add the line to ~/.zshrc
```
### linux
```bash
export LB_KEY="lblz_…"
# persist: add the line to ~/.bashrc or ~/.profile
```
:::

## 3 · Check the key

:::os
### windows
```powershell
# PowerShell aliases 'curl' to Invoke-WebRequest — use curl.exe
curl.exe -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $env:LB_KEY"
```
### macos
```bash
curl -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $LB_KEY"
```
### linux
```bash
curl -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $LB_KEY"
```
:::

```json
{"ok":true,"result":{"prefix":"lblz_qm9DsdMO","daily_quota":5000,"rpm":60,"used_today":12,"remaining_today":4988}}
```

## 4 · Call a tool over MCP

The endpoint speaks **Streamable HTTP (JSON-RPC 2.0)** and is stateless, so you can send `tools/call` directly without opening a session.

:::os
### windows
```powershell
curl.exe -s https://leftbrain.idlesync.in/mcp `
  -H "Authorization: Bearer $env:LB_KEY" -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'

# or natively:
Invoke-RestMethod -Method Post -Uri https://leftbrain.idlesync.in/mcp `
  -Headers @{ Authorization = "Bearer $env:LB_KEY"; Accept = "application/json, text/event-stream" } `
  -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
### macos
```bash
curl -s https://leftbrain.idlesync.in/mcp \
  -H "Authorization: Bearer $LB_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
### linux
```bash
curl -s https://leftbrain.idlesync.in/mcp \
  -H "Authorization: Bearer $LB_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
:::

The response is an SSE stream by default (`content-type: text/event-stream`, one `event:`/`data:` pair); the JSON-RPC result is in the `data:` line and its `structuredContent` is the leftbrain contract:

```json
{"ok":true,"result":{"ascending":[{"input":"9.11","value":"9.11"},{"input":"9.9","value":"9.9"}],"max":{"input":"9.9","value":"9.9"}},"assumptions":[],"warnings":[]}
```

Add `-H "Accept: application/json"` only (no `text/event-stream`) if your server runs with `--json` to get a plain JSON body.

<div class="callout">Every response carries <code>x-ratelimit-remaining-today</code> and <code>x-ratelimit-limit-minute</code> headers. A <code>429</code> includes <code>retry-after</code>.</div>

## Next

[Connect an MCP client](/docs/clients) — Claude Code, Claude Desktop, Cursor, VS Code or the Python client.
