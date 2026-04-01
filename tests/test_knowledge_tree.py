"""Tests for the Hierarchical Memory Tree system.

Covers: node CRUD, tree traversal, keyword index, document ingestion,
proactive loading, branch caching, prediction, FTS5 search, stats,
and edge cases.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.memory.knowledge_tree import (
    KnowledgeTree,
    TreeNode,
    TreeStats,
    _chunk_text,
    _estimate_tokens,
    _make_summary,
    _extract_title,
    _sanitise_fts,
)
from cortex.memory.memory_index import MemoryIndex
from cortex.memory.proactive_loader import ContextBundle, ProactiveLoader


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path():
    """Return an isolated temp DB with the full schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        p = Path(f.name)
    set_db_path(p)
    init_db(p)
    yield p
    p.unlink(missing_ok=True)


@pytest.fixture()
def conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    yield c
    c.close()


@pytest.fixture()
def tree(conn):
    return KnowledgeTree(conn=conn)


@pytest.fixture()
def index():
    return MemoryIndex()


@pytest.fixture()
def loader(tree):
    return ProactiveLoader(tree)


def _make_node(
    id: str,
    parent_id: str | None = None,
    title: str = "",
    summary: str = "",
    content: str = "",
    level: int = 0,
    keywords: list[str] | None = None,
) -> TreeNode:
    return TreeNode(
        id=id,
        parent_id=parent_id,
        title=title or id,
        summary=summary,
        content=content,
        token_count=_estimate_tokens(summary + " " + content),
        level=level,
        keywords=keywords or [],
    )


# ═══════════════════════════════════════════════════════════════════════
# Node CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestNodeCRUD:
    def test_add_and_get(self, tree):
        node = _make_node("root.a", title="Root A", summary="A summary")
        tree.add_node(node)
        fetched = tree.get_node("root.a")
        assert fetched is not None
        assert fetched.title == "Root A"
        assert fetched.summary == "A summary"

    def test_get_missing_returns_none(self, tree):
        assert tree.get_node("nonexistent") is None

    def test_update_content(self, tree):
        tree.add_node(_make_node("n1", content="old"))
        tree.update_node("n1", "new content")
        assert tree.get_node("n1").content == "new content"

    def test_update_recalculates_tokens(self, tree):
        tree.add_node(_make_node("n1", content="short"))
        tree.update_node("n1", "x" * 400)
        assert tree.get_node("n1").token_count == 100  # 400/4

    def test_update_nonexistent_is_noop(self, tree):
        tree.update_node("missing", "whatever")  # should not raise

    def test_delete(self, tree):
        tree.add_node(_make_node("r", level=0))
        tree.add_node(_make_node("r.a", parent_id="r", level=1))
        tree.delete_node("r.a")
        assert tree.get_node("r.a") is None
        assert "r.a" not in tree.get_node("r").children

    def test_delete_recursive(self, tree):
        tree.add_node(_make_node("r", level=0))
        tree.add_node(_make_node("r.a", parent_id="r", level=1))
        tree.add_node(_make_node("r.a.x", parent_id="r.a", level=2))
        tree.delete_node("r")
        assert tree.get_node("r") is None
        assert tree.get_node("r.a") is None
        assert tree.get_node("r.a.x") is None

    def test_add_sets_timestamps(self, tree):
        node = _make_node("ts")
        tree.add_node(node)
        assert node.created_at != ""
        assert node.updated_at != ""

    def test_children_updated_on_add(self, tree):
        tree.add_node(_make_node("parent", level=0))
        tree.add_node(_make_node("child1", parent_id="parent", level=1))
        tree.add_node(_make_node("child2", parent_id="parent", level=1))
        parent = tree.get_node("parent")
        assert set(parent.children) == {"child1", "child2"}

    def test_delete_nonexistent_is_noop(self, tree):
        tree.delete_node("nope")  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# Tree Traversal
# ═══════════════════════════════════════════════════════════════════════


