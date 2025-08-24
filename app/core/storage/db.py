from __future__ import annotations

import asyncio
import csv
import sqlite3
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class TradeRow:
    id: Optional[int]
    ts: int
    pair: str
    side: str
    price: float
    qty: float
    fee: float
    pnl_realized: float


@dataclass
class BalanceRow:
    ts: int
    usdt: float
    asset_qty: float
    asset_price: float
    equity: float


class SQLiteRepo:
    def __init__(self, db_path: str = "./gridbot.sqlite3") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._migrate()

    def _migrate(self) -> None:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                fee REAL NOT NULL,
                pnl_realized REAL NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS balances (
                ts INTEGER PRIMARY KEY,
                usdt REAL NOT NULL,
                asset_qty REAL NOT NULL,
                asset_price REAL NOT NULL,
                equity REAL NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                json TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

        # ensure settings row exists
        cur.execute("INSERT OR IGNORE INTO settings (id, json) VALUES (1, '{}')")
        self._conn.commit()

    async def insert_trade(self, t: TradeRow) -> None:
        await asyncio.to_thread(self._insert_trade_sync, t)

    def _insert_trade_sync(self, t: TradeRow) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO trades (ts, pair, side, price, qty, fee, pnl_realized) VALUES (?,?,?,?,?,?,?)",
            (t.ts, t.pair, t.side, t.price, t.qty, t.fee, t.pnl_realized),
        )
        self._conn.commit()

    async def snapshot_balance(self, b: BalanceRow) -> None:
        await asyncio.to_thread(self._snapshot_balance_sync, b)

    def _snapshot_balance_sync(self, b: BalanceRow) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR REPLACE INTO balances (ts, usdt, asset_qty, asset_price, equity) VALUES (?,?,?,?,?)",
            (b.ts, b.usdt, b.asset_qty, b.asset_price, b.equity),
        )
        self._conn.commit()

    async def fetch_trades(self, limit: int = 50) -> list[TradeRow]:
        return await asyncio.to_thread(self._fetch_trades_sync, limit)

    def _fetch_trades_sync(self, limit: int) -> list[TradeRow]:
        assert self._conn is not None
        cur = self._conn.execute("SELECT id, ts, pair, side, price, qty, fee, pnl_realized FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [
            TradeRow(
                id=row["id"],
                ts=row["ts"],
                pair=row["pair"],
                side=row["side"],
                price=row["price"],
                qty=row["qty"],
                fee=row["fee"],
                pnl_realized=row["pnl_realized"],
            )
            for row in rows
        ]

    async def export_trades_csv(self, path: str) -> str:
        return await asyncio.to_thread(self._export_trades_csv_sync, path)

    def _export_trades_csv_sync(self, path: str) -> str:
        assert self._conn is not None
        cur = self._conn.execute("SELECT ts,pair,side,price,qty,fee,pnl_realized FROM trades ORDER BY id ASC")
        rows = cur.fetchall()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ts", "pair", "side", "price", "qty", "fee", "pnl_realized"])
            for r in rows:
                writer.writerow([r["ts"], r["pair"], r["side"], r["price"], r["qty"], r["fee"], r["pnl_realized"]])
        return str(dest)

    async def clear_history(self) -> None:
        await asyncio.to_thread(self._clear_history_sync)

    def _clear_history_sync(self) -> None:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM balances")
        self._conn.commit()

    async def get_settings(self) -> dict:
        return await asyncio.to_thread(self._get_settings_sync)

    def _get_settings_sync(self) -> dict:
        assert self._conn is not None
        cur = self._conn.execute("SELECT json FROM settings WHERE id=1")
        row = cur.fetchone()
        if not row:
            return {}
        try:
            return json.loads(row[0] or "{}")
        except Exception:
            return {}

    async def set_settings(self, data: dict) -> None:
        await asyncio.to_thread(self._set_settings_sync, data)

    def _set_settings_sync(self, data: dict) -> None:
        assert self._conn is not None
        js = json.dumps(data)
        self._conn.execute("INSERT OR REPLACE INTO settings (id, json) VALUES (1, ?)", (js,))
        self._conn.commit()

    async def update_settings(self, patch: dict) -> None:
        await asyncio.to_thread(self._update_settings_sync, patch)

    def _update_settings_sync(self, patch: dict) -> None:
        assert self._conn is not None
        cur = self._conn.execute("SELECT json FROM settings WHERE id=1")
        row = cur.fetchone()
        base = {}
        if row:
            try:
                base = json.loads(row[0] or "{}")
            except Exception:
                base = {}
        base.update(patch)
        js = json.dumps(base)
        self._conn.execute("INSERT OR REPLACE INTO settings (id, json) VALUES (1, ?)", (js,))
        self._conn.commit()

