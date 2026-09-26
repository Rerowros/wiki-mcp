#!/usr/bin/env python3
"""Regression tests for full and incremental D1 index exports."""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import export_index as E  # noqa: E402
import push_index as P  # noqa: E402
import wikilib as W  # noqa: E402


def page(slug: str, marker: str, sections: int = 2) -> W.Page:
    body = "\n\n".join(
        f"## Section {i}\n\n{marker} text for {slug} section {i}."
        for i in range(sections)
    )
    fm = {
        "type": "finding", "title": slug.title(), "summary": marker,
        "status": "current", "author": "test", "created": "2026-09-07",
        "updated": "2026-09-07", "tags": [], "related": [],
        "claims": [{
            "subject": slug, "metric": "score", "qualifier": "v1",
            "value": marker, "as_of": "2026-09-07",
        }],
    }
    raw = W.dump_frontmatter(fm) + "\n" + body
    return W.Page(slug, os.path.join(ROOT, "wiki", "findings", f"{slug}.md"), fm, body, raw)


def apply(db: sqlite3.Connection, statements: list[str]) -> None:
    db.executescript("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;")


def schema() -> list[str]:
    return [statement.strip() + ";" for statement in E.SCHEMA.split(";") if statement.strip()]


class IndexUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        apply(self.db, schema())

    def tearDown(self) -> None:
        self.db.close()

    def assert_consistent(self) -> None:
        chunks = self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        fts = self.db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
        mismatched = self.db.execute(
            "SELECT count(*) FROM chunks c JOIN chunks_fts f ON f.rowid=c.id "
            "WHERE c.text <> f.text"
        ).fetchone()[0]
        self.assertEqual(chunks, fts)
        self.assertEqual(mismatched, 0)

    def test_full_two_independent_increments_repeat_and_delete(self) -> None:
        alpha = page("alpha-model", "alpha-v1")
        beta = page("beta-model", "beta-v1", sections=3)
        apply(self.db, [
            "DELETE FROM chunks_fts;", "DELETE FROM chunks;", "DELETE FROM claims;",
            "DELETE FROM links;", "DELETE FROM threads;", "DELETE FROM pages;",
            *E.page_rows(alpha), *E.page_rows(beta),
        ])
        self.assertEqual(self.db.execute("SELECT count(*) FROM pages").fetchone()[0], 2)
        self.assert_consistent()

        # These are separate exports.  The old count-based allocator gave both
        # updates the same starting id and the second one failed.
        alpha_v2 = page("alpha-model", "alpha-v2", sections=4)
        beta_v2 = page("beta-model", "beta-v2", sections=1)
        apply(self.db, E.page_rows(alpha_v2))
        apply(self.db, E.page_rows(beta_v2))
        self.assert_consistent()
        self.assertEqual(
            self.db.execute("SELECT summary FROM pages WHERE slug='alpha-model'").fetchone()[0],
            "alpha-v2",
        )

        # Replaying an entire incremental export after a network retry is safe.
        apply(self.db, E.page_rows(beta_v2))
        self.assert_consistent()
        self.assertEqual(self.db.execute("SELECT count(*) FROM claims").fetchone()[0], 2)

        apply(self.db, E.delete_rows("alpha-model"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM pages").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM chunks WHERE slug='alpha-model'").fetchone()[0], 0)
        self.assert_consistent()

    def test_active_rebuild_is_never_stolen_but_expired_one_is_recoverable(self) -> None:
        active = {
            "index_state": "building", "index_run_id": "publisher-a",
            "index_lease_until": 20_000,
        }
        self.assertEqual(P.recovery_action(active, now_ms=10_000), "wait")
        self.assertEqual(P.recovery_action(active, now_ms=20_001), "abort_full")
        self.assertEqual(P.recovery_action({"index_state": "failed"}, now_ms=10_000), "full")
        self.assertEqual(P.recovery_action({}, now_ms=10_000), "ready")

    def test_chunk_ids_are_stable_negative_and_page_scoped(self) -> None:
        first = E.chunk_id("alpha", 0)
        self.assertEqual(first, E.chunk_id("alpha", 0))
        self.assertLess(first, 0)
        self.assertNotEqual(first, E.chunk_id("alpha", 1))
        self.assertNotEqual(first, E.chunk_id("beta", 0))

    def test_partial_batch_cannot_publish_head_and_full_recovery_repairs_it(self) -> None:
        apply(self.db, [
            "INSERT INTO meta(k,v) VALUES ('head_sha','old-head');",
            "INSERT INTO meta(k,v) VALUES ('index_state','building');",
        ])
        rows = E.page_rows(page("interrupted", "partial", sections=5))
        apply(self.db, rows[:9])

        # Completion metadata is no longer part of exporter batches.  Only the
        # authenticated finish operation may move head_sha after every batch.
        self.assertEqual(
            self.db.execute("SELECT v FROM meta WHERE k='head_sha'").fetchone()[0],
            "old-head",
        )

        recovered = page("canonical", "from-markdown", sections=2)
        apply(self.db, [
            "DELETE FROM chunks_fts;", "DELETE FROM chunks;", "DELETE FROM claims;",
            "DELETE FROM links;", "DELETE FROM threads;", "DELETE FROM pages;",
            *E.page_rows(recovered),
            "UPDATE meta SET v='new-head' WHERE k='head_sha';",
            "UPDATE meta SET v='ready' WHERE k='index_state';",
        ])
        self.assertEqual(self.db.execute("SELECT slug FROM pages").fetchone()[0], "canonical")
        self.assertEqual(self.db.execute("SELECT v FROM meta WHERE k='head_sha'").fetchone()[0], "new-head")
        self.assert_consistent()


if __name__ == "__main__":
    unittest.main()