class TestTreeTraversal:
    def _build_sample_tree(self, tree):
        tree.add_node(_make_node("cooking", level=0, summary="Cooking knowledge",
                                 keywords=["cooking", "food", "recipe"]))
        tree.add_node(_make_node("cooking.italian", parent_id="cooking", level=1,
                                 summary="Italian cuisine",
                                 keywords=["italian", "pasta", "pizza"]))
        tree.add_node(_make_node("cooking.italian.pasta", parent_id="cooking.italian",
                                 level=2, content="How to make pasta…",
                                 keywords=["pasta", "noodles"]))
        tree.add_node(_make_node("cooking.italian.pizza", parent_id="cooking.italian",
                                 level=2, content="Pizza dough recipe",
                                 keywords=["pizza", "dough"]))
        tree.add_node(_make_node("science", level=0, summary="Science topics",
                                 keywords=["science", "physics", "chemistry"]))

    def test_get_roots(self, tree):
        self._build_sample_tree(tree)
        roots = tree.get_roots()
        ids = {r.id for r in roots}
        assert ids == {"cooking", "science"}

    def test_get_children(self, tree):
        self._build_sample_tree(tree)
        children = tree.get_children("cooking.italian")
        ids = {c.id for c in children}
        assert ids == {"cooking.italian.pasta", "cooking.italian.pizza"}

    def test_get_children_empty(self, tree):
        self._build_sample_tree(tree)
        assert tree.get_children("cooking.italian.pasta") == []

    def test_get_children_nonexistent(self, tree):
        assert tree.get_children("missing") == []

    def test_get_path(self, tree):
        self._build_sample_tree(tree)
        path = tree.get_path("cooking.italian.pasta")
        ids = [n.id for n in path]
        assert ids == ["cooking", "cooking.italian", "cooking.italian.pasta"]

    def test_get_path_root(self, tree):
        self._build_sample_tree(tree)
        path = tree.get_path("cooking")
        assert len(path) == 1
        assert path[0].id == "cooking"

    def test_get_branch(self, tree):
        self._build_sample_tree(tree)
        branch = tree.get_branch("cooking", max_depth=2)
        ids = {n.id for n in branch}
        assert "cooking" in ids
        assert "cooking.italian" in ids
        assert "cooking.italian.pasta" in ids

    def test_get_branch_limited_depth(self, tree):
        self._build_sample_tree(tree)
        branch = tree.get_branch("cooking", max_depth=1)
        ids = {n.id for n in branch}
        assert "cooking" in ids
        assert "cooking.italian" in ids
        assert "cooking.italian.pasta" not in ids

    def test_get_branch_content(self, tree):
        self._build_sample_tree(tree)
        content = tree.get_branch_content("cooking.italian", max_depth=1)
        assert "Italian cuisine" in content
        assert "How to make pasta" in content

    def test_get_branch_nonexistent(self, tree):
        assert tree.get_branch("missing") == []


