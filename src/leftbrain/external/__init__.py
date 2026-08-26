"""Network-backed tools: weather, FX rates, geocoding, URL checks.

Kept separate from the core because they can fail, are rate-limited, and are
not deterministic. All use free, key-less public APIs.
"""

from .tools import fx_rate, geo, url_check, weather

__all__ = ["fx_rate", "geo", "url_check", "weather"]
