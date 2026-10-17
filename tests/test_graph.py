import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import graph as g

REPO = "o/r"


def raw(**kw):
    def item(n, title="t", body="", state="OPEN", labels=(), merged=None):
        return {"number": n, "title": title, "state": state, "labels": [{"name": l} for l in labels], "body": body, "createdAt": "2026-01-01T00:00:00Z", "mergedAt": merged, "author": {"login": "u"}}
    base = {"repo": REPO, "issues": [item(1), item(2), item(3, labels=["priority: high"]), item(4, labels=["duplicate"])],
            "prs": [item(10, body="Fixes #1"), item(11, body="see https://github.com/o/r/issues/2", state="MERGED", merged="2026-01-02T00:00:00Z")],
            "comments": [], "closing": []}
    base.update(kw)
    return base


def kinds(graph):
    return {(min(a, b), max(a, b)): k for a, b, k in graph["edges"]}


class Links(unittest.TestCase):
    def test_body_closes_and_url_mention(self):
        k = kinds(g.build(raw()))
        self.assertEqual(k[(1, 10)], "c")
        self.assertEqual(k[(2, 11)], "r")

    def test_human_comment_links_count_including_urls_and_not_code(self):
        c = [{"u": "https://api.github.com/repos/o/r/issues/3", "a": "bob", "t": "User", "b": "like #1 and https://github.com/o/r/pull/10\n```\n#2\n```"}]
        k = kinds(g.build(raw(comments=c)))
        self.assertEqual(k[(1, 3)], "r")
        self.assertEqual(k[(3, 10)], "r")
        self.assertNotIn((2, 3), k)

    def test_other_repos_are_ignored(self):
        c = [{"u": "https://api.github.com/repos/o/r/issues/3", "a": "bob", "t": "User", "b": "x/y#1 https://github.com/x/y/issues/2"}]
        self.assertEqual(g.build(raw(comments=c))["edges"], g.build(raw())["edges"])

    def test_bot_comment_kinds_and_strongest_wins(self):
        c = [{"u": "https://api.github.com/repos/o/r/issues/4", "a": "x[bot]", "t": "Bot", "b": "Possible duplicate of #3.\n\nPossibly related: #1 and #2."},
             {"u": "https://api.github.com/repos/o/r/issues/2", "a": "bob", "t": "User", "b": "#4"}]
        k = kinds(g.build(raw(comments=c)))
        self.assertEqual((k[(3, 4)], k[(1, 4)], k[(2, 4)]), ("d", "p", "p"))

    def test_github_closing_references(self):
        self.assertEqual(kinds(g.build(raw(closing=[[11, 3]])))[(3, 11)], "c")


class Fixed(unittest.TestCase):
    def _raw(self, comments, **kw):
        r = raw(comments=comments)
        r["issues"].append({"number": 20, "title": "closed one", "state": "CLOSED", "stateReason": "COMPLETED", "closedAt": "2026-02-01T00:00:00Z", "labels": [], "body": "", "createdAt": "2026-01-01T00:00:00Z", "author": {"login": "u"}})
        r["issues"].append({"number": 21, "title": "later one", "state": "OPEN", "labels": [], "body": "", "createdAt": "2026-03-01T00:00:00Z", "author": {"login": "u"}})
        r["issues"].append({"number": 22, "title": "wontfix one", "state": "CLOSED", "stateReason": "NOT_PLANNED", "closedAt": "2026-02-01T00:00:00Z", "labels": [], "body": "", "createdAt": "2026-01-01T00:00:00Z", "author": {"login": "u"}})
        r.update(kw)
        return r

    def bot(self, issue, text):
        return {"u": f"https://api.github.com/repos/o/r/issues/{issue}", "a": "x[bot]", "t": "Bot", "b": text}

    def pf(self, comments, **kw):
        r = self._raw(comments, **kw)
        return {n["n"]: n.get("pf") for n in g.build(r)["nodes"]}

    def test_duplicate_of_an_issue_closed_after_it_was_opened_is_high(self):
        pf = self.pf([self.bot(2, "Possible duplicate of #20.")])
        self.assertEqual(pf[2]["confidence"], "high")

    def test_possibly_related_closed_issue_is_medium_and_older_closure_is_low(self):
        pf = self.pf([self.bot(2, "Possibly related: #20."), self.bot(21, "Possibly related: #20.")])
        self.assertEqual((pf[2]["confidence"], pf[21]["confidence"]), ("medium", "low"))

    def test_not_planned_closures_are_no_evidence(self):
        self.assertIsNone(self.pf([self.bot(3, "Possible duplicate of #22.")])[3])

    def test_merged_pr_that_closes_but_issue_is_open_is_high_and_names_the_pr(self):
        r = self._raw([])
        r["prs"][1]["body"] = "Closes #3"
        pf = {n["n"]: n.get("pf") for n in g.build(r)["nodes"]}
        self.assertEqual(pf[3]["confidence"], "high")
        self.assertEqual(pf[3]["evidence"][0]["number"], 11)

    def test_other_repository_evidence_is_capped_at_medium(self):
        r = self._raw([self.bot(3, "Possibly related: o/other#9 and o/other#8.")])
        r["external"] = {"o/other#9": {"title": "x", "state": "C", "r": "COMPLETED", "clt": "2026-02-01T00:00:00Z", "pr": False, "merged": False},
                         "o/other#8": {"title": "y", "state": "O", "r": "", "clt": "", "pr": False, "merged": False}}
        pf = {n["n"]: n.get("pf") for n in g.build(r)["nodes"]}
        self.assertEqual(pf[3]["confidence"], "medium")
        self.assertEqual([e["number"] for e in pf[3]["evidence"]], ["o/other#9"])

    def test_find_work_can_hide_them(self):
        graph = g.build(self._raw([self.bot(3, "Possible duplicate of #20.")]))
        self.assertIn(3, [r["number"] for r in g.find_work(graph)])
        self.assertNotIn(3, [r["number"] for r in g.find_work(graph, hide_possibly_fixed=True)])


class Work(unittest.TestCase):
    def test_find_work_skips_issues_with_a_pr_and_unclear_ones_and_ranks_by_priority(self):
        graph = g.build(raw())
        rows = g.find_work(graph)
        self.assertEqual([r["number"] for r in rows], [3])  # 1 has PR #10, 2 is linked to merged PR #11, 4 is a duplicate
        self.assertEqual(rows[0]["priority"], 3)

    def test_priority_labels(self):
        self.assertEqual([g.priority([l]) for l in ["P0", "priority: critical", "High", "priority: medium", "Low priority", "bug"]], [4, 4, 3, 2, 1, 0])

    def test_repo_name_is_validated(self):
        with self.assertRaises(ValueError):
            g.check_repo("../etc; rm -rf")


if __name__ == "__main__":
    unittest.main()