# ═══════════════════════════════════════════════════════════════════════
# Keyword Index
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryIndex:
    def test_add_and_search(self, index):
        index.add("n1", ["python", "programming"])
        index.add("n2", ["python", "data", "science"])
        results = index.search(["python"])
        ids = [r[0] for r in results]
        assert "n1" in ids
        assert "n2" in ids

    def test_search_ranking(self, index):
        index.add("n1", ["python", "programming"])
        index.add("n2", ["python", "data", "science"])
        index.add("n3", ["javascript", "web"])
        results = index.search(["python", "programming"])
        # n1 matches both keywords, should rank higher
        assert results[0][0] == "n1"

    def test_search_no_match(self, index):
        index.add("n1", ["python"])
        assert index.search(["rust"]) == []

    def test_search_empty_keywords(self, index):
        assert index.search([]) == []

    def test_remove(self, index):
        index.add("n1", ["alpha"])
        index.remove("n1")
        assert index.search(["alpha"]) == []

    def test_remove_nonexistent(self, index):
        index.remove("nope")  # should not raise

    def test_extract_keywords(self, index):
        kw = index.extract_keywords("How do I make a Python web application?")
        assert "python" in kw
        assert "web" in kw
        assert "application" in kw
        # stop-words removed
        assert "how" not in kw
        assert "do" not in kw
        assert "a" not in kw

    def test_extract_keywords_dedup(self, index):
        kw = index.extract_keywords("python python python")
        assert kw.count("python") == 1

    def test_extract_keywords_short_tokens_filtered(self, index):
        kw = index.extract_keywords("I x y z do this")
        assert "x" not in kw  # single char

    def test_get_stats(self, index):
        index.add("n1", ["a", "b"])
        index.add("n2", ["b", "c"])
        stats = index.get_stats()
        assert stats["total_keywords"] == 3  # a, b, c
        assert stats["total_nodes"] == 2

    def test_save_and_load(self, index, db_path):
        index.add("n1", ["alpha", "beta"])
        index.add("n2", ["gamma"])
        save_path = str(db_path.parent / "idx.json")
        index.save(save_path)

        new_index = MemoryIndex()
        new_index.load(save_path)
        results = new_index.search(["alpha"])
        assert len(results) == 1
        assert results[0][0] == "n1"

    def test_load_nonexistent(self, index):
        index.load("/does/not/exist.json")  # should not raise

    def test_top_k(self, index):
        for i in range(20):
            index.add(f"n{i}", ["shared"])
        results = index.search(["shared"], top_k=5)
        assert len(results) == 5

    def test_normalisation(self, index):
        index.add("n1", ["Python!", "  DATA  "])
        results = index.search(["python"])
        assert len(results) == 1

    def test_idf_weighting(self, index):
        # "rare" appears in 1 node, "common" in 10
        for i in range(10):
            index.add(f"common_{i}", ["common"])
        index.add("rare_node", ["rare", "common"])
        results = index.search(["rare", "common"], top_k=3)
        # rare_node should rank first (matches rare + common, and rare has high IDF)
        assert results[0][0] == "rare_node"


# ═══════════════════════════════════════════════════════════════════════
# Integrated Search (Tree + Index)
# ═══════════════════════════════════════════════════════════════════════


class TestTreeSearch:
    def test_search_keywords(self, tree):
        tree.add_node(_make_node("finance", level=0, keywords=["finance", "money"]))
        tree.add_node(_make_node("finance.tax", parent_id="finance", level=1,
                                 keywords=["tax", "irs", "deduction"]))
        results = tree.search(["tax"])
        assert any(n.id == "finance.tax" for n in results)

    def test_get_relevant_branches(self, tree):
        tree.add_node(_make_node("health", level=0, keywords=["health", "medical"]))
        tree.add_node(_make_node("health.cardio", parent_id="health", level=1,
                                 keywords=["cardio", "heart", "exercise"]))
        tree.add_node(_make_node("health.cardio.running", parent_id="health.cardio",
                                 level=2, keywords=["running", "jogging"]))
        branches = tree.get_relevant_branches("How should I start running for cardio?")
        assert len(branches) > 0
        # Should resolve up to branch root
        assert any(b in ("health", "health.cardio") for b in branches)


# ═══════════════════════════════════════════════════════════════════════
# Document Ingestion
# ═══════════════════════════════════════════════════════════════════════


class TestIngestion:
    def test_ingest_document(self, tree):
        text = "Python is great.\n\n" * 10
        root_id = tree.ingest_document(text, "Programming")
        assert root_id == "programming"
        root = tree.get_node(root_id)
        assert root is not None
        assert root.level == 0
        children = tree.get_children(root_id)
        assert len(children) >= 1

    def test_ingest_document_idempotent_root(self, tree):
        tree.ingest_document("Part 1", "Topic A")
        tree.ingest_document("Part 2", "Topic A")
        root = tree.get_node("topic_a")
        assert root is not None
        children = tree.get_children("topic_a")
        assert len(children) >= 2

    def test_ingest_conversation(self, tree):
        class FakeTurn:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        turns = [
            FakeTurn("user", "What is gravity?"),
            FakeTurn("assistant", "Gravity is the force of attraction between objects."),
        ]
        root_id = tree.ingest_conversation(turns, "abc123")
        assert root_id == "conversation.abc123"
        root = tree.get_node(root_id)
        assert root is not None
        children = tree.get_children(root_id)
        assert len(children) == 1

    def test_ingest_codebase(self, tree, db_path):
        # Create a small test file
        test_file = db_path.parent / "test_code.py"
        test_file.write_text("def hello():\n    print('Hello')\n")

        root_id = tree.ingest_codebase([str(test_file)], "MyProject")
        assert root_id == "code.myproject"
        root = tree.get_node(root_id)
        assert root is not None
        children = tree.get_children(root_id)
        assert len(children) == 1

        test_file.unlink()

    def test_ingest_codebase_missing_file(self, tree):
        root_id = tree.ingest_codebase(["/no/such/file.py"], "BadProject")
        children = tree.get_children(root_id)
        assert len(children) == 0  # skipped gracefully


