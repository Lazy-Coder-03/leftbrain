"""The MCP server the three leftbrain servers are built on.

`MCPServer` validates `tools/call` arguments against the tool's input schema before the tool
runs, and a rejection leaves as a transport error carrying a pydantic dump. That is the one
answer an agent cannot act on, because it is not the shape every other answer has. This
subclass turns those rejections into the contract envelope instead - see
:func:`leftbrain.contract.schema_rejection`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from pydantic import ValidationError

from . import __version__
from .contract import schema_rejection
from .observe import meta_for

__all__ = ["ContractMCPServer"]


class ContractMCPServer(MCPServer):
    """An `MCPServer` whose schema rejections answer in the leftbrain contract."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        started = time.perf_counter()
        try:
            return self._with_meta(await super().call_tool(name, arguments, context), name, arguments, started)
        except MCPToolError as exc:
            if not isinstance(exc.__cause__, ValidationError):
                raise  # an unknown tool, or the tool's own failure - not ours to reshape
            registered = self._tool_manager.get_tool(name)
            if registered is None:  # pragma: no cover - defensive; validation implies a tool
                raise
            envelope = schema_rejection(name, exc.__cause__.errors())
            return self._with_meta(registered.fn_metadata.convert_result(envelope), name, arguments, started)

    @staticmethod
    def _with_meta(result: Any, name: str, arguments: dict[str, Any], started: float) -> Any:
        """Add `meta` to the envelope, in both the structured result and the text copy."""
        envelope = getattr(result, "structured_content", None)
        meta = meta_for(name, arguments.get("mode"), envelope, started=started, version=__version__)
        if meta is None:
            return result
        envelope["meta"] = meta
        # The text block is the same JSON for clients that only read text, so it has to agree.
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                block.text = json.dumps(envelope, indent=2, default=str)
                break
        return result
