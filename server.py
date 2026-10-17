#!/usr/bin/env python3
"""issue-graph MCP server (stdio, stdlib only). Works for any GitHub repo `gh` can read."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import graph as g

VERSION = "0.1.0"
REPO = {"type": "string", "description": "GitHub repository as 'owner/name'"}
REFRESH = {"type": "boolean", "description": "Re-fetch from GitHub instead of using the cache (default cache age: 1 hour)"}

TOOLS = [
    {
        "name": "build_graph",
        "description": "Fetch all issues, PRs and comments of a repo and write one self-contained, interactive HTML graph "
                       "(draggable nodes, filters, 'find work' list). Links come from #N and issue/PR URLs in titles, bodies and ALL "
                       "comments, GitHub's own closing references, and bot 'possibly related' / 'possible duplicate' comments. "
                       "The first run on a big repo takes minutes.",
        "inputSchema": {"type": "object", "required": ["repo"], "properties": {
            "repo": REPO, "refresh": REFRESH,
            "out_path": {"type": "string", "description": "Where to write the .html file (default: ~/.cache/issue-graph-mcp/<owner>__<name>/graph.html)"},
            "include_closed": {"type": "boolean", "description": "Show closed and merged items by default in the page (default: only open)"},
            "comments": {"type": "boolean", "description": "Also read comments for links (default true)"}}},
    },
    {
        "name": "find_work",
        "description": "Ranked list of open issues that nobody has a PR for yet, to pick new work from. Uses the same link data as the graph.",
        "inputSchema": {"type": "object", "required": ["repo"], "properties": {
            "repo": REPO, "refresh": REFRESH,
            "limit": {"type": "integer", "description": "Max rows (default 20)"},
            "sort": {"type": "string", "enum": ["priority", "links", "oldest", "newest"], "description": "Default priority (from labels such as 'priority: high'), then most linked"},
            "hide_with_pr": {"type": "boolean", "description": "Skip issues linked to an open or merged PR (default true)"},
            "hide_unclear": {"type": "boolean", "description": "Skip duplicate / needs-info / question / support labelled issues (default true)"},
            "label": {"type": "string", "description": "Only issues with exactly this label"},
            "query": {"type": "string", "description": "Only issues whose title contains this text"}}},
    },
    {
        "name": "related_items",
        "description": "Everything linked to one issue or PR (closes, mentions, bot-suggested duplicates / related), with titles and states.",
        "inputSchema": {"type": "object", "required": ["repo", "number"], "properties": {"repo": REPO, "refresh": REFRESH, "number": {"type": "integer"}}},
    },
]


def _graph(a: dict, comments: bool = True) -> tuple[dict, dict]:
    repo = g.check_repo(a.get("repo"))
    raw = g.fetch(repo, refresh=bool(a.get("refresh")), comments=comments)
    return raw, g.build(raw)


def call(name: str, a: dict):
    if name == "build_graph":
        raw, graph = _graph(a, a.get("comments", True))
        out = Path(a["out_path"]).expanduser() if a.get("out_path") else g.CACHE / a["repo"].replace("/", "__") / "graph.html"
        if out.suffix.lower() != ".html":
            raise ValueError("out_path must end in .html")
        g.render(graph, out, only_open=not a.get("include_closed"))
        return {"file": str(out.resolve()), "open": f"file://{out.resolve()}", **g.stats(graph), "warnings": raw.get("warnings", [])}
    if name == "find_work":
        _, graph = _graph(a)
        return g.find_work(graph, limit=int(a.get("limit", 20)), sort=a.get("sort", "priority"), hide_with_pr=a.get("hide_with_pr", True),
                           hide_unclear=a.get("hide_unclear", True), label=a.get("label"), query=a.get("query"))
    if name == "related_items":
        _, graph = _graph(a)
        return g.neighbours(graph, int(a["number"]))
    raise ValueError(f"unknown tool {name}")


def reply(id_, result=None, error=None):
    m = {"jsonrpc": "2.0", "id": id_}
    m.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(m, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(m: dict) -> None:
    method, id_ = m.get("method"), m.get("id")
    if id_ is None:  # notification
        return
    if method == "initialize":
        want = (m.get("params") or {}).get("protocolVersion", "2025-06-18")
        reply(id_, {"protocolVersion": want, "capabilities": {"tools": {}}, "serverInfo": {"name": "issue-graph", "version": VERSION}})
    elif method == "ping":
        reply(id_, {})
    elif method == "tools/list":
        reply(id_, {"tools": TOOLS})
    elif method == "tools/call":
        p = m.get("params") or {}
        try:
            res = call(p.get("name", ""), p.get("arguments") or {})
            reply(id_, {"content": [{"type": "text", "text": json.dumps(res, indent=1, ensure_ascii=False)}]})
        except Exception as e:  # tool errors are results, not protocol errors
            print(traceback.format_exc(), file=sys.stderr)
            reply(id_, {"isError": True, "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}]})
    else:
        reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})


if __name__ == "__main__":
    main()
