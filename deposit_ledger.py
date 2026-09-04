"""Small durable SQLite ledger for Telegram casino crypto deposits.

The bot's existing JSON snapshot remains the primary application state.  This
ledger is an additional durable source for deposit identity and transaction
records, so a damaged/partial JSON write cannot silently lose a user's
pending deposit relationship.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any


_ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER_PATH = os.getenv(
    "CASINO_DEPOSIT_LEDGER",
    os.path.join(_ROOT, "casino_deposits.sqlite3"),
)
_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(LEDGER_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=20000")
    return connection


def initialize() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(LEDGER_PATH)), exist_ok=True)
    with _LOCK, _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS deposits (
                payment_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                status TEXT NOT NULL,
                required_confirmations INTEGER NOT NULL DEFAULT 2,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS deposit_transactions (
                payment_id TEXT NOT NULL,
                txid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                amount REAL NOT NULL,
                confirmations INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                first_seen_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                transaction_json TEXT NOT NULL,
                PRIMARY KEY (payment_id, txid)
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deposit_transactions_txid
            ON deposit_transactions(txid)
            """
        )
        db.commit()


def upsert_deposit(record: dict[str, Any]) -> None:
    payment_id = str(record.get("payment_id") or "").strip()
    address = str(record.get("pay_address") or record.get("deposit_address") or "").strip()
    if not payment_id or not address:
        return
    now = float(record.get("updated_at") or record.get("last_seen_at") or 0.0)
    if now <= 0:
        import time
        now = time.time()
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    with _LOCK, _connect() as db:
        db.execute(
            """
            INSERT INTO deposits (
                payment_id, user_id, coin, network, address, status,
                required_confirmations, created_at, updated_at, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                user_id=excluded.user_id,
                coin=excluded.coin,
                network=excluded.network,
                address=excluded.address,
                status=excluded.status,
                required_confirmations=excluded.required_confirmations,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                record_json=excluded.record_json
            """,
            (
                payment_id,
                str(record.get("user_id") or ""),
                str(record.get("coin") or record.get("crypto") or "").upper(),
                str(record.get("network") or "").upper(),
                address,
                str(record.get("status") or "waiting"),
                int(record.get("required_confirmations") or 2),
                float(record.get("created_at") or now),
                now,
                payload,
            ),
        )
        db.commit()


def upsert_transaction(
    payment_id: str,
    deposit: dict[str, Any],
    transaction: dict[str, Any],
) -> None:
    payment_id = str(payment_id or "").strip()
    txid = str(transaction.get("txid") or "").strip()
    if not payment_id or not txid:
        return
    import time
    now = time.time()
    existing_first_seen = float(transaction.get("first_seen_at") or now)
    payload = json.dumps(transaction, ensure_ascii=False, sort_keys=True, default=str)
    with _LOCK, _connect() as db:
        db.execute(
            """
            INSERT INTO deposit_transactions (
                payment_id, txid, user_id, coin, network, address, amount,
                confirmations, status, first_seen_at, updated_at, transaction_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(payment_id, txid) DO UPDATE SET
                confirmations=excluded.confirmations,
                status=excluded.status,
                amount=excluded.amount,
                updated_at=excluded.updated_at,
                transaction_json=excluded.transaction_json
            """,
            (
                payment_id,
                txid,
                str(deposit.get("user_id") or ""),
                str(deposit.get("coin") or deposit.get("crypto") or "").upper(),
                str(deposit.get("network") or "").upper(),
                str(deposit.get("pay_address") or deposit.get("deposit_address") or ""),
                float(transaction.get("coin_amount") or 0.0),
                int(transaction.get("confirmations") or 0),
                str(transaction.get("status") or "confirming"),
                existing_first_seen,
                now,
                payload,
            ),
        )
        db.commit()


def load_records() -> list[dict[str, Any]]:
    initialize()
    with _LOCK, _connect() as db:
        rows = db.execute(
            "SELECT record_json FROM deposits ORDER BY created_at ASC"
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row["record_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("payment_id"):
            records.append(value)
    return records


initialize()