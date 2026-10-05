"""SQLite persistence for provider prices, universe snapshots and fetch jobs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .config import FREQUENCIES
from .data import PriceRow


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "market_data.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_path(path: str | Path | None = None) -> Path:
    configured = path or os.getenv("KELLY_MARKET_DB") or DEFAULT_DB_PATH
    return Path(configured).expanduser().resolve()


class MarketStore:
    def __init__(self, path: str | Path | None = None):
        self.path = database_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    methodology TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instruments (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    exchange TEXT NOT NULL DEFAULT '',
                    asset_type TEXT NOT NULL DEFAULT 'unknown',
                    validation_status TEXT NOT NULL DEFAULT 'pending',
                    validation_note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id TEXT NOT NULL REFERENCES collections(id),
                    source_url TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    membership_as_of TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    member_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshot_members (
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL REFERENCES instruments(symbol),
                    source_symbol TEXT NOT NULL,
                    member_name TEXT NOT NULL,
                    verification_status TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY (snapshot_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS snapshot_members_symbol ON snapshot_members(symbol);
                CREATE TABLE IF NOT EXISTS prices (
                    symbol TEXT NOT NULL REFERENCES instruments(symbol),
                    frequency TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
                    observed_on TEXT NOT NULL,
                    adjusted_close REAL NOT NULL CHECK (adjusted_close > 0),
                    source TEXT NOT NULL DEFAULT 'EODHD',
                    retrieved_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, frequency, observed_on)
                );
                CREATE INDEX IF NOT EXISTS prices_symbol_freq_date ON prices(symbol, frequency, observed_on);
                CREATE TABLE IF NOT EXISTS coverage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL REFERENCES instruments(symbol),
                    frequency TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
                    requested_from TEXT NOT NULL,
                    requested_to TEXT NOT NULL,
                    first_date TEXT NOT NULL,
                    last_date TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    collections_json TEXT NOT NULL,
                    frequencies_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    total_items INTEGER NOT NULL DEFAULT 0,
                    completed_items INTEGER NOT NULL DEFAULT 0,
                    failed_items INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS job_items (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL REFERENCES instruments(symbol),
                    frequency TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    first_date TEXT,
                    last_date TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, symbol, frequency)
                );
                CREATE INDEX IF NOT EXISTS job_items_status ON job_items(job_id, status);
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    api_requests INTEGER,
                    api_requests_date TEXT NOT NULL DEFAULT '',
                    daily_rate_limit INTEGER,
                    extra_limit INTEGER,
                    raw_fields_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS exchange_directories (
                    exchange TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    codes_json TEXT NOT NULL
                );
                """
            )

    def upsert_prices(self, rows: list[PriceRow], frequency: str, source: str = "EODHD") -> None:
        if frequency not in FREQUENCIES:
            raise ValueError(f"unsupported frequency: {frequency}")
        stamp = utc_now()
        with self.connection() as db:
            db.executemany(
                "INSERT INTO prices(symbol,frequency,observed_on,adjusted_close,source,retrieved_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,frequency,observed_on) DO UPDATE SET "
                "adjusted_close=excluded.adjusted_close, source=excluded.source, retrieved_at=excluded.retrieved_at",
                [(r.symbol, frequency, r.date.isoformat(), r.adjusted_close, source, stamp) for r in rows],
            )

    def ensure_instrument(self, symbol: str, name: str = "", asset_type: str = "unknown") -> None:
        normalized = symbol.strip().upper()
        exchange = normalized.rsplit(".", 1)[-1] if "." in normalized else ""
        with self.connection() as db:
            db.execute(
                "INSERT INTO instruments(symbol,name,exchange,asset_type,validation_status,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                "name=CASE WHEN excluded.name!='' THEN excluded.name ELSE instruments.name END, updated_at=excluded.updated_at",
                (normalized, name, exchange, asset_type, "pending", utc_now()),
            )

    def record_coverage(self, symbol: str, frequency: str, requested_from: str, requested_to: str,
                        rows: list[PriceRow], job_id: str = "") -> None:
        if not rows:
            raise ValueError("cannot record successful coverage without price rows")
        with self.connection() as db:
            db.execute(
                "INSERT INTO coverage(symbol,frequency,requested_from,requested_to,first_date,last_date,row_count,retrieved_at,job_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (symbol, frequency, requested_from, requested_to, min(r.date for r in rows).isoformat(),
                 max(r.date for r in rows).isoformat(), len(rows), utc_now(), job_id),
            )

    def has_coverage(self, symbol: str, frequency: str, requested_from: str, requested_to: str) -> bool:
        start, end = date.fromisoformat(requested_from), date.fromisoformat(requested_to)
        with self.connection() as db:
            rows = db.execute(
                "SELECT requested_from,requested_to FROM coverage WHERE symbol=? AND frequency=? ORDER BY requested_from,requested_to",
                (symbol.strip().upper(), frequency),
            ).fetchall()
        cursor = start
        for row in rows:
            left, right = date.fromisoformat(row["requested_from"]), date.fromisoformat(row["requested_to"])
            if right < cursor:
                continue
            if left > cursor:
                return False
            cursor = max(cursor, right + timedelta(days=1))
            if cursor > end:
                return True
        return cursor > end

    def import_snapshot(self, collection_id: str, name: str, source_url: str, methodology: str,
                        source_sha256: str, membership_as_of: str, note: str,
                        members: list[dict[str, str]]) -> int:
        stamp = utc_now()
        deduped: dict[str, dict[str, str]] = {}
        for member in members:
            symbol = member["symbol"].strip().upper()
            if not symbol:
                continue
            deduped[symbol] = {**member, "symbol": symbol}
        with self.connection() as db:
            db.execute(
                "INSERT INTO collections(id,name,source_url,methodology,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,source_url=excluded.source_url,methodology=excluded.methodology,updated_at=excluded.updated_at",
                (collection_id, name, source_url, methodology, stamp),
            )
            cur = db.execute(
                "INSERT INTO snapshots(collection_id,source_url,imported_at,source_sha256,membership_as_of,note,member_count) "
                "VALUES(?,?,?,?,?,?,?)",
                (collection_id, source_url, stamp, source_sha256, membership_as_of, note, len(deduped)),
            )
            snapshot_id = int(cur.lastrowid)
            for member in deduped.values():
                symbol = member["symbol"]
                exchange = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
                asset_type = member.get("asset_type", "stock")
                db.execute(
                    "INSERT INTO instruments(symbol,name,exchange,asset_type,validation_status,validation_note,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                    "name=CASE WHEN excluded.name!='' THEN excluded.name ELSE instruments.name END, "
                    "exchange=excluded.exchange, asset_type=excluded.asset_type, updated_at=excluded.updated_at",
                    (symbol, member.get("name", ""), exchange, asset_type,
                     member.get("verification_status", "pending"), member.get("verification_note", ""), stamp),
                )
                db.execute(
                    "INSERT INTO snapshot_members(snapshot_id,symbol,source_symbol,member_name,verification_status) "
                    "VALUES(?,?,?,?,?)",
                    (snapshot_id, symbol, member.get("source_symbol", symbol), member.get("name", ""),
                     member.get("verification_status", "pending")),
                )
        return snapshot_id

    def collection_symbols(self, collection_ids: list[str]) -> list[str]:
        if not collection_ids:
            return []
        placeholders = ",".join("?" for _ in collection_ids)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT DISTINCT sm.symbol FROM snapshot_members sm
                    JOIN snapshots sn ON sn.id=sm.snapshot_id
                    WHERE sn.collection_id IN ({placeholders})
                    AND sn.id=(SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.collection_id=sn.collection_id)
                    ORDER BY sm.symbol""",
                collection_ids,
            ).fetchall()
        return [r[0] for r in rows]

    def start_job(self, job_id: str, collection_ids: list[str], frequencies: list[str], symbols: list[str]) -> None:
        invalid = set(frequencies) - set(FREQUENCIES)
        if invalid:
            raise ValueError(f"unsupported frequencies: {sorted(invalid)}")
        stamp = utc_now()
        items = [(job_id, symbol, frequency, "pending", stamp)
                 for symbol in sorted(set(symbols)) for frequency in frequencies]
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs(id,status,collections_json,frequencies_json,started_at,updated_at,total_items) "
                "VALUES(?,?,?,?,?,?,?)",
                (job_id, "queued", json.dumps(collection_ids), json.dumps(frequencies), stamp, stamp, len(items)),
            )
            db.executemany(
                "INSERT INTO job_items(job_id,symbol,frequency,status,updated_at) VALUES(?,?,?,?,?)", items
            )

    def update_job(self, job_id: str, **values: object) -> None:
        allowed = {"status", "completed_items", "failed_items", "message", "finished_at"}
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at"] = utc_now()
        clause = ",".join(f"{key}=?" for key in updates)
        with self.connection() as db:
            db.execute(f"UPDATE jobs SET {clause} WHERE id=?", (*updates.values(), job_id))

    def update_job_item(self, job_id: str, symbol: str, frequency: str, **values: object) -> None:
        allowed = {"status", "attempts", "row_count", "first_date", "last_date", "error"}
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at"] = utc_now()
        clause = ",".join(f"{key}=?" for key in updates)
        with self.connection() as db:
            db.execute(f"UPDATE job_items SET {clause} WHERE job_id=? AND symbol=? AND frequency=?",
                       (*updates.values(), job_id, symbol, frequency))

    def add_usage_snapshot(self, values: dict[str, object]) -> None:
        fields = ("apiRequests", "apiRequestsDate", "dailyRateLimit", "extraLimit")
        def integer(name: str) -> int | None:
            value = values.get(name)
            try:
                return int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None
        safe_fields = {name: values.get(name) for name in fields}
        with self.connection() as db:
            db.execute(
                "INSERT INTO usage_snapshots(observed_at,api_requests,api_requests_date,daily_rate_limit,extra_limit,raw_fields_json) "
                "VALUES(?,?,?,?,?,?)",
                (utc_now(), integer(fields[0]), str(values.get(fields[1], "")), integer(fields[2]),
                 integer(fields[3]), json.dumps(safe_fields, ensure_ascii=False, allow_nan=False)),
            )

    def exchange_directory(self, exchange: str, max_age_days: int = 7) -> set[str] | None:
        with self.connection() as db:
            row = db.execute("SELECT fetched_at,codes_json FROM exchange_directories WHERE exchange=?", (exchange,)).fetchone()
        if not row:
            return None
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if (datetime.now(timezone.utc) - fetched).total_seconds() > max_age_days * 86400:
                return None
            codes = json.loads(row["codes_json"])
            return set(codes) if isinstance(codes, list) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def save_exchange_directory(self, exchange: str, codes: set[str]) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO exchange_directories(exchange,fetched_at,codes_json) VALUES(?,?,?) "
                "ON CONFLICT(exchange) DO UPDATE SET fetched_at=excluded.fetched_at,codes_json=excluded.codes_json",
                (exchange, utc_now(), json.dumps(sorted(codes), ensure_ascii=False)),
            )

    def job_detail(self, job_id: str) -> dict[str, object] | None:
        with self.connection() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                return None
            items = db.execute(
                "SELECT * FROM job_items WHERE job_id=? ORDER BY updated_at DESC,symbol,frequency", (job_id,)
            ).fetchall()
        return {"job": dict(job), "items": [dict(r) for r in items]}

    def list_jobs(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,status,started_at,updated_at,finished_at,total_items,completed_items,failed_items,message "
                "FROM jobs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()]

    def price_status(self, symbols: list[str]) -> dict[str, dict[str, dict[str, object]]]:
        if not symbols:
            return {}
        result: dict[str, dict[str, dict[str, object]]] = {}
        for offset in range(0, len(symbols), 900):
            chunk = symbols[offset:offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            with self.connection() as db:
                rows = db.execute(
                    f"SELECT symbol,frequency,MIN(observed_on) first_date,MAX(observed_on) last_date,COUNT(*) row_count "
                    f"FROM prices WHERE symbol IN ({placeholders}) GROUP BY symbol,frequency", chunk
                ).fetchall()
            for row in rows:
                result.setdefault(row["symbol"], {})[row["frequency"]] = {
                    "first_date": row["first_date"], "last_date": row["last_date"], "row_count": row["row_count"]
                }
        return result

    def list_exchanges(self) -> list[str]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT DISTINCT i.exchange FROM instruments i
                   JOIN snapshot_members sm ON sm.symbol=i.symbol
                   JOIN snapshots sn ON sn.id=sm.snapshot_id
                   WHERE i.exchange!='' AND sn.id=(
                     SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.collection_id=sn.collection_id
                   ) ORDER BY i.exchange"""
            ).fetchall()
        return [str(row[0]) for row in rows]

    def get_price_series(self, symbol: str, frequency: str) -> list[PriceRow]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT observed_on, symbol, adjusted_close FROM prices "
                "WHERE symbol=? AND frequency=? ORDER BY observed_on",
                (symbol.strip().upper(), frequency),
            ).fetchall()
        return [PriceRow(date.fromisoformat(r["observed_on"]), r["symbol"], r["adjusted_close"]) for r in rows]

    def latest_dates(self, symbols: list[str] | None = None) -> dict[str, dict[str, str | None]]:
        params: list[object] = []
        where = ""
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            where = f"WHERE symbol IN ({placeholders})"
            params.extend(symbols)
        with self.connection() as db:
            rows = db.execute(
                f"SELECT symbol, frequency, MIN(observed_on) first_date, MAX(observed_on) last_date, COUNT(*) row_count "
                f"FROM prices {where} GROUP BY symbol, frequency",
                params,
            ).fetchall()
        result: dict[str, dict[str, str | None]] = {}
        for row in rows:
            result.setdefault(row["symbol"], {})[row["frequency"]] = {
                "first_date": row["first_date"], "last_date": row["last_date"], "row_count": row["row_count"]
            }
        return result

    def collection_catalog(self) -> list[dict[str, object]]:
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT c.id, c.name, c.source_url, c.methodology,
                   COALESCE(s.id,0) snapshot_id, COALESCE(s.imported_at,'') imported_at,
                   COALESCE(s.member_count,0) member_count
                   FROM collections c LEFT JOIN snapshots s ON s.id=(
                     SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.collection_id=c.id
                   ) ORDER BY c.name"""
            ).fetchall()]

    def list_instruments(self, collection_ids: list[str] | None = None, query: str = "",
                         exchange: str = "") -> list[dict[str, object]]:
        clauses: list[str] = []
        args: list[object] = []
        if collection_ids:
            clauses.append("i.symbol IN (SELECT sm.symbol FROM snapshot_members sm JOIN snapshots sn ON sn.id=sm.snapshot_id "
                           "WHERE sn.collection_id IN (" + ",".join("?" for _ in collection_ids) + ") "
                           "AND sn.id=(SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.collection_id=sn.collection_id))")
            args.extend(collection_ids)
        else:
            clauses.append("i.symbol IN (SELECT sm.symbol FROM snapshot_members sm JOIN snapshots sn ON sn.id=sm.snapshot_id "
                           "WHERE sn.id=(SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.collection_id=sn.collection_id))")
        if query.strip():
            clauses.append("(i.symbol LIKE ? OR i.name LIKE ?)")
            args.extend([f"%{query.strip()}%", f"%{query.strip()}%"])
        if exchange.strip():
            clauses.append("i.exchange=?")
            args.append(exchange.strip().upper())
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""SELECT i.symbol, i.name, i.exchange, i.asset_type, i.validation_status,
                  i.validation_note,
                  (SELECT GROUP_CONCAT(DISTINCT sn2.collection_id) FROM snapshot_members sm2
                   JOIN snapshots sn2 ON sn2.id=sm2.snapshot_id
                   WHERE sm2.symbol=i.symbol AND sn2.id=(SELECT MAX(s3.id) FROM snapshots s3 WHERE s3.collection_id=sn2.collection_id)) collection_ids,
                  (SELECT GROUP_CONCAT(DISTINCT c2.name) FROM snapshot_members sm3
                   JOIN snapshots sn3 ON sn3.id=sm3.snapshot_id JOIN collections c2 ON c2.id=sn3.collection_id
                   WHERE sm3.symbol=i.symbol AND sn3.id=(SELECT MAX(s4.id) FROM snapshots s4 WHERE s4.collection_id=sn3.collection_id)) collection_names,
                  COUNT(DISTINCT p.frequency) frequencies,
                  MIN(p.observed_on) first_date, MAX(p.observed_on) last_date, COUNT(p.observed_on) rows
                  FROM instruments i
                  LEFT JOIN prices p ON p.symbol=i.symbol
                  {where}
                  GROUP BY i.symbol ORDER BY i.symbol"""
        with self.connection() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def local_bundle(self, symbols: list[str]) -> dict[str, dict[str, list[PriceRow]]]:
        return {symbol: {frequency: self.get_price_series(symbol, frequency) for frequency in FREQUENCIES}
                for symbol in symbols}