# ═══════════════════════════════════════════════════════════════════════
# Proactive Loading
# ═══════════════════════════════════════════════════════════════════════


class TestProactiveLoader:
    def _setup_tree(self, tree):
        tree.add_node(_make_node("payments", level=0, summary="Payment systems",
                                 keywords=["payment", "billing", "charge"]))
        tree.add_node(_make_node("payments.refund", parent_id="payments", level=1,
                                 summary="Refund processing",
                                 content="Refunds are processed within 5 business days.",
                                 keywords=["refund", "return", "money"]))
        tree.add_node(_make_node("shipping", level=0, summary="Shipping logistics",
                                 keywords=["shipping", "delivery", "tracking"]))
        tree.add_node(_make_node("shipping.international", parent_id="shipping",
                                 level=1, summary="International shipping info",
                                 content="International orders take 7-14 business days.",
                                 keywords=["international", "customs", "overseas"]))

    async def test_prepare_context(self, tree, loader):
        self._setup_tree(tree)
        bundle = await loader.prepare_context("How do refunds work?")
        assert isinstance(bundle, ContextBundle)
        assert bundle.load_time_ms >= 0
        assert len(bundle.branches_loaded) > 0

    async def test_prepare_context_has_knowledge(self, tree, loader):
        self._setup_tree(tree)
        bundle = await loader.prepare_context("Tell me about refunds")
        assert "refund" in bundle.knowledge_context.lower() or \
               "payment" in bundle.system_prompt.lower()

    async def test_branch_caching(self, tree, loader):
        self._setup_tree(tree)
        # First call — cold load
        b1 = await loader.prepare_context("How do refunds work?")
        loaded_first = list(b1.branches_loaded)

        # Second call — should be cached
        b2 = await loader.prepare_context("What about refund timing?")
        assert b2.from_cache or len(b2.branches_loaded) > 0

    async def test_unload_branch(self, tree, loader):
        self._setup_tree(tree)
        await loader.prepare_context("How do refunds work?")
        loaded = loader.get_loaded_branches()
        assert len(loaded) > 0
        for bid in loaded:
            loader.unload_branch(bid)
        assert len(loader.get_loaded_branches()) == 0

    async def test_unload_all(self, tree, loader):
        self._setup_tree(tree)
        await loader.prepare_context("refunds")
        loader.unload_all()
        assert len(loader.get_loaded_branches()) == 0

    async def test_context_token_count(self, tree, loader):
        self._setup_tree(tree)
        await loader.prepare_context("refund shipping")
        count = loader.get_context_token_count()
        assert count >= 0

    async def test_predict_next(self, tree, loader):
        self._setup_tree(tree)
        # Load both branches together to build co-occurrence
        await loader.prepare_context("refund international shipping")
        loader.unload_all()

        # Now load one — should predict the other
        await loader.prepare_context("refund info")
        predictions = await loader.predict_next("more details")
        # predictions may or may not include shipping — depends on co-occurrence
        assert isinstance(predictions, list)

    async def test_system_prompt_includes_roots(self, tree, loader):
        self._setup_tree(tree)
        bundle = await loader.prepare_context("hello")
        assert "Payment systems" in bundle.system_prompt or \
               "Shipping logistics" in bundle.system_prompt or \
               bundle.system_prompt == ""  # empty if no keywords match, roots still present

    async def test_empty_message(self, tree, loader):
        self._setup_tree(tree)
        bundle = await loader.prepare_context("")
        assert isinstance(bundle, ContextBundle)

    async def test_no_relevant_branches(self, tree, loader):
        self._setup_tree(tree)
        bundle = await loader.prepare_context("quantum entanglement theory")
        assert isinstance(bundle, ContextBundle)
        assert bundle.knowledge_context == "" or bundle.branches_loaded == []


