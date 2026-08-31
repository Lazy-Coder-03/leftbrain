"""What a key minted by consent is called, the way a messaging app names a linked device.

``Claude Code · Windows`` tells its owner which row to revoke; an opaque identifier does not.
A client running in its vendor's cloud reads ``· web``, never the approving browser's operating
system: that would be a lie someone could act on, revoking ``ChatGPT · Windows`` in the belief
it was tied to their PC. A key minted through the device grant reads ``· device`` for the same
reason: that flow exists so the approval can happen on a different machine from the agent, so
the approving browser says nothing about where the agent runs (#104).
"""

from __future__ import annotations

import re

from .redirects import is_loopback

#: Order matters. An iPhone's user agent says "like Mac OS X" and an Android's says "Linux",
#: so the specific device is tested before the desktop whose name it also carries.
_OS_MARKERS = (
    ("Windows", "Windows"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("Android", "Android"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)

#: The `note` column the dashboard renders as a key's name.
MAX_NAME = 40


def os_from_user_agent(ua: str | None) -> str | None:
    for marker, label in _OS_MARKERS:
        if marker in (ua or ""):
            return label
    return None


def connector_key_name(client_name: str | None, redirect_uris: list[str], user_agent: str | None, *, grant: str = "browser") -> str:
    """``<app> · <where it runs>``, trimmed to fit the note column.

    A client is treated as running on the approver's machine when it registered a loopback
    redirect, which is exactly the distinction that matters: a cloud client's redirect goes
    to its vendor, so the browser that approved says nothing about where the client runs.
    ``grant="device"`` overrides that: the device grant is approved from wherever a browser is,
    which need not be the agent's machine, so the key is named for the grant instead.

    ``client_name`` comes from the client and is therefore untrusted. Runs of whitespace,
    newlines included, collapse to single spaces so it cannot forge a second line; it is
    escaped again wherever it is rendered.
    """
    app = re.sub(r"\s+", " ", (client_name or "").strip()) or "app"
    local = any(is_loopback(u) for u in redirect_uris)
    if grant == "device":
        where = "device"
    else:
        where = (os_from_user_agent(user_agent) or "local") if local else "web"
    return f"{app[: MAX_NAME - len(where) - 3]} · {where}"
