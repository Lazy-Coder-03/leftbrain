"""The MCP server the three leftbrain servers are built on.

`MCPServer` validates `tools/call` arguments against the tool's input schema before the tool
runs, and a rejection leaves as a transport error carrying a pydantic dump. That is the one
answer an agent cannot act on, because it is not the shape every other answer has. This
subclass turns those rejections into the contract envelope instead - see
:func:`leftbrain.contract.schema_rejection`.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from pydantic import ValidationError

from .contract import schema_rejection

__all__ = ["ContractMCPServer"]


class ContractMCPServer(MCPServer):
    """An `MCPServer` whose schema rejections answer in the leftbrain contract."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except MCPToolError as exc:
            if not isinstance(exc.__cause__, ValidationError):
                raise  # an unknown tool, or the tool's own failure - not ours to reshape
            registered = self._tool_manager.get_tool(name)
            if registered is None:  # pragma: no cover - defensive; validation implies a tool
                raise
            envelope = schema_rejection(name, exc.__cause__.errors())
            return registered.fn_metadata.convert_result(envelope)
