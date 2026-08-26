# MCP clients

leftbrain is a standard MCP server over Streamable HTTP. Point any client at `https://leftbrain.idlesync.in/mcp` with a bearer header. `/external/mcp` adds the network tools (weather, FX rates, geocoding, URL checks).

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
