# Quickstart

Two ways in: hand the prompt below to your coding agent, or wire it up yourself in three steps — get a key, store it in your shell, call a tool. All examples target the hosted server at **https://leftbrain.idlesync.in**; a self-hosted server uses the same routes on your own host.

<h2 id="set-it-up-for-me">Set it up for me</h2>

You do two things: [sign in](/login) and create a key, then paste the block below to your
coding agent — Claude Code, Cursor, Windsurf, VS Code, Copilot, Codex, Gemini CLI, Cline,
Continue. It knows the endpoint, the transport, the header, and the config file for each of
them, and it ends by proving the connection works.

```text
Set up the leftbrain MCP server for me.

leftbrain answers, exactly, the things language models get wrong: arithmetic, dates and time
zones, unit and currency conversion, counting, sorting, validation, hashing, seeded randomness.

  Endpoint   https://leftbrain.idlesync.in/mcp           - the 12 core tools
             https://leftbrain.idlesync.in/external/mcp  - weather, FX rates, geocoding, URL checks
  Transport  MCP Streamable HTTP
  Auth       header  Authorization: Bearer <key>

The key: read $LB_KEY from my environment; if it is not set, ask me for it and wait. Never print
it, echo it back, or write it into a file that gets committed — use your client's environment
variable interpolation wherever the format below supports it.

Configure the client this project actually uses. ENDPOINT below is https://leftbrain.idlesync.in/mcp.

  Claude Code      claude mcp add --transport http leftbrain ENDPOINT \
                     --header "Authorization: Bearer $LB_KEY"        (+ --scope user|project)
  Copilot CLI      copilot mcp add --transport http leftbrain ENDPOINT \
                     --header "Authorization: Bearer $LB_KEY"
  Gemini CLI       gemini mcp add --transport http leftbrain ENDPOINT \
                     -H "Authorization: Bearer $LB_KEY"              (+ --scope user)
  Cursor           .cursor/mcp.json (or ~/.cursor/mcp.json), key "mcpServers":
                     "leftbrain": {"url": "ENDPOINT",
                       "headers": {"Authorization": "Bearer ${env:LB_KEY}"}}
  Windsurf         ~/.codeium/windsurf/mcp_config.json, key "mcpServers":
                     "leftbrain": {"serverUrl": "ENDPOINT",
                       "headers": {"Authorization": "Bearer ${env:LB_KEY}"}}
  VS Code          .vscode/mcp.json, key "servers" (not "mcpServers"):
                     "leftbrain": {"type": "http", "url": "ENDPOINT",
                       "headers": {"Authorization": "Bearer ${input:lbKey}"}}
  Cline            cline_mcp_settings.json, key "mcpServers":
                     "leftbrain": {"type": "streamableHttp", "url": "ENDPOINT",
                       "headers": {"Authorization": "Bearer <key>"}}
  Continue         .continue/mcpServers/leftbrain.yaml:
                     mcpServers:
                       - name: leftbrain
                         type: streamable-http
                         url: ENDPOINT
                         requestOptions:
                           headers: {Authorization: "Bearer ${LB_KEY}"}
  Codex CLI        ~/.codex/config.toml:
                     [mcp_servers.leftbrain]
                     url = "ENDPOINT"
                     bearer_token_env_var = "LB_KEY"
                     (older Codex also needs [features] experimental_use_rmcp_client = true)
  Claude Desktop   a static key needs the mcp-remote bridge; claude_desktop_config.json:
                     "leftbrain": {"command": "npx", "args": ["-y", "mcp-remote", "ENDPOINT",
                       "--header", "Authorization:${AUTH_HEADER}"],
                       "env": {"AUTH_HEADER": "Bearer <key>"}}
                     (the space goes in the env var, not in the --header argument)

If my client is not on that list, or a format above does not load, check that client's own MCP
documentation: the endpoint, the transport and the header are everything it needs.

Then verify. Reload the client, list the leftbrain tools, and call the `numbers` tool with
{"mode": "compare", "values": ["9.11", "9.9"]}. It must report 9.9 as the larger value. Tell me
how many tools you found and what that call returned.
```

Prefer to wire it up yourself? The rest of this page is the manual route.

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
{
  "ok": true,
  "result": {
    "prefix": "lblz_qm9DsdMO",
    "daily_quota": 5000,
    "rpm": 60,
    "used_today": 12,
    "remaining_today": 4988
  }
}
```

<h2 id="call-a-tool-over-mcp">4 · Call a tool over MCP</h2>

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
{
  "ok": true,
  "result": {
    "ascending": [
      {"input": "9.11", "value": "9.11"},
      {"input": "9.9", "value": "9.9"}
    ],
    "max": {"input": "9.9", "value": "9.9"}
  },
  "assumptions": [],
  "warnings": []
}
```

Add `-H "Accept: application/json"` only (no `text/event-stream`) if your server runs with `--json` to get a plain JSON body.

<div class="callout">Every response carries <code>x-ratelimit-remaining-today</code> and <code>x-ratelimit-limit-minute</code> headers. A <code>429</code> includes <code>retry-after</code>.</div>

## Next

[Connect an MCP client](/docs/clients) — Claude Code, Claude Desktop, Cursor, VS Code or the Python client.

[Build your own agent](/docs/custom-agents) — the raw protocol, an MCP client in eight languages, and the framework wiring.
