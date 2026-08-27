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

## Claude on the web

Which client can carry a key comes down to **where its configuration lives**.

| client | how it connects | works today |
| --- | --- | --- |
| Claude Code | `--header` on the add command | ✅ |
| Claude Desktop, Cursor, VS Code | a config file with `headers` | ✅ |
| Claude on the **web** (claude.ai → Connectors) | a dialog with no header field | ❌ |
| ChatGPT (Settings → Plugins → Create) | **OAuth / No Auth / Mixed** only — [see below](#chatgpt) | ❌ |

An app that reads a config file can send `Authorization: Bearer lblz_YOUR_KEY`, which is all leftbrain
wants. An app configured through a *browser dialog* can only offer what the dialog offers, and
neither Claude's web connector form nor ChatGPT's has a place to put a static key — both expect
the server to speak OAuth, which leftbrain does not yet.

**Use the desktop app for leftbrain.** It is the same account and the same conversations; only
the connector lives locally, in `claude_desktop_config.json` as shown above. This is the shortest
path by a wide margin and needs nothing from us.

## ChatGPT

ChatGPT calls these **Plugins** now — the July 2026 rename of Connectors — and a custom one is a
remote MCP server, which is exactly what leftbrain is. Streamable HTTP and SSE are both
supported, so the transport is not the problem.

**Two things gate it.**

**1. Developer mode.** Custom plugins are behind it, on Plus, Pro, Team, Enterprise and Edu.
Settings → Connectors → Advanced → **Developer mode**. On Enterprise and Edu an admin enables it
at Workspace Settings → Permissions & Roles → *Create custom MCP connectors*. Then Settings →
Plugins → **Create**, which asks for an icon, a name, a description, the server URL and the
authentication.

**2. Authentication, which is where leftbrain stops.** The dialog offers **OAuth**, **No Auth**
and **Mixed** — there is no field for a key, and that is deliberate rather than an oversight.
OpenAI's own documentation is explicit: ChatGPT *"does not support machine-to-machine OAuth
grants such as client credentials, service accounts, or JWT bearer assertions, nor can it
present custom API keys"*. An authenticated server is expected to implement OAuth 2.1 against
the MCP authorization spec — Client ID Metadata Documents, dynamic client registration, PKCE.

leftbrain authenticates with a bearer key and does not speak OAuth yet, so **there is no
configuration of leftbrain and ChatGPT that works directly today.** Anyone who tells you to paste
the key somewhere in that dialog is describing a field that does not exist.

### What does work

Put something in front of leftbrain that holds the key, and point the plugin at *that* with
**No Auth**. A dozen lines on any edge platform:

:::request
```js
// Cloudflare Worker. LEFTBRAIN_KEY is a secret; it never reaches ChatGPT.
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
call leftbrain as you. So give it a long random hostname or path, put a tightly scoped key behind
it from the [Keys page](/dashboard) — a plugin rarely needs all eighteen tools, and scoping it is
two clicks — and revoke that key rather than the Worker if the URL leaks.

The same shape works on Vercel, Deno Deploy, Fly, or an nginx `proxy_set_header`. It does not
make the problem go away; it moves the secret somewhere a browser dialog can reach.

Once connected, ChatGPT lists each tool separately and lets you toggle them per plugin, and
**Refresh** re-reads the server after leftbrain adds a tool or changes a description.

### The real fix

leftbrain speaking OAuth 2.1 against the MCP authorization spec, so the dialog can do what it is
built for. The MCP SDK already ships the protocol surface — metadata, `/authorize`, `/token`,
dynamic client registration, PKCE — so it is one provider class and token storage rather than an
implementation from scratch. Until then, the proxy above is the honest answer.

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
