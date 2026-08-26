"""Start leftbrain-serve with a key store, sign up, and call a tool over Streamable HTTP with the official client.

Run: python scripts/http_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8791


def wait_for(url: str, timeout: float = 30) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise SystemExit(f"server did not start: {url}")


async def call(base: str, key: str) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    try:
        import httpx2 as httpx_mod  # mcp>=2 may vendor httpx as httpx2
    except ImportError:
        import httpx as httpx_mod

    client = httpx_mod.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
    async with streamable_http_client(f"{base}/mcp", http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            r = await s.call_tool("numbers", {"mode": "compare", "values": ["9.11", "9.9"]})
            payload = getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)
            print("numbers.compare ->", json.dumps(payload)[:200])
            assert payload["result"]["max"]["input"] == "9.9"


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="leftbrain_http_")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "LEFTBRAIN_KEYS_DB": str(Path(tmp) / "keys.sqlite3"), "LEFTBRAIN_OPEN_SIGNUP": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "leftbrain.serve", "--port", str(PORT), "--host", "127.0.0.1", "--no-external"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{PORT}"
    try:
        wait_for(f"{base}/healthz")
        print("index:", urllib.request.urlopen(f"{base}/").read().decode()[:200])
        req = urllib.request.Request(f"{base}/keys/signup", data=json.dumps({"email": "dev@example.com"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        signup = json.loads(urllib.request.urlopen(req).read())
        print("signup:", {k: v for k, v in signup.items() if k != "key"})
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/keys/me"))
        except urllib.error.HTTPError as e:
            print("no key ->", e.code)
        asyncio.run(call(base, signup["key"]))
        me = json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/keys/me", headers={"Authorization": f"Bearer {signup['key']}"})).read())
        print("me:", me["result"]["used_today"], "used of", me["result"]["daily_quota"])
        print("HTTP check OK")
        return 0
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0]
        print("--- server log tail ---")
        print("\n".join(out.splitlines()[-8:]))


if __name__ == "__main__":
    sys.exit(main())