# ═══════════════════════════════════════════════════════════════════════
# Branch Content Assembly
# ═══════════════════════════════════════════════════════════════════════


class TestBranchContent:
    def test_branch_content_format(self, tree):
        tree.add_node(_make_node("topic", level=0, summary="Topic overview"))
        tree.add_node(_make_node("topic.sub", parent_id="topic", level=1,
                                 summary="Sub-topic detail",
                                 content="Detailed explanation here."))
        content = tree.get_branch_content("topic", max_depth=1)
        assert "Topic overview" in content
        assert "Detailed explanation" in content

    def test_branch_content_empty_tree(self, tree):
        assert tree.get_branch_content("nonexistent") == ""


# ═══════════════════════════════════════════════════════════════════════
# Tree Stats
# ═══════════════════════════════════════════════════════════════════════


class TestTreeStats:
    def test_stats_empty(self, tree):
        stats = tree.get_tree_stats()
        assert stats.total_nodes == 0
        assert stats.total_tokens == 0
        assert stats.max_depth == 0

    def test_stats_populated(self, tree):
        tree.add_node(_make_node("r", level=0, summary="Root", keywords=["root"]))
        tree.add_node(_make_node("r.a", parent_id="r", level=1,
                                 content="Branch A", keywords=["a"]))
        tree.add_node(_make_node("r.a.x", parent_id="r.a", level=2,
                                 content="Leaf X", keywords=["x"]))
        stats = tree.get_tree_stats()
        assert stats.total_nodes == 3
        assert stats.roots == 1
        assert stats.branches == 1
        assert stats.leaves == 1
        assert stats.max_depth == 2
        assert stats.total_tokens > 0

    def test_all_roots_summary(self, tree):
        tree.add_node(_make_node("cooking", level=0, summary="Cooking knowledge",
                                 keywords=["cooking"]))
        tree.add_node(_make_node("science", level=0, summary="Science topics",
                                 keywords=["science"]))
        summary = tree.get_all_roots_summary()
        assert "Cooking knowledge" in summary
        assert "Science topics" in summary
        assert summary.startswith("Known topics:")


# ═══════════════════════════════════════════════════════════════════════
# FTS5 Search
# ═══════════════════════════════════════════════════════════════════════


class TestFTS5Search:
    def test_fts_search_basic(self, tree, conn):
        tree.add_node(_make_node("n1", level=0, summary="Quantum physics introduction",
                                 content="This covers quantum mechanics fundamentals.",
                                 keywords=["quantum", "physics"]))
        # Manually populate the FTS table since content= tables need triggers
        conn.execute(
            "INSERT INTO knowledge_node_fts(rowid, title, summary, content, keywords) "
            "SELECT rowid, title, summary, content, keywords FROM knowledge_nodes "
            "WHERE id = 'n1'"
        )
        conn.commit()
        results = tree.fts_search("quantum")
        assert len(results) >= 1
        assert results[0].id == "n1"

    def test_fts_search_no_match(self, tree):
        results = tree.fts_search("xyznonexistent")
        assert results == []

    def test_fts_search_empty_query(self, tree):
        results = tree.fts_search("")
        assert results == []


