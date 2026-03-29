"""CAG (Cache-Augmented Generation) — Pre-computed knowledge injection for Atlas Cortex.

Two operating modes:
- 'ollama': Retrieves indexed knowledge text and injects into system prompt
- 'transformers': Pre-computes KV caches for zero-token knowledge injection

Usage:
    engine = CAGEngine()
    await engine.init()

    # Index a document
    bank_id = await engine.index_document("medical_guide.txt", text, tags=["medical"])

    # Retrieve knowledge for a query
    knowledge = await engine.recall(query="What is NeoCardiol's dosing?", top_k=3)

    # In transformers mode, get KV cache
    cache, prefix_len = await engine.get_cache(bank_id, model_name="Qwen/Qwen3-4B")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Optional heavy deps
_HAS_TORCH = False
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache
    _HAS_TORCH = True
except ImportError:
    pass

CAG_MODE = os.environ.get("CAG_MODE", "ollama")  # 'ollama' or 'transformers'
CACHE_DIR = Path(os.environ.get("CAG_CACHE_DIR", "./data/kv_caches"))


@dataclass
class KnowledgeBank:
    """Metadata for an indexed knowledge document."""
    bank_id: str
    title: str
    source_hash: str
    text: str
    tags: list[str] = field(default_factory=list)
    token_count: int = 0
    cache_path: str | None = None
    model_name: str | None = None
    created_at: float = 0.0
    last_accessed: float = 0.0


@dataclass
class CAGRecall:
    """Result from a CAG (Cache-Augmented Generation) recall query."""
    bank_id: str
    title: str
    text: str
    score: float
    tags: list[str] = field(default_factory=list)
    cache_available: bool = False


class CAGEngine:
    """CAG (Cache-Augmented Generation) — pre-computed knowledge injection system."""

    def __init__(self, db_conn=None, mode: str | None = None):
        self._mode = mode or CAG_MODE
        self._db = db_conn
        self._model = None
        self._tokenizer = None
        self._model_name: str | None = None

    async def init(self, db_conn=None):
        """Initialize database tables and cache directory."""
        if db_conn:
            self._db = db_conn
        if self._db:
            _ensure_tables(self._db)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("CAG engine initialized (mode=%s)", self._mode)

    @property
    def mode(self) -> str:
        return self._mode

    # ── Document Indexing ────────────────────────────────────────────

    async def index_document(
        self,
        title: str,
        text: str,
        tags: list[str] | None = None,
        build_cache: bool = True,
        model_name: str | None = None,
    ) -> str:
        """Index a document into the CAG engine.

        Returns the bank_id (content hash).
        """
        source_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        bank_id = f"cag_{source_hash}"
        tags = tags or []
        now = time.time()

        if self._db:
            self._db.execute(
                """INSERT OR REPLACE INTO kv_cache_banks
                   (bank_id, title, source_hash, text, tags, token_count,
                    model_name, created_at, last_accessed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bank_id, title, source_hash, text, json.dumps(tags),
                 len(text.split()), model_name, now, now),
            )
            # Also index in FTS for search
            self._db.execute(
                "DELETE FROM cag_fts WHERE bank_id = ?", (bank_id,)
            )
            self._db.execute(
                """INSERT INTO cag_fts (bank_id, title, text, tags)
                   VALUES (?, ?, ?, ?)""",
                (bank_id, title, text, json.dumps(tags)),
            )
            self._db.commit()

        # Build KV cache if in transformers mode
        if build_cache and self._mode == "transformers" and _HAS_TORCH:
            await self._build_and_store_cache(bank_id, text, model_name)

        logger.info("Indexed document '%s' as %s (%d words)",
                     title, bank_id, len(text.split()))
        return bank_id

    async def _build_and_store_cache(
        self, bank_id: str, text: str, model_name: str | None = None,
    ):
        """Build KV cache from text and save to disk."""
        if not _HAS_TORCH:
            logger.warning("PyTorch not available — cannot build KV cache")
            return

        model_name = model_name or os.environ.get("CAG_MODEL", "Qwen/Qwen3-4B")
        self._ensure_model_loaded(model_name)

        msgs = [{"role": "system", "content": text}]
        prefix = self._tokenizer.apply_chat_template(
            msgs, tokenize=False, enable_thinking=False,
        )
        ids = self._tokenizer(prefix, return_tensors="pt").input_ids.to("cuda")
        prefix_len = ids.shape[1]

        with torch.no_grad():
            out = self._model(ids, use_cache=True)
        cache = out.past_key_values

        # Serialize cache
        cache_dir = CACHE_DIR / model_name.replace("/", "_") / bank_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "cache.pt"

        cache_data: dict[str, Any] = {"prefix_len": prefix_len, "layers": []}
        if hasattr(cache, "key_cache"):
            for i in range(len(cache.key_cache)):
                if cache.key_cache[i] is not None:
                    cache_data["layers"].append({
                        "keys": cache.key_cache[i].cpu(),
                        "values": cache.value_cache[i].cpu(),
                    })

        torch.save(cache_data, str(cache_path))
        cache_size = cache_path.stat().st_size

        if self._db:
            self._db.execute(
                """UPDATE kv_cache_banks
                   SET cache_path = ?, model_name = ?, token_count = ?
                   WHERE bank_id = ?""",
                (str(cache_path), model_name, prefix_len, bank_id),
            )
            self._db.commit()

        logger.info("Built KV cache for %s: %d tokens, %.1f MB",
                     bank_id, prefix_len, cache_size / 1e6)

    def _ensure_model_loaded(self, model_name: str):
        """Lazy-load the model for cache building."""
        if self._model_name == model_name and self._model is not None:
            return
        if self._model is not None:
            del self._model
            del self._tokenizer
            import gc
            gc.collect()
            torch.cuda.empty_cache()

        logger.info("Loading model %s for CAG engine...", model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_name, dtype=torch.float16,
        ).to("cuda")
        self._model.eval()
        self._model_name = model_name

    # ── Knowledge Retrieval ──────────────────────────────────────────

    async def recall(
        self,
        query: str,
        top_k: int = 3,
        tags: list[str] | None = None,
    ) -> list[CAGRecall]:
        """Retrieve relevant knowledge banks for a query.

        Uses FTS5 BM25 matching against indexed document text.
        """
        if not self._db:
            return []

        safe_query = _fts_sanitize(query)
        if not safe_query:
            return []

        tag_filter = ""
        params: list[Any] = [safe_query]
        if tags:
            tag_conditions = " OR ".join("tags LIKE ?" for _ in tags)
            tag_filter = f"AND f.bank_id IN (SELECT bank_id FROM kv_cache_banks WHERE {tag_conditions})"
            params.extend(f"%{t}%" for t in tags)

        rows = self._db.execute(
            f"""SELECT f.bank_id, f.title, f.text, f.tags,
                       b.cache_path,
                       bm25(cag_fts) AS score
                FROM cag_fts f
                LEFT JOIN kv_cache_banks b ON b.bank_id = f.bank_id
                WHERE cag_fts MATCH ?
                {tag_filter}
                ORDER BY score
                LIMIT ?""",
            (*params, top_k),
        ).fetchall()

        results = []
        now = time.time()
        for row in rows:
            bank_id = row["bank_id"]
            self._db.execute(
                "UPDATE kv_cache_banks SET last_accessed = ? WHERE bank_id = ?",
                (now, bank_id),
            )
            results.append(CAGRecall(
                bank_id=bank_id,
                title=row["title"],
                text=row["text"],
                score=abs(row["score"]),
                tags=json.loads(row["tags"]) if row["tags"] else [],
                cache_available=bool(row["cache_path"]),
            ))
        if results:
            self._db.commit()

        return results

    async def get_cache(self, bank_id: str, model_name: str | None = None):
        """Load a pre-computed KV cache from disk.

        Returns (DynamicCache, prefix_len) or (None, 0) if not available.
        """
        if not _HAS_TORCH:
            return None, 0

        if not self._db:
            return None, 0

        row = self._db.execute(
            "SELECT cache_path, model_name FROM kv_cache_banks WHERE bank_id = ?",
            (bank_id,),
        ).fetchone()

        if not row or not row["cache_path"]:
            return None, 0

        cache_path = Path(row["cache_path"])
        if not cache_path.exists():
            logger.warning("Cache file missing: %s", cache_path)
            return None, 0

        data = torch.load(str(cache_path), map_location="cuda", weights_only=False)
        prefix_len = data["prefix_len"]

        cache = DynamicCache()
        for layer_data in data["layers"]:
            cache.update(
                layer_data["keys"].to("cuda"),
                layer_data["values"].to("cuda"),
                len(cache) if hasattr(cache, "__len__") else 0,
            )

        return cache, prefix_len

    def clone_cache(self, cache):
        """Deep clone a DynamicCache for reuse."""
        if not _HAS_TORCH:
            return None
        c = DynamicCache()
        if hasattr(cache, "key_cache"):
            for i in range(len(cache.key_cache)):
                if cache.key_cache[i] is not None:
                    c.update(
                        cache.key_cache[i].clone(),
                        cache.value_cache[i].clone(), i,
                    )
        return c

    # ── Knowledge Context Formatting ─────────────────────────────────

    async def format_context(self, query: str, top_k: int = 3) -> str:
        """Retrieve and format knowledge as system prompt text.

        Used in 'ollama' mode where we can't inject KV caches directly.
        Returns formatted text suitable for system prompt injection.
        """
        recalls = await self.recall(query, top_k=top_k)
        if not recalls:
            return ""

        parts = []
        for r in recalls:
            text = r.text[:2000] if len(r.text) > 2000 else r.text
            parts.append(f"[{r.title}]\n{text}")

        return "\n\n".join(parts)

    # ── Management ───────────────────────────────────────────────────

    async def list_banks(self) -> list[KnowledgeBank]:
        """List all indexed knowledge banks."""
        if not self._db:
            return []
        rows = self._db.execute(
            """SELECT bank_id, title, source_hash, text, tags, token_count,
                      cache_path, model_name, created_at, last_accessed
               FROM kv_cache_banks ORDER BY last_accessed DESC""",
        ).fetchall()
        return [
            KnowledgeBank(
                bank_id=r["bank_id"],
                title=r["title"],
                source_hash=r["source_hash"],
                text=r["text"][:200] + "..." if len(r["text"]) > 200 else r["text"],
                tags=json.loads(r["tags"]) if r["tags"] else [],
                token_count=r["token_count"],
                cache_path=r["cache_path"],
                model_name=r["model_name"],
                created_at=r["created_at"],
                last_accessed=r["last_accessed"],
            )
            for r in rows
        ]

    async def delete_bank(self, bank_id: str) -> bool:
        """Delete a knowledge bank and its cache."""
        if not self._db:
            return False

        row = self._db.execute(
            "SELECT cache_path FROM kv_cache_banks WHERE bank_id = ?", (bank_id,),
        ).fetchone()
        if not row:
            return False

        if row["cache_path"]:
            cache_path = Path(row["cache_path"])
            if cache_path.exists():
                cache_path.unlink()

        self._db.execute("DELETE FROM kv_cache_banks WHERE bank_id = ?", (bank_id,))
        self._db.execute("DELETE FROM cag_fts WHERE bank_id = ?", (bank_id,))
        self._db.commit()
        return True

    async def get_stats(self) -> dict:
        """Return CAG engine statistics."""
        if not self._db:
            return {"mode": self._mode, "banks": 0}

        row = self._db.execute(
            "SELECT COUNT(*) as cnt, SUM(token_count) as tokens FROM kv_cache_banks",
        ).fetchone()
        cache_row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM kv_cache_banks WHERE cache_path IS NOT NULL",
        ).fetchone()

        return {
            "mode": self._mode,
            "banks": row["cnt"],
            "total_tokens": row["tokens"] or 0,
            "caches_built": cache_row["cnt"],
            "cache_dir": str(CACHE_DIR),
            "torch_available": _HAS_TORCH,
        }


