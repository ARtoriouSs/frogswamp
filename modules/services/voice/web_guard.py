"""Claude Code PreToolUse hook for WebFetch: only pages that came back in this session's own
WebSearch results may be opened

Claude can't build a URL itself, so it can't put home data into a link, even if a web page
tells it to. Anything unexpected blocks the fetch (exit code 2).
"""

import json
import sys

LINKS_PREFIX = "Links: "


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


def search_result_urls(transcript_path):
    search_ids = set()
    urls = set()
    with open(transcript_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                    search_ids.add(block.get("id"))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in search_ids:
                    urls |= urls_from_result(entry, block)
    return {norm(u) for u in urls}


def main(stdin=sys.stdin, stderr=sys.stderr):
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
    sys.exit(main())