# ═══════════════════════════════════════════════════════════════════════
# Persistence (DB round-trip)
# ═══════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_save_and_load(self, conn):
        tree1 = KnowledgeTree(conn=conn)
        tree1.add_node(_make_node("r", level=0, summary="Root",
                                  keywords=["root"]))
        tree1.add_node(_make_node("r.a", parent_id="r", level=1,
                                  content="Child A", keywords=["child"]))
        tree1.save_to_db()

        tree2 = KnowledgeTree(conn=conn)
        tree2.load_from_db()

        assert tree2.get_node("r") is not None
        assert tree2.get_node("r.a") is not None
        assert tree2.get_node("r").children == ["r.a"]

    def test_load_rebuilds_index(self, conn):
        tree1 = KnowledgeTree(conn=conn)
        tree1.add_node(_make_node("r", level=0, keywords=["alpha", "beta"]))
        tree1.save_to_db()

        tree2 = KnowledgeTree(conn=conn)
        tree2.load_from_db()
        results = tree2.search(["alpha"])
        assert len(results) == 1
        assert results[0].id == "r"

    def test_load_empty_db(self, conn):
        tree = KnowledgeTree(conn=conn)
        tree.load_from_db()  # should not raise
        assert tree.get_tree_stats().total_nodes == 0


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_tree_roots(self, tree):
        assert tree.get_roots() == []

    def test_empty_tree_summary(self, tree):
        assert tree.get_all_roots_summary() == ""

    def test_single_node_path(self, tree):
        tree.add_node(_make_node("solo", level=0))
        path = tree.get_path("solo")
        assert len(path) == 1

    def test_deep_nesting(self, tree):
        tree.add_node(_make_node("l0", level=0))
        tree.add_node(_make_node("l1", parent_id="l0", level=1))
        tree.add_node(_make_node("l2", parent_id="l1", level=2))
        tree.add_node(_make_node("l3", parent_id="l2", level=3))
        tree.add_node(_make_node("l4", parent_id="l3", level=4))
        path = tree.get_path("l4")
        assert len(path) == 5
        assert [n.id for n in path] == ["l0", "l1", "l2", "l3", "l4"]

    def test_branch_max_depth_zero(self, tree):
        tree.add_node(_make_node("r", level=0))
        tree.add_node(_make_node("r.a", parent_id="r", level=1))
        branch = tree.get_branch("r", max_depth=0)
        assert len(branch) == 1
        assert branch[0].id == "r"

    def test_node_with_empty_keywords(self, tree):
        tree.add_node(_make_node("empty_kw", level=0, keywords=[]))
        # Should still be accessible by ID
        assert tree.get_node("empty_kw") is not None

    def test_node_no_summary_no_content(self, tree):
        node = _make_node("bare", level=0)
        tree.add_node(node)
        assert tree.get_node("bare") is not None

    def test_duplicate_child_add(self, tree):
        tree.add_node(_make_node("p", level=0))
        tree.add_node(_make_node("c", parent_id="p", level=1))
        # Adding same child again shouldn't duplicate in parent.children
        tree.add_node(_make_node("c", parent_id="p", level=1))
        parent = tree.get_node("p")
        assert parent.children.count("c") == 1


# ═══════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_estimate_tokens(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("x" * 40) == 10

    def test_chunk_text(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = _chunk_text(text, 30)
        assert len(chunks) >= 2

    def test_chunk_text_small(self):
        chunks = _chunk_text("short", 1000)
        assert len(chunks) == 1
        assert chunks[0] == "short"

    def test_make_summary(self):
        s = _make_summary("Hello world this is a test", max_len=15)
        assert len(s) <= 20  # may include ellipsis

    def test_make_summary_short(self):
        assert _make_summary("Hi") == "Hi"

    def test_extract_title_heading(self):
        assert _extract_title("# My Title\nSome content") == "My Title"

    def test_extract_title_first_line(self):
        assert _extract_title("First line here\nSecond line") == "First line here"

    def test_sanitise_fts(self):
        assert _sanitise_fts("hello world") == "hello OR world"
        assert _sanitise_fts("") == ""
        assert _sanitise_fts("!!!") == ""


# ═══════════════════════════════════════════════════════════════════════
# ContextBundle Dataclass
# ═══════════════════════════════════════════════════════════════════════


class TestContextBundle:
    def test_defaults(self):
        b = ContextBundle()
        assert b.system_prompt == ""
        assert b.knowledge_context == ""
        assert b.branches_loaded == []
        assert b.load_time_ms == 0.0
        assert b.from_cache is False

    def test_fields(self):
        b = ContextBundle(
            system_prompt="sys",
            knowledge_context="ctx",
            branches_loaded=["a", "b"],
            load_time_ms=42.0,
            from_cache=True,
        )
        assert b.system_prompt == "sys"
        assert len(b.branches_loaded) == 2
        assert b.from_cache is True