# ── Module-level singleton ───────────────────────────────────────────

_cag_engine: CAGEngine | None = None


def get_cag_engine() -> CAGEngine | None:
    return _cag_engine


def set_cag_engine(engine: CAGEngine) -> None:
    global _cag_engine
    _cag_engine = engine


# ── Database helpers ─────────────────────────────────────────────────

def _ensure_tables(conn):
    """Create CAG (Cache-Augmented Generation) tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv_cache_banks (
            bank_id     TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            text        TEXT NOT NULL,
            tags        TEXT DEFAULT '[]',
            token_count INTEGER DEFAULT 0,
            cache_path  TEXT,
            model_name  TEXT,
            created_at  REAL DEFAULT 0,
            last_accessed REAL DEFAULT 0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS cag_fts USING fts5(
            bank_id, title, text, tags,
            tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS cag_usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_id     TEXT NOT NULL,
            query       TEXT,
            mode        TEXT,
            tokens_saved INTEGER DEFAULT 0,
            latency_ms  REAL DEFAULT 0,
            created_at  REAL DEFAULT 0
        );
    """)
    conn.commit()


def _fts_sanitize(query: str) -> str:
    """Sanitize a free-text string for safe use in FTS5 MATCH."""
    clean = ""
    for ch in query:
        if ch.isalnum() or ch in " _-":
            clean += ch
        else:
            clean += " "
    tokens = [t.strip() for t in clean.split() if len(t.strip()) >= 2]
    if not tokens:
        return ""
    return " OR ".join(tokens)
