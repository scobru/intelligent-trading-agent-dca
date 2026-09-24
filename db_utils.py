"""
Persistenza SQLite per il bot DCA e Ribilanciatore.
Salva storico dei ribilanciamenti, acquisti DCA, snapshot dell'equity e sentiment.
"""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(config.SQLITE_DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                total_value_usd REAL NOT NULL,
                fng_value INTEGER,
                fng_class TEXT,
                balances_json TEXT,
                weights_json TEXT
            );

            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                operation TEXT NOT NULL,
                amount_usd REAL,
                details_json TEXT,
                result_json TEXT,
                status TEXT,
                reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at);
            CREATE INDEX IF NOT EXISTS idx_operations_created ON operations(created_at);
        """)


def log_snapshot(status: Dict[str, Any]):
    try:
        init_db()
        now = time.time()
        fng = status.get("sentiment") or {}
        portfolio = status.get("portfolio") or {}
        assets = portfolio.get("assets", {})

        with _connect() as conn:
            conn.execute("""
                INSERT INTO snapshots (created_at, total_value_usd, fng_value, fng_class, balances_json, weights_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                now,
                float(status.get("total_value_usd", 0.0)),
                int(fng.get("value", 50)) if fng.get("value") is not None else None,
                str(fng.get("classification", "")),
                json.dumps(status.get("balances", {}), default=str),
                json.dumps({s: a.get("current_weight") for s, a in assets.items()}, default=str),
            ))
    except Exception as exc:
        logger.warning("Errore salvataggio snapshot a DB: %s", exc)


def log_operation(action: Dict[str, Any], result: Dict[str, Any]):
    try:
        init_db()
        now = time.time()
        op = action.get("operation", "unknown")
        amt = float(action.get("amount_usd", action.get("total_usd", 0.0)))
        status = result.get("status", "unknown")
        reason = action.get("reason", "")

        with _connect() as conn:
            conn.execute("""
                INSERT INTO operations (created_at, operation, amount_usd, details_json, result_json, status, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                now, op, amt,
                json.dumps(action, default=str),
                json.dumps(result, default=str),
                status, reason
            ))
    except Exception as exc:
        logger.warning("Errore salvataggio operazione a DB: %s", exc)


def get_recent_snapshots(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        init_db()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_recent_operations(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        init_db()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []
