"""
db.py — SQLite recommendation log

Stores every recommendation request with full debug info.
Query with: sqlite3 recommendations.db "SELECT * FROM recommendation_logs ORDER BY created_at DESC LIMIT 10"
"""

import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.getenv("REC_LOG_DB", "recommendations.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_domain TEXT NOT NULL,
            query TEXT,
            query_type TEXT,
            merge_order TEXT NOT NULL,
            purchased_ids TEXT,
            collab_count INTEGER NOT NULL DEFAULT 0,
            tag_count INTEGER NOT NULL DEFAULT 0,
            final_count INTEGER NOT NULL DEFAULT 0,
            final_picks TEXT,
            browse_boost_map TEXT,
            collab_candidates TEXT,
            tag_candidates TEXT,
            full_debug TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_shop_domain 
        ON recommendation_logs(shop_domain)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_created_at 
        ON recommendation_logs(created_at)
    """)
    conn.commit()
    conn.close()


def log_recommendation(
    shop_domain: str,
    query: str | None,
    query_type: str | None,
    merge_order: str,
    purchased_ids: list,
    collab_recs: list,
    tag_recs: list,
    browse_boost: dict,
    merged: list,
    debug_info: dict | None = None,
):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO recommendation_logs (
            shop_domain, query, query_type, merge_order,
            purchased_ids, collab_count, tag_count, final_count,
            final_picks, browse_boost_map, collab_candidates,
            tag_candidates, full_debug, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shop_domain,
            query,
            query_type,
            merge_order,
            json.dumps(purchased_ids),
            len(collab_recs),
            len(tag_recs),
            len(merged),
            json.dumps([r["id"] for r in merged]),
            json.dumps(browse_boost),
            json.dumps([{"id": r["id"], "title": r["title"], "source": r.get("source"), "score": r.get("score", 0)} for r in collab_recs]),
            json.dumps([{"id": r["id"], "title": r["title"], "source": r.get("source"), "score": r.get("score", 0)} for r in tag_recs]),
            json.dumps(debug_info) if debug_info else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_recent_logs(shop_domain: str = None, limit: int = 20) -> list:
    conn = _get_conn()
    if shop_domain:
        rows = conn.execute(
            "SELECT * FROM recommendation_logs WHERE shop_domain = ? ORDER BY created_at DESC LIMIT ?",
            (shop_domain, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recommendation_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# Init on import
init_db()
