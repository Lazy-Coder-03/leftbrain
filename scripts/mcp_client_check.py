"""Spawn each leftbrain MCP server over stdio, list tools, call one. Run: python scripts/mcp_client_check.py"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def check(module: str, tool: str, args: dict) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", module], env={"PYTHONPATH": str(ROOT / "src"), "LEFTBRAIN_FILE_ROOTS": str(ROOT)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            info = getattr(init, "server_info", None) or init.serverInfo
            print(f"[{module}] server={info.name} v{info.version} tools={names}")
            res = await session.call_tool(tool, args)
            structured = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
            payload = structured if structured is not None else [getattr(c, "text", str(c)) for c in res.content]
            print(f"  call {tool}{json.dumps(args)} -> {json.dumps(payload, default=str)[:400]}")
            is_err = getattr(res, "is_error", None) or getattr(res, "isError", False)
            assert not is_err, "tool call reported an error"


async def main() -> None:
    await check("leftbrain.mcp_server", "math", {"mode": "eval", "expr": "2^10 + 15% of 200"})
    await check("leftbrain.mcp_server", "datetime", {"mode": "convert_tz", "value": "2026-08-26 15:00", "from_tz": "Asia/Kolkata", "to_tz": "America/New_York"})
    await check("leftbrain.mcp_server", "datetime", {"mode": "diff", "start": "2026-08-26", "end": "2026-12-25"})
    await check("leftbrain.mcp_server", "math", {"mode": "integrate", "expr": "x^2", "var": "x", "lower": 0, "upper": 1})
    await check("leftbrain.files.mcp_server", "files", {"mode": "list_dir", "path": "src/leftbrain", "glob": "*.py"})
    await check("leftbrain.external.mcp_server", "fx_rate", {"base": "USD", "to": "INR"})


if __name__ == "__main__":
    asyncio.run(main())
