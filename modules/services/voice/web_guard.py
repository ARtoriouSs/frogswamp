"""Claude Code PreToolUse hooks that keep home data off the web. Anything unexpected blocks
the tool call (exit code 2)

fetch: WebFetch may only open pages that came back in this session's own WebSearch results.
  Claude can't build a URL itself, so it can't put home data into a link, even if a page tells it to
home: Home Assistant tools
  - only tools of known integrations; a new integration can add tools past HA's exposure settings
  - no network URLs in any argument; Music Assistant would fetch them from the server
  - no control of the home in a request where Claude has already read the web, so a web page
    can't steer the devices. Reading the home stays allowed
"""

import json
import re
import sys

LINKS_PREFIX = "Links: "
HA_PREFIX = "mcp__homeassistant__"
HA_INTEGRATIONS = {
    "homeassistant", "llm", "intent", "intent_script", "climate", "light",
    "media_player", "script", "todo", "calendar", "assist_satellite",
}
HA_READ_TOOLS = {
    "homeassistant__GetLiveContext", "llm__GetDateTime", "calendar__get_events", "todo__get_items",
    "media_player__search_media", "intent_script__FrogRoomTemperature",
}
WEB_TOOLS = {"WebSearch", "WebFetch"}
# Music Assistant's own URIs (spotify://, library://) are fine, these go out to the network
NETWORK_URL_RE = re.compile(r"\b(?:https?|ftps?|rtsps?|rtmps?|mms|wss?|sftp)://", re.IGNORECASE)


def norm(url):
    return url.rstrip("/")


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def urls_from_result(entry, block):
    urls = set()
    top = entry.get("toolUseResult")
    if isinstance(top, dict):
        for result in top.get("results") or []:
            if isinstance(result, dict):
                urls |= {link["url"] for link in result.get("content") or [] if isinstance(link, dict) and "url" in link}
    for line in result_text(block.get("content")).splitlines():
        if line.startswith(LINKS_PREFIX):
            try:
                urls |= {link["url"] for link in json.loads(line[len(LINKS_PREFIX):]) if "url" in link}
            except (ValueError, TypeError, KeyError):
                pass
    return urls


def transcript(path):
    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def blocks(entry):
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def search_result_urls(transcript_path):
    search_ids = set()
    urls = set()
    for entry in transcript(transcript_path):
        for block in blocks(entry):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                search_ids.add(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in search_ids:
                urls |= urls_from_result(entry, block)
    return {norm(u) for u in urls}


def is_owner_message(entry):
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in content) \
        and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def web_used_in_this_request(transcript_path):
    used = False
    for entry in transcript(transcript_path):
        if is_owner_message(entry):
            used = False
        elif any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in WEB_TOOLS for b in blocks(entry)):
            used = True
    return used


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def check_home(event):
    """Reason to block the call, or None"""
    name = event["tool_name"]
    tool = name[len(HA_PREFIX):] if name.startswith(HA_PREFIX) else ""
    integration = tool.split("__")[0]
    if integration not in HA_INTEGRATIONS:
        return f"tools of {integration or name} are not allowed for the voice assistant"
    if any(NETWORK_URL_RE.search(s) for s in strings(event.get("tool_input"))):
        return "web links can't be passed to the home"
    if tool not in HA_READ_TOOLS and web_used_in_this_request(event["transcript_path"]):
        return "the home can't be controlled in a request after reading the web. Answer, and the owner can ask again"
    return None


def main(stdin=sys.stdin, stderr=sys.stderr, mode="fetch"):
    if mode == "home":
        try:
            reason = check_home(json.load(stdin))
        except Exception as e:
            reason = f"the guard could not check the call ({type(e).__name__})"
        if reason:
            print(f"Blocked: {reason}", file=stderr)
            return 2
        return 0
    try:
        event = json.load(stdin)
        url = event["tool_input"]["url"]
        allowed = norm(url) in search_result_urls(event["transcript_path"])
    except Exception as e:
        print(f"Web fetch blocked: the guard could not check the URL ({type(e).__name__})", file=stderr)
        return 2
    if allowed:
        return 0
    print("Web fetch blocked: only pages from your own search results can be opened. Search first", file=stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(mode=sys.argv[1] if len(sys.argv) > 1 else "fetch"))
