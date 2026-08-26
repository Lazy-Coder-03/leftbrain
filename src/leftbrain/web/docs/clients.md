# MCP clients

leftbrain is a standard MCP server over Streamable HTTP. Point any client at `https://leftbrain.idlesync.in/mcp` with a bearer header. `/external/mcp` adds the network tools (weather, FX rates, geocoding, URL checks).

## Claude Code

:::os
### windows
```powershell
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $env:LB_KEY"
```
### macos
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $LB_KEY"
```
### linux
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $LB_KEY"
```
:::

## Claude Desktop, Cursor, VS Code

Add this to the client's MCP config — `claude_desktop_config.json`, `.cursor/mcp.json`, or `.vscode/mcp.json` (VS Code uses `"servers"` instead of `"mcpServers"`):

```json
{
  "mcpServers": {
    "leftbrain": {
      "type": "http",
      "url": "https://leftbrain.idlesync.in/mcp",
      "headers": { "Authorization": "Bearer lblz_…" }
    }
  }
}
```

## Python (official `mcp` client)

```python
import asyncio
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    http = httpx.AsyncClient(headers={"Authorization": "Bearer lblz_…"})
    async with streamable_http_client("https://leftbrain.idlesync.in/mcp", http_client=http) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            r = await s.call_tool("numbers", {"mode": "compare", "values": ["9.11", "9.9"]})
            print(r.structured_content)

asyncio.run(main())
```

## No client at all

Install the library and call the same functions locally — no key, no network:

```bash
pip install leftbrain
```

```python
from leftbrain.core.numbers import numbers
numbers("compare", values=["9.11", "9.9"])
```
