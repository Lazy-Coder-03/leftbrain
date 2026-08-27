# MCP clients

leftbrain is a standard MCP server over Streamable HTTP. Point any client at `https://leftbrain.idlesync.in/mcp` with a bearer header. `/external/mcp` adds the network tools (weather, FX rates, geocoding, URL checks).

A key limited to specific tools on the [Keys page](/dashboard) simply lists fewer tools in the client; a call outside its scope comes back as `{"ok": false, "error": "forbidden", …}` naming what the key may call, so an agent reads it and stops rather than retrying.

<div class="callout">Would rather not edit config files? The quickstart has a <a href="/docs/quickstart#set-it-up-for-me">copy-paste prompt</a> that sets any of these clients up for you — it carries the exact format for each one. Writing your own agent instead? See <a href="/docs/custom-agents">Custom agents</a>.</div>

## Claude Code

:::command
:::os
### windows
```powershell
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp `
  --header "Authorization: Bearer $env:LB_KEY"
```
### macos
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp \
  --header "Authorization: Bearer $LB_KEY"
```
### linux
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp \
  --header "Authorization: Bearer $LB_KEY"
```
:::
:::

## Claude Desktop, Cursor, VS Code

Add this to the client's MCP config — `claude_desktop_config.json`, `.cursor/mcp.json`, or `.vscode/mcp.json` (VS Code uses `"servers"` instead of `"mcpServers"`):

:::request
```json
{
  "mcpServers": {
    "leftbrain": {
      "type": "http",
      "url": "https://leftbrain.idlesync.in/mcp",
      "headers": { "Authorization": "Bearer lblz_YOUR_KEY" }
    }
  }
}
```
:::

## Claude Desktop, Claude on the web, and ChatGPT

The apps split into two groups, and the line between them is **where the config lives**.

| client | how it connects | works today |
| --- | --- | --- |
| Claude Code | `--header` on the add command | ✅ |
| Claude Desktop, Cursor, VS Code | a config file with `headers` | ✅ |
| Claude on the **web** (claude.ai → Connectors) | a dialog with no header field | ❌ |
| ChatGPT (Settings → Connectors → Create) | **OAuth / No Auth / Mixed** only | ❌ |

An app that reads a config file can send `Authorization: Bearer lblz_…`, which is all leftbrain
wants. An app configured through a *browser dialog* can only offer what the dialog offers, and
neither Claude's web connector form nor ChatGPT's has a place to put a static key — they expect
the server to speak OAuth, which leftbrain does not yet ([#34](/docs/tools)).

### If you use Claude on the web

**Use the desktop app for leftbrain.** It is the same account and the same conversations; only
the connector lives locally, in `claude_desktop_config.json` as shown above. This is the shortest
path by a wide margin and needs nothing from us.

### If you need ChatGPT, or the web app specifically

Put something in front of leftbrain that holds the key, and point the connector at *that* with
**No Auth**. A dozen lines on any edge platform:

:::request
```js
// Cloudflare Worker. LEFTBRAIN_KEY is a secret; it never reaches the client.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const target = "https://leftbrain.idlesync.in/mcp" + url.search;
    return fetch(target, {
      method: request.method,
      headers: { ...Object.fromEntries(request.headers), authorization: `Bearer ${env.LEFTBRAIN_KEY}` },
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    });
  },
};
```
:::

The trade is explicit: **the Worker's URL becomes the credential**, because anyone who has it can
call leftbrain as you. So give it a long random hostname or path, put the key it carries on a
tight scope from the [Keys page](/dashboard) — a connector rarely needs all eighteen tools — and
revoke that key rather than the Worker if the URL leaks.

The same shape works on Vercel, Deno Deploy, Fly, or an nginx `proxy_set_header`. What it does
not do is make the problem go away; it moves the secret somewhere a browser dialog can reach.

### The real fix

leftbrain speaking OAuth, so the connector dialogs can do what they are built for. That is
[issue #34](/docs/tools) — the MCP SDK already ships the protocol surface, so it is one provider
class plus token storage rather than an implementation from scratch. Until it lands, the table
above is the honest state of things.

## Python (official `mcp` client)

:::request
```python
import asyncio
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    http = httpx.AsyncClient(headers={"Authorization": "Bearer lblz_YOUR_KEY"})
    url = "https://leftbrain.idlesync.in/mcp"
    async with streamable_http_client(url, http_client=http) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            r = await s.call_tool("numbers", {"mode": "compare", "values": ["9.11", "9.9"]})
            print(r.structured_content["result"]["max"])

asyncio.run(main())
```
:::

:::response
```text
{'input': '9.9', 'value': '9.9'}
```
:::

## No client at all

Install the library and call the same functions locally — no key, no network:

:::command
```bash
pip install leftbrain
```
:::

:::request
```python
from leftbrain.core.numbers import numbers
numbers("compare", values=["9.11", "9.9"])["result"]["max"]
```
:::

:::response
```text
{'input': '9.9', 'value': '9.9'}
```
:::

## Something else

Windsurf, Cline, Continue, Codex CLI, Gemini CLI and the Copilot CLI all speak the same endpoint; the [set-it-up prompt](/docs/quickstart#set-it-up-for-me) carries the exact config format for each. To talk to leftbrain from your own program — in Python, TypeScript, Go, Java, C#, Rust, Swift or Kotlin, with an SDK or with plain HTTP — see [Custom agents](/docs/custom-agents).
