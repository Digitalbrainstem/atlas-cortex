"""Hierarchical knowledge storage with O(1) lookup and branch loading.

The ``KnowledgeTree`` organises knowledge into a rooted tree with four
conceptual layers:

* **Level 0 — Roots** : topic categories (summaries always resident).
* **Level 1 — Branches** : sub-topics loaded on demand.
* **Level 2 — Leaves** : full content chunks.
* **Level 3+ — Detail** : deeply nested detail nodes.

Nodes are persisted in the ``knowledge_nodes`` SQLite table and indexed by
an in-memory :class:`MemoryIndex` for sub-millisecond keyword look-ups.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cortex.db import get_db
from cortex.memory.memory_index import MemoryIndex

log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # rough heuristic (matches compaction.py)

# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class TreeNode:
    """A single node in the knowledge tree."""

    id: str
    parent_id: str | None
    title: str
    summary: str
    content: str
    token_count: int
    level: int
    children: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TreeStats:
    """Aggregate statistics for the tree."""

    total_nodes: int
    total_tokens: int
    max_depth: int
    roots: int
    branches: int
    leaves: int


# ── Main class ──────────────────────────────────────────────────────────


class KnowledgeTree:
    """Hierarchical knowledge storage with O(1) lookup and branch loading."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn
        self._index = MemoryIndex()
        self._nodes: dict[str, TreeNode] = {}  # in-memory cache

    @property
    def index(self) -> MemoryIndex:
        return self._index

    def _get_conn(self) -> sqlite3.Connection:
        return self._conn or get_db()

    # ── Tree Management ─────────────────────────────────────────────────

    def add_node(self, node: TreeNode) -> None:
        """Insert a node into the tree (memory + DB)."""
        now = _now_iso()
        node.created_at = node.created_at or now
        node.updated_at = node.updated_at or now
        if not node.token_count:
            node.token_count = _estimate_tokens(node.summary + " " + node.content)

        self._nodes[node.id] = node
        self._index.add(node.id, node.keywords)

        # Update parent's children list
        if node.parent_id and node.parent_id in self._nodes:
            parent = self._nodes[node.parent_id]
            if node.id not in parent.children:
                parent.children.append(node.id)

        self._persist_node(node)

    def update_node(self, node_id: str, content: str) -> None:
        """Update the content of an existing node."""
        node = self._nodes.get(node_id)
        if node is None:
            return
        node.content = content
        node.token_count = _estimate_tokens(node.summary + " " + content)
        node.updated_at = _now_iso()
        self._persist_node(node)

    def delete_node(self, node_id: str) -> None:
        """Delete a node and its descendants recursively."""
        node = self._nodes.get(node_id)
        if node is None:
            return

        # Delete children first (depth-first)
        for child_id in list(node.children):
            self.delete_node(child_id)

        # Remove from parent's children list
        if node.parent_id and node.parent_id in self._nodes:
            parent = self._nodes[node.parent_id]
            if node.id in parent.children:
                parent.children.remove(node.id)

        self._index.remove(node_id)
        del self._nodes[node_id]

        conn = self._get_conn()
        conn.execute("DELETE FROM knowledge_nodes WHERE id = ?", (node_id,))
        conn.commit()

    def get_node(self, node_id: str) -> TreeNode | None:
        return self._nodes.get(node_id)

    def get_children(self, node_id: str) -> list[TreeNode]:
        node = self._nodes.get(node_id)
        if node is None:
            return []
        return [self._nodes[cid] for cid in node.children if cid in self._nodes]

    def get_path(self, node_id: str) -> list[TreeNode]:
        """Return the path from the root down to the given node."""
        path: list[TreeNode] = []
        current = self._nodes.get(node_id)
        while current:
            path.append(current)
            current = self._nodes.get(current.parent_id) if current.parent_id else None
        path.reverse()
        return path

    # ── Tree Traversal ──────────────────────────────────────────────────

    def get_roots(self) -> list[TreeNode]:
        """Return all Level 0 nodes."""
        return [n for n in self._nodes.values() if n.level == 0]

    def get_branch(self, node_id: str, max_depth: int = 2) -> list[TreeNode]:
        """Return a subtree rooted at *node_id*, up to *max_depth* levels."""
        root = self._nodes.get(node_id)
        if root is None:
            return []
        result: list[TreeNode] = []
        self._collect_branch(root, 0, max_depth, result)
        return result

    def get_branch_content(self, node_id: str, max_depth: int = 2) -> str:
        """Return concatenated text from a branch for prompt injection."""
        nodes = self.get_branch(node_id, max_depth)
        parts: list[str] = []
        for node in nodes:
            indent = "  " * node.level
            if node.summary:
                parts.append(f"{indent}## {node.title}\n{indent}{node.summary}")
            if node.content:
                parts.append(f"{indent}{node.content}")
        return "\n\n".join(parts)

    # ── Index Operations ────────────────────────────────────────────────

    def search(self, keywords: list[str]) -> list[TreeNode]:
        """Keyword → nodes via inverted index."""
        hits = self._index.search(keywords)
        return [self._nodes[nid] for nid, _ in hits if nid in self._nodes]

    def get_relevant_branches(
        self,
        message: str,
        max_branches: int = 5,
    ) -> list[str]:
        """Return branch root IDs most relevant to a user message."""
        keywords = self._index.extract_keywords(message)
        if not keywords:
            return []

        hits = self._index.search(keywords, top_k=max_branches * 3)
        # Walk each hit up to its branch root (level 0 or 1)
        branch_ids: list[str] = []
        seen: set[str] = set()
        for nid, _score in hits:
            branch_root = self._find_branch_root(nid)
            if branch_root and branch_root not in seen:
                seen.add(branch_root)
                branch_ids.append(branch_root)
                if len(branch_ids) >= max_branches:
                    break
        return branch_ids

    # ── Bulk Operations ─────────────────────────────────────────────────

    def get_all_roots_summary(self) -> str:
        """Concatenated root summaries for the system prompt residual layer."""
        roots = self.get_roots()
        if not roots:
            return ""
        parts = [f"• {r.title}: {r.summary}" for r in roots if r.summary]
        return "Known topics:\n" + "\n".join(parts)

    def get_tree_stats(self) -> TreeStats:
        total_tokens = 0
        max_depth = 0
        branches = 0
        leaves = 0
        roots = 0
        for node in self._nodes.values():
            total_tokens += node.token_count
            if node.level > max_depth:
                max_depth = node.level
            if node.level == 0:
                roots += 1
            elif node.children:
                branches += 1
            else:
                leaves += 1
        return TreeStats(
            total_nodes=len(self._nodes),
            total_tokens=total_tokens,
            max_depth=max_depth,
            roots=roots,
            branches=branches,
            leaves=leaves,
        )

    # ── Persistence ─────────────────────────────────────────────────────

    def save_to_db(self) -> None:
        """Persist all in-memory nodes to the database."""
        conn = self._get_conn()
        for node in self._nodes.values():
            self._persist_node(node, conn=conn)
        conn.commit()

    def load_from_db(self) -> None:
        """Load all nodes from the database into memory and rebuild index."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, parent_id, title, summary, content, token_count, "
                "level, keywords, created_at, updated_at FROM knowledge_nodes"
            ).fetchall()
        except sqlite3.OperationalError:
            log.warning("knowledge_nodes table not found — starting with empty tree")
            return

        self._nodes.clear()
        self._index = MemoryIndex()

        for row in rows:
            kw_raw = row["keywords"] if row["keywords"] else "[]"
            try:
                keywords = json.loads(kw_raw)
            except (json.JSONDecodeError, TypeError):
                keywords = []

            node = TreeNode(
                id=row["id"],
                parent_id=row["parent_id"],
                title=row["title"],
                summary=row["summary"] or "",
                content=row["content"] or "",
                token_count=row["token_count"] or 0,
                level=row["level"] or 0,
                children=[],
                keywords=keywords,
                created_at=row["created_at"] or "",
                updated_at=row["updated_at"] or "",
            )
            self._nodes[node.id] = node

        # Rebuild children lists from parent_id references
        for node in self._nodes.values():
            if node.parent_id and node.parent_id in self._nodes:
                parent = self._nodes[node.parent_id]
                if node.id not in parent.children:
                    parent.children.append(node.id)

        self.build_index()
        log.info("Loaded %d knowledge nodes from DB", len(self._nodes))

    def build_index(self) -> None:
        """Build the keyword → node_id inverted index from in-memory nodes."""
        self._index = MemoryIndex()
        for node in self._nodes.values():
            if node.keywords:
                self._index.add(node.id, node.keywords)

    # ── Knowledge Ingestion ─────────────────────────────────────────────

    def ingest_document(
        self,
        text: str,
        category: str,
        chunk_size: int = 2000,
    ) -> str:
        """Split *text* into chunks and add as nodes under *category*.

        Returns the root node ID for the ingested document.
        """
        root_id = category.lower().replace(" ", "_")

        # Create or find root
        if root_id not in self._nodes:
            root = TreeNode(
                id=root_id,
                parent_id=None,
                title=category,
                summary=_make_summary(text),
                content="",
                token_count=0,
                level=0,
                keywords=self._index.extract_keywords(category),
            )
            self.add_node(root)

        # Chunk the text
        chunks = _chunk_text(text, chunk_size)
        # Determine offset so repeated ingestions don't collide
        existing_children = self.get_children(root_id)
        offset = len(existing_children)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{root_id}.chunk_{offset + i}"
            chunk_keywords = self._index.extract_keywords(chunk)
            chunk_title = _extract_title(chunk) or f"Section {offset + i + 1}"
            node = TreeNode(
                id=chunk_id,
                parent_id=root_id,
                title=chunk_title,
                summary=_make_summary(chunk),
                content=chunk,
                token_count=_estimate_tokens(chunk),
                level=1,
                keywords=chunk_keywords,
            )
            self.add_node(node)

        return root_id

    def ingest_conversation(
        self,
        turns: list,
        session_id: str,
    ) -> str:
        """Distil conversation turns into tree nodes.

        Returns the root node ID.
        """
        root_id = f"conversation.{session_id}"
        combined = "\n".join(
            f"{getattr(t, 'role', 'user')}: {getattr(t, 'content', str(t))}"
            for t in turns
        )
        summary = _make_summary(combined)
        keywords = self._index.extract_keywords(combined)

        root = TreeNode(
            id=root_id,
            parent_id=None,
            title=f"Conversation {session_id[:8]}",
            summary=summary,
            content="",
            token_count=0,
            level=0,
            keywords=keywords[:20],
        )
        self.add_node(root)

        # Add a single leaf with full content
        leaf = TreeNode(
            id=f"{root_id}.full",
            parent_id=root_id,
            title="Full transcript",
            summary="",
            content=combined,
            token_count=_estimate_tokens(combined),
            level=1,
            keywords=keywords[:20],
        )
        self.add_node(leaf)
        return root_id

    def ingest_codebase(
        self,
        file_paths: list[str],
        project_name: str,
    ) -> str:
        """Index source files under a project root.

        Returns the root node ID.
        """
        root_id = f"code.{project_name.lower().replace(' ', '_')}"

        root = TreeNode(
            id=root_id,
            parent_id=None,
            title=project_name,
            summary=f"Source code for {project_name} ({len(file_paths)} files)",
            content="",
            token_count=0,
            level=0,
            keywords=self._index.extract_keywords(project_name) + ["code", "source"],
        )
        self.add_node(root)

        for fpath in file_paths:
            try:
                with open(fpath, "r", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue

            file_id = f"{root_id}.{fpath.replace('/', '.').replace(' ', '_')}"
            node = TreeNode(
                id=file_id,
                parent_id=root_id,
                title=fpath,
                summary=f"Source: {fpath}",
                content=content,
                token_count=_estimate_tokens(content),
                level=1,
                keywords=self._index.extract_keywords(fpath + " " + content[:500]),
            )
            self.add_node(node)

        return root_id

    # ── FTS5 Search ─────────────────────────────────────────────────────

    def fts_search(self, query: str, top_k: int = 10) -> list[TreeNode]:
        """Full-text search via SQLite FTS5 (porter-stemmed)."""
        conn = self._get_conn()
        safe_query = _sanitise_fts(query)
        if not safe_query:
            return []
        try:
            rows = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE rowid IN ("
                "  SELECT rowid FROM knowledge_node_fts WHERE knowledge_node_fts MATCH ?"
                ") LIMIT ?",
                (safe_query, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._nodes[r["id"]] for r in rows if r["id"] in self._nodes]

    # ── Internals ───────────────────────────────────────────────────────

    def _persist_node(
        self,
        node: TreeNode,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        conn = conn or self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id, parent_id, title, summary, content, token_count, level, keywords, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.id,
                node.parent_id,
                node.title,
                node.summary,
                node.content,
                node.token_count,
                node.level,
                json.dumps(node.keywords),
                node.created_at,
                node.updated_at,
            ),
        )
        conn.commit()

    def _collect_branch(
        self,
        node: TreeNode,
        current_depth: int,
        max_depth: int,
        out: list[TreeNode],
    ) -> None:
        out.append(node)
        if current_depth >= max_depth:
            return
        for child_id in node.children:
            child = self._nodes.get(child_id)
            if child:
                self._collect_branch(child, current_depth + 1, max_depth, out)

    def _find_branch_root(self, node_id: str) -> str | None:
        """Walk up to the nearest level-0 or level-1 ancestor."""
        node = self._nodes.get(node_id)
        while node:
            if node.level <= 1:
                return node.id
            if node.parent_id:
                node = self._nodes.get(node.parent_id)
            else:
                return node.id
        return None


# ── Free helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _estimate_tokens(text: str) -> int:
    return max(len(text) // _CHARS_PER_TOKEN, 1) if text else 0


def _chunk_text(text: str, size: int) -> list[str]:
    """Split text into chunks of approximately *size* characters on paragraph
    boundaries (double newlines) when possible."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > size and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def _make_summary(text: str, max_len: int = 200) -> str:
    """Return the first *max_len* characters as a simple summary."""
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rsplit(" ", 1)[0] + "…"


def _extract_title(text: str) -> str:
    """Pull the first heading or first sentence from a chunk."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:80]
        if line and len(line) > 5:
            return line[:80]
    return ""


def _sanitise_fts(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression."""
    tokens = re.findall(r"[a-zA-Z0-9]+", query)
    if not tokens:
        return ""
    return " OR ".join(tokens)
