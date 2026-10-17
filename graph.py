"""Fetch a GitHub repo's issues, PRs and comments with the `gh` CLI, link them, render an HTML graph.

Stdlib only. Everything GitHub-facing goes through `gh`, so it uses whatever auth `gh` has.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
CACHE = Path.home() / ".cache" / "issue-graph-mcp"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

CLOSE_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:([\w.-]+/[\w.-]+))?#(\d+)", re.I)
REF_RE = re.compile(r"(?<![\w/&])(?:([\w.-]+/[\w.-]+))?#(\d+)\b")
URL_RE = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
CODE_RE = re.compile(r"```.*?```", re.S)

# Edge kinds, strongest first: c = closes/fixes, d = bot "possible duplicate", p = bot "possibly related",
# r = plain mention (body, title or any comment).
RANK = {"r": 0, "p": 1, "d": 2, "c": 3}


class GhError(RuntimeError):
    pass


def gh(*args: str, timeout: int = 900) -> str:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise GhError("the `gh` CLI is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GhError(f"gh {' '.join(args[:3])} timed out") from e
    if r.returncode != 0:
        raise GhError(r.stderr.strip() or f"gh exited with {r.returncode}")
    return r.stdout


def check_repo(repo: str) -> str:
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise ValueError("repo must look like 'owner/name'")
    return repo


def _cache_dir(repo: str) -> Path:
    d = CACHE / repo.replace("/", "__")
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(repo: str, refresh: bool = False, max_age_s: int = 3600, limit: int = 20000, comments: bool = True) -> dict:
    """Raw data of `repo`, cached on disk for `max_age_s` seconds."""
    check_repo(repo)
    f = _cache_dir(repo) / "raw.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < max_age_s:
        raw = json.loads(f.read_text())
        if raw.get("comments") is not None or not comments:
            return raw
    warnings: list[str] = []
    issues = json.loads(gh("issue", "list", "-R", repo, "--state", "all", "--limit", str(limit), "--json", "number,title,state,labels,body,createdAt,author"))
    prs = json.loads(gh("pr", "list", "-R", repo, "--state", "all", "--limit", str(limit), "--json", "number,title,state,labels,body,createdAt,mergedAt,isDraft,author"))
    cms = None
    if comments:
        try:
            out = gh("api", "--paginate", f"repos/{repo}/issues/comments?per_page=100", "--jq", ".[] | {u:.issue_url,a:.user.login,t:.user.type,b:.body}")
            cms = [json.loads(line) for line in out.splitlines() if line.strip()]
        except GhError as e:
            warnings.append(f"comments skipped: {e}")
    closing = []
    try:
        closing = fetch_closing_refs(repo)
    except GhError as e:
        warnings.append(f"closing references skipped: {e}")
    raw = {"repo": repo, "fetched": int(time.time()), "issues": issues, "prs": prs, "comments": cms, "closing": closing, "warnings": warnings}
    f.write_text(json.dumps(raw))
    return raw


def fetch_closing_refs(repo: str) -> list[list[int]]:
    """[pr, issue] pairs GitHub itself records (the 'linked a pull request' sidebar, `Closes #N`)."""
    owner, name = repo.split("/")
    out: list[list[int]] = []
    cursor = ""
    for _ in range(2000):
        after = f', after:"{cursor}"' if cursor else ""
        q = ("query{repository(owner:%s,name:%s){pullRequests(first:100%s){pageInfo{hasNextPage endCursor}"
             "nodes{number closingIssuesReferences(first:20){nodes{number repository{nameWithOwner}}}}}}}") % (json.dumps(owner), json.dumps(name), after)
        d = json.loads(gh("api", "graphql", "-f", f"query={q}"))
        prs = d["data"]["repository"]["pullRequests"]
        for pr in prs["nodes"]:
            for i in pr["closingIssuesReferences"]["nodes"]:
                if i["repository"]["nameWithOwner"].lower() == repo.lower():
                    out.append([pr["number"], i["number"]])
        if not prs["pageInfo"]["hasNextPage"]:
            break
        cursor = prs["pageInfo"]["endCursor"]
    return out


def _refs(text: str, repo: str) -> list[tuple[int, bool]]:
    """(number, closes?) for every same-repo reference in `text`: `#N`, `owner/repo#N`, issue/PR URLs."""
    text = CODE_RE.sub("", text or "")
    low = repo.lower()
    closes = {int(n) for r, n in CLOSE_RE.findall(text) if not r or r.lower() == low}
    seen: dict[int, bool] = {}
    for r, n in REF_RE.findall(text):
        if not r or r.lower() == low:
            seen[int(n)] = int(n) in closes
    for r, n in URL_RE.findall(text):
        if r.lower() == low:
            seen.setdefault(int(n), False)
    return list(seen.items())


def build(raw: dict) -> dict:
    repo = raw["repo"]
    nodes: dict[int, dict] = {}
    for kind, items in (("i", raw["issues"]), ("p", raw["prs"])):
        for x in items:
            s = "M" if x.get("mergedAt") else x["state"][0]
            nodes[x["number"]] = {
                "n": x["number"], "t": kind, "s": s, "ti": x["title"], "l": [l["name"] for l in x["labels"]],
                "d": x["createdAt"][:10], "ex": (x.get("body") or "").replace("\r", "")[:200],
                "a": (x.get("author") or {}).get("login", ""), "_b": x.get("body") or "",
            }
    edges: dict[tuple[int, int], tuple[int, int, str]] = {}

    def add(a: int, b: int, kind: str) -> None:
        if a == b or a not in nodes or b not in nodes:
            return
        k = (min(a, b), max(a, b))
        if k not in edges or RANK[kind] > RANK[edges[k][2]]:
            edges[k] = (a, b, kind)

    for n, x in nodes.items():
        for m, closes in _refs(x["ti"] + "\n" + x["_b"], repo):
            add(n, m, "c" if closes else "r")
    for pr, issue in raw.get("closing") or []:
        add(pr, issue, "c")
    for c in raw.get("comments") or []:
        try:
            n = int(c["u"].rsplit("/", 1)[1])
        except (KeyError, ValueError, IndexError):
            continue
        body = c.get("b") or ""
        bot = c.get("t") == "Bot" or str(c.get("a", "")).endswith("[bot]")
        if bot:
            for line in body.split("\n"):
                low = line.lower()
                kind = "d" if low.startswith("possible duplicate") or low.startswith("duplicate of") else "p" if low.startswith("possibly related") else "r"
                for m, _ in _refs(line, repo):
                    add(n, m, kind)
        else:
            for m, _ in _refs(body, repo):
                add(n, m, "r")
    for x in nodes.values():
        x.pop("_b")
    return {"repo": repo, "repoUrl": f"https://github.com/{repo}/", "nodes": list(nodes.values()), "edges": [list(e) for e in edges.values()]}


def render(graph: dict, out: Path, only_open: bool = True) -> Path:
    t = (HERE / "template.html").read_text()
    t = t.replace("__TITLE__", f"{graph['repo']} issues & PRs")
    data = json.dumps(graph, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if not only_open:
        t = t.replace('<input type="checkbox" id="sc">', '<input type="checkbox" id="sc" checked>').replace('<input type="checkbox" id="sm">', '<input type="checkbox" id="sm" checked>')
    t = t.replace("__DATA__", data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(t)
    return out


PRIO_CRIT = re.compile(r"critical|blocker|urgent|\bp0\b|\bp1\b|sev(erity)?[ :-]*[01]", re.I)
PRIO_HIGH = re.compile(r"high|\bp2\b", re.I)
PRIO_MED = re.compile(r"medium|normal|\bp3\b", re.I)
PRIO_LOW = re.compile(r"low|minor|\bp4\b", re.I)
PRIO_ANY = re.compile(r"prior|^p[0-4]\b|severity|urgent|critical|blocker|\bhigh\b|\bmedium\b|\blow\b", re.I)
UNCLEAR = re.compile(r"duplicate|invalid|wontfix|won't fix|needs[ -]info|needs[ -]repro|question|support|no action|decision|discussion|stale|multiple issues|spam", re.I)


def priority(labels: list[str]) -> int:
    best = 0
    for l in labels:
        if not PRIO_ANY.search(l):
            continue
        best = max(best, 4 if PRIO_CRIT.search(l) else 3 if PRIO_HIGH.search(l) else 2 if PRIO_MED.search(l) else 1 if PRIO_LOW.search(l) else 0)
    return best


def degrees(graph: dict) -> dict[int, int]:
    d: dict[int, int] = {}
    for a, b, _ in graph["edges"]:
        d[a] = d.get(a, 0) + 1
        d[b] = d.get(b, 0) + 1
    return d


def neighbours(graph: dict, number: int) -> list[dict]:
    by = {n["n"]: n for n in graph["nodes"]}
    out = []
    for a, b, k in graph["edges"]:
        if number in (a, b):
            o = by[b if a == number else a]
            out.append({"number": o["n"], "type": "issue" if o["t"] == "i" else "pr", "state": o["s"], "title": o["ti"], "link": {"c": "closes", "d": "possible duplicate", "p": "possibly related", "r": "mentions"}[k]})
    return out


def find_work(graph: dict, limit: int = 20, sort: str = "priority", hide_with_pr: bool = True, hide_unclear: bool = True,
              label: str | None = None, query: str | None = None) -> list[dict]:
    deg = degrees(graph)
    by = {n["n"]: n for n in graph["nodes"]}
    has_pr: set[int] = set()
    for a, b, _ in graph["edges"]:
        for x, y in ((a, b), (b, a)):
            if by[x]["t"] == "i" and by[y]["t"] == "p" and by[y]["s"] != "C":
                has_pr.add(x)
    rows = []
    for n in graph["nodes"]:
        if n["t"] != "i" or n["s"] != "O":
            continue
        if hide_with_pr and n["n"] in has_pr:
            continue
        if hide_unclear and any(UNCLEAR.search(l) for l in n["l"]):
            continue
        if label and label not in n["l"]:
            continue
        if query and query.lower() not in n["ti"].lower():
            continue
        rows.append({"number": n["n"], "title": n["ti"], "labels": n["l"], "priority": priority(n["l"]), "links": deg.get(n["n"], 0),
                     "created": n["d"], "has_pr": n["n"] in has_pr, "url": f"{graph['repoUrl']}issues/{n['n']}"})
    key = {"priority": lambda r: (-r["priority"], -r["links"]), "links": lambda r: (-r["links"], -r["priority"]),
           "oldest": lambda r: r["created"], "newest": lambda r: tuple(-ord(c) for c in r["created"])}[sort]
    rows.sort(key=key)
    return rows[:limit]


def stats(graph: dict) -> dict:
    kinds: dict[str, int] = {}
    for _, _, k in graph["edges"]:
        kinds[k] = kinds.get(k, 0) + 1
    return {"issues": sum(n["t"] == "i" for n in graph["nodes"]), "prs": sum(n["t"] == "p" for n in graph["nodes"]), "links": len(graph["edges"]),
            "links_by_kind": {{"c": "closes", "d": "possible_duplicate", "p": "possibly_related", "r": "mentions"}[k]: v for k, v in kinds.items()}}
