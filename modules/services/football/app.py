"""Football: pick an Ace Stream match on the phone, play it on the TV's Kodi.

The page takes an Ace Stream ID (or an acestream:// link, or a URL with id=),
wakes the TV and opens Kodi through Home Assistant, then tells Kodi to play
the stream of the Ace Stream engine running on this server. Pure helpers come
first; the I/O and the web server are below them.
"""

import re

ID_RE = re.compile(r"(?:^|://|id=)([0-9a-fA-F]{40})(?![0-9a-fA-F])")


def parse_id(text):
    """The 40-hex content ID in a pasted ID or link, lowercased, or None"""
    m = ID_RE.search((text or "").strip())
    return m.group(1).lower() if m else None


def stream_urls(content_id, engine_base):
    base = engine_base.rstrip("/")
    return {
        "tv": f"{base}/ace/getstream?id={content_id}",
        "hls": f"{base}/ace/manifest.m3u8?id={content_id}",
    }


def add_recent(recent, content_id, label, now, limit=8):
    """Newest first, no duplicates, at most `limit` entries"""
    rest = [r for r in recent if r["id"] != content_id]
    return [{"id": content_id, "label": label, "at": now}] + rest[: limit - 1]
