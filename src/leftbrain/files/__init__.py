"""File tools for custom agents: PDF text, image -> base64 data URI, file info.

Filesystem access is restricted to an allowlist of root directories
(``LEFTBRAIN_FILE_ROOTS``, ``;``/``:``-separated; default: current directory).
"""

from .tools import files

__all__ = ["files"]
