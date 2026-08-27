"""leftbrain - the left brain for your AI agent.

Exact, deterministic answers for everything LLMs are bad at: math, dates,
units, proportional scaling, validation, randomness, and more.

Every tool is a plain Python function that returns the same envelope::

    {"ok": True, "result": ..., "assumptions": [...], "warnings": [...]}

Use them directly from Python, or run ``leftbrain`` as an MCP server.
"""

from .contract import Ambiguous, ToolError, fail, ok
from .core import (
    collections_,
    color,
    convert,
    datetimex,
    encode,
    finance,
    geo_offline,
    holidays_,
    mathx,
    numbers,
    random_,
    scale,
    text,
    validate,
)

__version__ = "0.3.1"

# Friendly aliases: leftbrain.math_tool(...), leftbrain.datetime_tool(...)
math_tool = mathx.math
datetime_tool = datetimex.datetime_tool
scale_tool = scale.scale
convert_tool = convert.convert
holidays_tool = holidays_.holidays
numbers_tool = numbers.numbers
text_tool = text.text
collections_tool = collections_.collections
validate_tool = validate.validate
random_tool = random_.random_tool
geo_tool = geo_offline.geo_offline
encode_tool = encode.encode
finance_tool = finance.finance
color_tool = color.color

TOOLS = {
    "math": math_tool,
    "datetime": datetime_tool,
    "scale": scale_tool,
    "convert": convert_tool,
    "holidays": holidays_tool,
    "numbers": numbers_tool,
    "text": text_tool,
    "collections": collections_tool,
    "validate": validate_tool,
    "random": random_tool,
    "geo_offline": geo_tool,
    "encode": encode_tool,
    "finance": finance_tool,
    "color": color_tool,
}

__all__ = [
    "Ambiguous",
    "ToolError",
    "TOOLS",
    "__version__",
    "collections_tool",
    "color_tool",
    "convert_tool",
    "datetime_tool",
    "encode_tool",
    "fail",
    "finance_tool",
    "geo_tool",
    "holidays_tool",
    "math_tool",
    "numbers_tool",
    "ok",
    "random_tool",
    "scale_tool",
    "text_tool",
    "validate_tool",
]
