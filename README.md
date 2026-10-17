# issue-graph MCP server

Turns any GitHub repo's issues, PRs and comments into an interactive graph (one self-contained
HTML file) and answers "what should I work on next?". Python 3 standard library only; all GitHub
access goes through the [`gh`](https://cli.github.com/) CLI, so it uses your existing `gh auth login`.

## Install

```sh
claude mcp add issue-graph -- python3 /home/max/Coding/issue-graph-mcp/server.py
```

Any MCP client works the same way: command `python3`, argument the path to `server.py`, stdio transport.

## Tools

| Tool | What it does |
|---|---|
| `build_graph(repo, refresh?, out_path?, include_closed?, comments?)` | Fetches everything, writes the HTML graph, returns the file path and link statistics. |
| `find_work(repo, limit?, sort?, hide_with_pr?, hide_unclear?, label?, query?)` | Open issues nobody has a PR for, ranked by priority label, then by how connected they are. |
| `related_items(repo, number)` | Everything linked to one issue/PR, with the kind of link. |

`repo` is always `owner/name`. Data is cached for an hour in `~/.cache/issue-graph-mcp/<owner>__<name>/`
(`refresh: true` re-fetches). The first run on a big repo takes minutes (photocraft: ~2 min,
cli/cli: ~6 min); later calls answer from the cache.

## Where links come from

- `#N`, `owner/repo#N` and issue/PR URLs of the same repo in titles, bodies and **every comment**
  (human or bot); text in code blocks is ignored. Links to other repos are ignored.
- `Closes/Fixes/Resolves #N` in a title or body, and GitHub's own closing references
  (the "linked a pull request" sidebar), count as **closes**.
- Bot comments starting with "Possibly related" / "Possible duplicate" become their own link kinds.

When two items are linked several ways, the strongest wins: closes > duplicate > related > mention.

## The graph page

Drag nodes, pan, zoom, click to highlight neighbours. Filters for state, link kind, label, hubs,
unlinked items; a "Find work" list with sort and unclear/bug filters. Priority is read from labels
(`priority: high`, `P1`, `severity: critical`, ...), and labels such as `duplicate`, `needs info`,
`question` count as "unclear".

## Tests

```sh
python3 -m unittest discover -s tests
```
