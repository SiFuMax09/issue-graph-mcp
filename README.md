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
| `possibly_fixed(repo, min_confidence?, limit?)` | Open issues that may already be fixed, with the evidence (see below). |
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

## Possibly fixed

An open issue is flagged when something linked to it has been resolved:

- **high**: a merged PR says `Closes #N` but the issue is still open, or the bot marked it a duplicate of an issue
  that was closed as completed after this one was opened.
- **medium**: a bot "possibly related" issue was closed as completed after this one was opened; or the same in
  another repository (sibling apps sharing libraries; capped at medium because it may not be fixed *here*).
- **low**: the linked item was closed *before* this issue was opened (the reporter may be on an older build),
  or a merged PR merely mentions it.

Closures as "not planned" / duplicate are no evidence. Each flag lists the items and the merged PRs that fixed
them. It is a hint to check, not proof: the bot's "possibly related" is noisy. `find_work` lists the flag and can
hide it (`hide_possibly_fixed`); the page shows a "fixed?" badge and a checkbox.

## The graph page

Drag nodes, pan, zoom, click to highlight neighbours. Filters for state, link kind, label, hubs,
unlinked items; a "Find work" list with sort and unclear/bug filters. Priority is read from labels
(`priority: high`, `P1`, `severity: critical`, ...), and labels such as `duplicate`, `needs info`,
`question` count as "unclear".

## Tests

```sh
python3 -m unittest discover -s tests
```
