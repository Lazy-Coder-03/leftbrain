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

#: Refusals that describe what the mode that ran expects, and so are the ones a caller can
#: misread when the mode was defaulted rather than chosen (#79).
_MODE_SHAPED = frozenset({"invalid_input", "ambiguous", "unsupported"})


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

    def _default_mode(self, name: str) -> Any:
        """The mode the schema will apply when the caller names none."""
        registered = self._tool_manager.get_tool(name)
        properties = (getattr(registered, "parameters", None) or {}).get("properties", {})
        return (properties.get("mode") or {}).get("default")

    def _with_meta(self, result: Any, name: str, arguments: dict[str, Any], started: float) -> Any:
        """Add `meta` to the envelope, in both the structured result and the text copy.

        A call that names no `mode` gets the schema's default, and that used to be invisible:
        the answer carried no `meta.mode`, and any refusal was phrased in the default mode's
        vocabulary. An agent debugging a `validate` call it believed said `mode: "email"` was
        told it needed `rules` - a parameter with nothing to do with email, which pointed away
        from the real problem for several turns (#79). The mode that ran is now always
        reported, and a refusal from a defaulted mode says that is what happened.
        """
        envelope = getattr(result, "structured_content", None)
        mode = arguments.get("mode")
        defaulted = mode is None and (mode := self._default_mode(name)) is not None
        if defaulted and isinstance(envelope, dict):
            if envelope.get("ok"):
                envelope["assumptions"] = [f"mode not given: {mode}", *(envelope.get("assumptions") or [])]
            elif envelope.get("error") in _MODE_SHAPED and envelope.get("message"):
                # Only where the refusal is about what that mode expects. A `forbidden` never
                # ran the mode at all, and saying it did would be its own wrong answer.
                envelope["message"] = f"no 'mode' was given, so '{mode}' ran: {envelope['message']}"
                envelope.setdefault("hint", f"Pass mode= explicitly if you meant a mode other than '{mode}'.")
        meta = meta_for(name, mode, envelope, started=started, version=__version__)
        if meta is None:
            return result
        envelope["meta"] = meta
        # The text block is the same JSON for clients that only read text, so it has to agree.
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                block.text = json.dumps(envelope, indent=2, default=str)
                break
        return result
