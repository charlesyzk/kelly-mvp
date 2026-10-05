"""Resumable, quota-aware EODHD fetch jobs for the local market database."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .config import FREQUENCIES
from .data import PriceRow
from .eodhd import EODHDHTTPError, fetch_account_usage, fetch_exchange_symbols, fetch_prices
from .market_store import MarketStore, utc_now


MIN_HISTORY_DATE = "1900-01-01"
OVERLAP_DAYS = 45
_ACTIVE_THREADS: dict[str, threading.Thread] = {}
_ACTIVE_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_REQUEST_PACE_LOCK = threading.Lock()
_LAST_PROVIDER_REQUEST = 0.0
MIN_PROVIDER_REQUEST_INTERVAL = 0.1


@contextmanager
def _provider_request_slot():
    """Serialize provider requests and keep starts below 600 per minute."""
    global _LAST_PROVIDER_REQUEST
    with _REQUEST_PACE_LOCK:
        now = time.monotonic()
        delay = MIN_PROVIDER_REQUEST_INTERVAL - (now - _LAST_PROVIDER_REQUEST)
        if delay > 0:
            time.sleep(delay)
        try:
            yield
        finally:
            _LAST_PROVIDER_REQUEST = time.monotonic()


def _quota_remaining(usage: dict[str, object]) -> int | None:
    try:
        limit = int(usage["dailyRateLimit"])
        spent = int(usage.get("apiRequests", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None
    usage_date = str(usage.get("apiRequestsDate", ""))[:10]
    # The provider's daily usage date may follow UTC while the desktop and
    # mocked account payload follow the user's local date. Treat either date
    # as current across the UTC/local midnight boundary.
    current_dates = {
        datetime.now(timezone.utc).date().isoformat(),
        date.today().isoformat(),
    }
    if usage_date and usage_date not in current_dates:
        spent = 0
    # Do not consume separately purchased extra calls without an explicit UI budget setting.
    return max(0, limit - spent)


def _safe_usage_snapshot(store: MarketStore, job_id: str) -> dict[str, object] | None:
    """Refresh free quota telemetry, pausing safely on provider/account errors."""
    try:
        with _provider_request_slot():
            usage = fetch_account_usage()
        store.add_usage_snapshot(usage)
        return usage
    except EODHDHTTPError as exc:
        status = {401: "needs_token", 402: "paused_quota", 429: "paused_rate_limit"}.get(
            exc.status_code, "needs_attention")
        message = ("EODHD拒绝了认证；请检查服务端环境变量" if exc.status_code == 401
                   else _safe_error(exc))
        store.update_job(job_id, status=status, message=message)
    except Exception as exc:
        store.update_job(job_id, status="needs_attention",
                         message=f"无法读取账户额度：{_safe_error(exc)}；为防止超额已暂停")
    return None


def _api_symbol_key(value: str) -> tuple[str, str]:
    code = value.strip().upper()
    if "." in code:
        ticker, exchange = code.rsplit(".", 1)
        if exchange == "US":
            ticker = ticker.replace(".", "-").replace("/", "-")
        return ticker, exchange
    return code, ""


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, EODHDHTTPError):
        suffix = f"；Retry-After {exc.retry_after:g}秒" if exc.retry_after is not None else ""
        return f"EODHD HTTP {exc.status_code}{suffix}"
    message = str(exc)
    message = re.sub(r"(?i)(api_token=)[^&\s]+", r"\1[redacted]", message)
    message = re.sub(r"https?://\S+", "[provider URL suppressed]", message)
    if len(message) > 240:
        message = message[:237] + "..."
    return message or type(exc).__name__


def create_fetch_job(store: MarketStore, collection_ids: list[str], frequencies: list[str] | None = None,
                     symbols_filter: list[str] | None = None, exchange_filter: str = "") -> str:
    selected_frequencies = frequencies or list(FREQUENCIES)
    if not collection_ids:
        raise ValueError("请选择至少一个数据清单")
    symbols = store.collection_symbols(collection_ids)
    if symbols_filter is not None:
        allowed = set(symbols)
        symbols = [symbol.strip().upper() for symbol in symbols_filter if symbol.strip().upper() in allowed]
    if exchange_filter.strip():
        exchange = exchange_filter.strip().upper()
        symbols = [symbol for symbol in symbols if symbol.rsplit(".", 1)[-1].upper() == exchange]
    if not symbols:
        raise ValueError("所选清单没有已导入的代码")
    job_id = uuid.uuid4().hex[:12]
    store.start_job(job_id, collection_ids, selected_frequencies, symbols)
    return job_id


def _validate_exchange_universes(store: MarketStore, job_id: str, symbols: list[str]) -> bool:
    exchanges = sorted({s.rsplit(".", 1)[-1].upper() for s in symbols if "." in s})
    if not exchanges:
        return True
    for exchange_index, exchange in enumerate(exchanges):
        job = store.job_detail(job_id)
        if not job or job["job"]["status"] in {"pause_requested", "cancelled"}:
            return False
        usage = _safe_usage_snapshot(store, job_id)
        if usage is None:
            return False
        remaining = _quota_remaining(usage)
        if remaining is None or remaining < 1:
            store.update_job(job_id, status="paused_quota", message="额度不足，未继续请求交易所代码目录")
            return False
        try:
            codes = store.exchange_directory(exchange)
            if codes is None:
                uncached_remaining = sum(1 for suffix in exchanges[exchange_index:]
                                         if store.exchange_directory(suffix) is None)
                if remaining <= uncached_remaining:
                    store.update_job(job_id, status="paused_quota",
                                     message="额度不足以完成代码目录校验并保留行情请求；任务已暂停")
                    return False
                with _provider_request_slot():
                    codes = fetch_exchange_symbols(exchange)
                store.save_exchange_directory(exchange, codes)
        except EODHDHTTPError as exc:
            # A failed exchange-list call is billed too; fail closed rather than probing every ticker.
            store.update_job(job_id, status="needs_attention", message=f"无法校验交易所后缀 {exchange}（HTTP {exc.status_code}）；行情请求已暂停")
            return False
        except Exception as exc:
            store.update_job(job_id, status="needs_attention", message=f"无法读取交易所代码目录：{_safe_error(exc)}；行情请求已暂停")
            return False
        target = {_api_symbol_key(s) for s in symbols if s.rsplit(".", 1)[-1].upper() == exchange}
        available = {_api_symbol_key(code) for code in codes}
        missing = {symbol for symbol in target if symbol not in available}
        listed = target - missing
        if listed:
            with store.connection() as db:
                for ticker, suffix in listed:
                    full_symbol = f"{ticker}.{suffix}"
                    db.execute("UPDATE instruments SET validation_status='provider_listed',validation_note=?,updated_at=? WHERE symbol=?",
                               ("EODHD交易所代码目录确认存在；这不代表指数成分身份已核实。", utc_now(), full_symbol))
                    db.execute("UPDATE snapshot_members SET verification_status='provider_listed' WHERE symbol=?", (full_symbol,))
        if missing:
            with store.connection() as db:
                for ticker, suffix in missing:
                    full_symbol = f"{ticker}.{suffix}"
                    db.execute("UPDATE instruments SET validation_status='not_listed',validation_note=?,updated_at=? WHERE symbol=?",
                               ("EODHD交易所目录未返回该代码；为避免计费的404请求而跳过。", utc_now(), full_symbol))
            with store.connection() as db:
                for item in missing:
                    symbol = f"{item[0]}.{item[1]}"
                    db.execute("UPDATE job_items SET status='skipped',error=?,updated_at=? WHERE job_id=? AND symbol=? AND status='pending'",
                               ("EODHD交易所目录未列出该代码", utc_now(), job_id, symbol))
    return True


def run_fetch_job(store: MarketStore, job_id: str, *, batch_size: int = 30,
                  progress: Callable[[str], None] | None = None) -> dict[str, object]:
    if not os.getenv("EODHD_API_TOKEN", "").strip():
        store.update_job(job_id, status="needs_token", message="未配置 EODHD_API_TOKEN；未发送供应商请求")
        return store.job_detail(job_id) or {}
    store.update_job(job_id, status="validating", message="正在检查供应商代码目录与额度")
    detail = store.job_detail(job_id)
    if not detail:
        raise ValueError("取数任务不存在")
    all_symbols = sorted({item["symbol"] for item in detail["items"]})
    if not _validate_exchange_universes(store, job_id, all_symbols):
        return store.job_detail(job_id) or {}
    done_in_batch = 0
    while True:
        detail = store.job_detail(job_id)
        if not detail:
            raise ValueError("取数任务不存在")
        status = detail["job"]["status"]
        if status in {"pause_requested", "cancelled", "paused_quota", "needs_attention"}:
            if status == "pause_requested":
                store.update_job(job_id, status="paused", message="已暂停；可从管理页继续")
            break
        pending = [item for item in detail["items"] if item["status"] in {"pending", "retry"}]
        if not pending:
            if done_in_batch:
                usage = _safe_usage_snapshot(store, job_id)
                if usage is None:
                    break
                done_in_batch = 0
            failed = sum(1 for item in detail["items"] if item["status"] == "failed")
            skipped = sum(1 for item in detail["items"] if item["status"] == "skipped")
            status = "completed_with_issues" if failed or skipped else "completed"
            store.update_job(job_id, status=status, failed_items=failed + skipped,
                             finished_at=utc_now(), message=f"任务结束；失败 {failed}，跳过 {skipped}")
            break
        if done_in_batch == 0:
            store.update_job(job_id, status="running", message="正在按批次获取复权价格")
            usage = _safe_usage_snapshot(store, job_id)
            if usage is None:
                break
            remaining = _quota_remaining(usage)
            if remaining is None:
                store.update_job(job_id, status="needs_attention", message="无法解析账户额度；为防止超额已暂停")
                break
            if remaining <= 0:
                store.update_job(job_id, status="paused_quota", message="本日 EODHD API call 额度已用尽")
                break
            current_batch_quota = min(batch_size, remaining)
        batch = pending[:max(1, current_batch_quota - done_in_batch)]
        item = batch[0]
        symbol, frequency = item["symbol"], item["frequency"]
        store.update_job_item(job_id, symbol, frequency, status="running", attempts=item["attempts"] + 1, error="")
        try:
            cached = store.get_price_series(symbol, frequency)
            today = date.today()
            if cached:
                # Re-fetch a small tail to absorb late provider corrections while
                # keeping incremental updates proportional to recent history.
                start = max(date.fromisoformat(MIN_HISTORY_DATE),
                            max(row.date for row in cached) - timedelta(days=OVERLAP_DAYS))
            else:
                start = date.fromisoformat(MIN_HISTORY_DATE)
            with _provider_request_slot():
                rows = fetch_prices(symbol, start, today, frequency=frequency)
            if not rows:
                raise ValueError("供应商没有返回历史行情")
            store.upsert_prices(rows, frequency)
            store.record_coverage(symbol, frequency, start.isoformat(), today.isoformat(), rows, job_id)
            store.update_job_item(job_id, symbol, frequency, status="completed", row_count=len(rows),
                                  first_date=min(r.date for r in rows).isoformat(),
                                  last_date=max(r.date for r in rows).isoformat(), error="")
            with store.connection() as db:
                db.execute("UPDATE instruments SET validation_status='validated',validation_note='',updated_at=? WHERE symbol=?",
                           (utc_now(), symbol))
                db.execute("UPDATE snapshot_members SET verification_status='validated' WHERE symbol=?", (symbol,))
            done_in_batch += 1
            done = sum(1 for i in store.job_detail(job_id)["items"] if i["status"] == "completed")
            if progress:
                progress(f"{symbol} {frequency}: {len(rows)} bars; completed {done}")
        except EODHDHTTPError as exc:
            if exc.status_code in {402, 429}:
                status = "paused_quota" if exc.status_code == 402 else "paused_rate_limit"
                store.update_job_item(job_id, symbol, frequency, status="pending", error=_safe_error(exc))
                store.update_job(job_id, status=status, message=_safe_error(exc))
                break
            store.update_job_item(job_id, symbol, frequency, status="failed", error=_safe_error(exc))
            if exc.status_code == 401:
                refreshed = store.job_detail(job_id)
                completed = sum(1 for row in refreshed["items"] if row["status"] == "completed")
                failed = sum(1 for row in refreshed["items"] if row["status"] in {"failed", "skipped"})
                store.update_job(job_id, completed_items=completed, failed_items=failed)
                store.update_job(job_id, status="needs_token", message="EODHD拒绝了认证；请检查服务端环境变量")
                break
            done_in_batch += 1
        except Exception as exc:
            store.update_job_item(job_id, symbol, frequency, status="failed", error=_safe_error(exc))
            done_in_batch += 1
        if done_in_batch >= current_batch_quota:
            usage = _safe_usage_snapshot(store, job_id)
            if usage is None:
                break
            done_in_batch = 0
            current_batch_quota = 0
        # Persist total counters from item state for UI polling.
        refreshed = store.job_detail(job_id)
        completed = sum(1 for i in refreshed["items"] if i["status"] == "completed")
        failed = sum(1 for i in refreshed["items"] if i["status"] in {"failed", "skipped"})
        store.update_job(job_id, completed_items=completed, failed_items=failed)
    return store.job_detail(job_id) or {}


def launch_fetch_job(store: MarketStore, job_id: str, *, batch_size: int = 30) -> None:
    with _ACTIVE_LOCK:
        active = _ACTIVE_THREADS.get(job_id)
        if active and active.is_alive():
            return
        thread = threading.Thread(target=_run_serialized, args=(store, job_id, batch_size), daemon=True,
                                  name=f"market-fetch-{job_id}")
        _ACTIVE_THREADS[job_id] = thread
        thread.start()


def _run_serialized(store: MarketStore, job_id: str, batch_size: int) -> None:
    with _RUN_LOCK:
        detail = store.job_detail(job_id)
        if not detail:
            return
        if detail["job"]["status"] in {"pause_requested", "cancelled"}:
            store.update_job(job_id, status="paused", message="已暂停；可从管理页继续")
            return
        try:
            run_fetch_job(store, job_id, batch_size=batch_size)
        except Exception as exc:
            store.update_job(job_id, status="needs_attention", message=f"任务异常：{_safe_error(exc)}", finished_at=utc_now())


def pause_fetch_job(store: MarketStore, job_id: str) -> None:
    with _ACTIVE_LOCK:
        thread = _ACTIVE_THREADS.get(job_id)
        if thread and thread.is_alive():
            store.update_job(job_id, status="pause_requested", message="等待当前请求完成后暂停")
        else:
            detail = store.job_detail(job_id)
            if not detail:
                raise ValueError("取数任务不存在")
            if detail["job"]["status"] in {"queued", "validating", "running", "pause_requested"}:
                # The active worker may be in a different process (CLI/server).
                # Persist a request it can observe between provider requests.
                store.update_job(job_id, status="pause_requested", message="等待当前请求完成后暂停")
            else:
                store.update_job(job_id, status="paused", message="已暂停；可从管理页继续")


def resume_fetch_job(store: MarketStore, job_id: str, *, batch_size: int = 30,
                     run_async: bool = True) -> dict[str, object]:
    detail = store.job_detail(job_id)
    if not detail:
        raise ValueError("取数任务不存在")
    if detail["job"]["status"] == "completed":
        raise ValueError("该任务已完成")
    with store.connection() as db:
        # A process restart can leave an item in running state even though no
        # worker owns it anymore. Requeue it on explicit resume.
        db.execute("UPDATE job_items SET status='pending',error='',updated_at=? WHERE job_id=? AND status IN ('failed','retry','running')",
                   (utc_now(), job_id))
    store.update_job(job_id, status="queued", message="任务已恢复", finished_at=None)
    if run_async:
        launch_fetch_job(store, job_id, batch_size=batch_size)
    return store.job_detail(job_id) or {}
