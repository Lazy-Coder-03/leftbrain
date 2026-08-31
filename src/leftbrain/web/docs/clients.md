# MCP clients

leftbrain is a standard MCP server over Streamable HTTP. Point any client at `https://leftbrain.idlesync.in/mcp` with a bearer header. `/external/mcp` adds the network tools (weather, FX rates, geocoding, URL checks).

A key limited to specific tools on the [Keys page](/dashboard) simply lists fewer tools in the client; a call outside its scope comes back as `{"ok": false, "error": "forbidden", …}` naming what the key may call, so an agent reads it and stops rather than retrying.

<div class="callout">Would rather not edit config files? The quickstart has a <a href="/docs/quickstart#set-it-up-for-me">copy-paste prompt</a> that sets any of these clients up for you — it carries the exact format for each one. Writing your own agent instead? See <a href="/docs/custom-agents">Custom agents</a>.</div>

## Connect Claude Code

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

**Or connect with OAuth**, if you would rather not handle a key at all. Add it with no header:

:::command
```
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp
```
:::

`claude mcp list` will show it as **Needs authentication**. Run `/mcp` in a session, pick
leftbrain, and authenticate — a browser opens leftbrain's consent page. Approve, and the tools
appear. Your config holds only the URL; the credential lives with Claude Code, and the key it
created is on your [dashboard](/dashboard) as `Claude Code · <your OS>`.

If `claude mcp list` shows it as **Disabled for this project** instead, Claude Code has
remembered a server of the same name that was removed or disabled here before; the server has
not been asked anything yet. Run `/mcp`, enable it, and then authenticate as above.

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

**Or leave the `headers` block out.** Cursor, VS Code and Windsurf will find leftbrain's OAuth
metadata, and offer to sign you in the first time they connect:

:::request
```json
{
  "mcpServers": {
    "leftbrain": {
      "type": "http",
      "url": "https://leftbrain.idlesync.in/mcp"
    }
  }
}
```
:::

Where the prompt appears differs between them — a notification, a "needs authentication" badge on
the server, or a button in the MCP panel. All three end at the same leftbrain consent page.

<h2 id="two-ways-to-connect">Two ways to connect</h2>

**Paste a key.** Create one on the [Keys page](/dashboard), copy it, and put it in your client's
config — the sections above. Works with every client, and it is the only way for a client with no
OAuth support.

**Or connect with OAuth.** Click connect in the app and approve leftbrain's page. You never see or
handle a key: leftbrain **creates a key** for you, names it after the app and the machine it runs on
(`Claude Code · Windows`, `ChatGPT · web`), and shows it on your [dashboard](/dashboard) where you can
read it, change its tools, or revoke it exactly like one you made yourself. It uses one of your key
slots, and revoking it cuts that app off on its next call.

**Then narrow it.** Connect with everything allowed, use it for a day, and then open that key on
your dashboard and choose **Edit scope**. Every tool shows how many times this key has actually
called it — untick the ones sitting at zero. It applies to the connector's very next call: nothing
to reconnect and nothing to re-approve. Doing it in that order matters, because "which tools will
this need?" is not a question anyone can answer before they have watched it work.

| client | key in a config file | OAuth |
| --- | --- | --- |
| Claude Code | ✅ | ✅ |
| Claude Desktop, Cursor, VS Code | ✅ | ✅ |
| Claude on the **web** | a header entered by an org admin (beta) | ✅ |
| ChatGPT | ❌ — no key field, no header editor | ✅ |

## Connect Claude on the web

Claude.ai → **Settings → Connectors → Add custom connector**, and enter
`https://leftbrain.idlesync.in/mcp`. Leave the OAuth Client ID and Secret fields empty — they are
for a server that issues you credentials by hand, and leftbrain does not need them. Claude reads
the discovery documents itself.

Approve the leftbrain page that opens. The key it creates reads `Claude · web`, because the
connector runs on Anthropic's servers rather than on the machine you approved from.

An organisation admin can instead paste a key as a request header when adding the connector —
Anthropic's `static_headers`, in beta at the time of writing — which shares one credential across
the whole organisation. OAuth gives each person their own, revocable on its own.

## Connect ChatGPT

ChatGPT calls these **Plugins** — the July 2026 rename of Connectors — and a custom one lives behind
developer mode: **Settings → Connectors → Advanced**. On Enterprise or Edu an admin enables it first,
under *Create custom MCP connectors*.

ChatGPT is the one client that cannot take a key at all: its dialog offers **OAuth**, **No Auth** or
**Mixed**, with no field for a header. OpenAI's documentation says so outright — it *"does not support
machine-to-machine OAuth grants such as client credentials, service accounts, or JWT bearer
assertions, nor can it present custom API keys"*. So OAuth is the only route, and it now works.

1. **Settings → Connectors → Create**, and enter `https://leftbrain.idlesync.in/mcp`.
2. Choose **OAuth**. ChatGPT reads leftbrain's discovery documents and fills the rest in itself —
   there is nothing to copy from anywhere.
3. Approve the leftbrain page that opens. Sign in first if you are not already.
4. Pick the tools it may use, or leave them all ticked and narrow it later.

Your dashboard will show a new key called **ChatGPT · web**. It reads `web` rather than an operating
system because ChatGPT runs on OpenAI's servers, not on the machine you approved from — saying
`ChatGPT · Windows` there would be a lie you could act on.

## Connect from a terminal

For an agent on a machine with no browser — an SSH session, a container, a CI runner — leftbrain
supports the OAuth **device grant** (RFC 8628), the same flow `gh auth login` and `docker login`
use. The agent asks for a code and shows it to you:

:::response
```
To connect leftbrain, visit:  https://leftbrain.idlesync.in/device
Enter code:  WXYZ-1234
Waiting…
```
:::

You open that page in any browser — your phone will do — sign in, and approve. The agent picks up a
token within a few seconds. Nothing secret crosses the conversation: the code is worthless until a
signed-in human approves it, and it expires in ten minutes. That is the point of using it instead of
pasting a key into a chat window, where the key ends up in scrollback and in whatever logs sit
behind the model.

**No shipping client drives it yet.** Claude Code, Cursor and the rest do not offer the device flow
for MCP servers today, so this is for an agent calling the endpoints itself — see
[the agent guide](/docs/agents/auth) for the exact requests. It is here so that agents can, and so
that clients work the day they add support.

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
