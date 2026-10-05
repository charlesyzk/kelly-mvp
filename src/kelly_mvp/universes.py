"""Fetch and normalize the five user-provided EODHD universe pages."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .market_store import MarketStore


NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&exchange=nasdaq&download=true"
SP500_URL = "https://raw.githubusercontent.com/yfiua/index-constituents/refs/heads/main/docs/constituents-sp500.csv"
CSI300_URL = "https://raw.githubusercontent.com/yfiua/index-constituents/refs/heads/main/docs/constituents-csi300.csv"
CSI500_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000905cons.xls"


@dataclass(frozen=True, slots=True)
class Universe:
    id: str
    name: str
    source_url: str
    methodology: str
    note: str
    content: bytes
    members: list[dict[str, str]]


def _get(url: str, *, nasdaq: bool = False) -> bytes:
    headers = {"User-Agent": "kelly-mvp/0.6 (+local research data import)"}
    if nasdaq:
        headers.update({"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
                        "Referer": "https://www.nasdaq.com/"})
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read(30 * 1024 * 1024 + 1)
    except Exception as exc:
        raise RuntimeError(f"清单来源读取失败（{type(exc).__name__}）：{url}") from None
    if len(body) > 30 * 1024 * 1024:
        raise ValueError("成分清单响应超过30MB上限")
    return body


def _csv_rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{str(k).strip().lower(): str(v or "").strip() for k, v in row.items() if k is not None}
            for row in reader]


def _norm_us(source_symbol: str) -> str:
    symbol = source_symbol.strip().upper().replace(".", "-").replace("/", "-")
    if symbol.endswith(".US"):
        return symbol
    return f"{symbol}.US"


def _nasdaq_members(content: bytes) -> list[dict[str, str]]:
    payload = json.loads(content.decode("utf-8-sig"))
    rows = payload.get("data", {}).get("rows", []) if isinstance(payload, dict) else []
    excluded = re.compile(r"\b(Warrants?|Units?|Rights?|Preferred|Preference|Convertible|ETF|ETN)\b|Exchange Traded Fund", re.I)
    result = []
    for row in rows:
        symbol, name = str(row.get("symbol", "")).strip(), str(row.get("name", "")).strip()
        if not symbol or not name or excluded.search(name):
            continue
        result.append({"symbol": _norm_us(symbol), "source_symbol": symbol, "name": name,
                       "asset_type": "stock", "verification_status": "candidate",
                       "verification_note": "由 Nasdaq 股票筛选器按 HTML 中的排除规则重构；不是官方精确 Composite 成分表"})
    return result


def _sp500_members(content: bytes) -> list[dict[str, str]]:
    return [{"symbol": _norm_us(row.get("symbol", "")), "source_symbol": row.get("symbol", ""),
             "name": row.get("name", ""), "asset_type": "stock", "verification_status": "pending"}
            for row in _csv_rows(content) if row.get("symbol") and row.get("name")]


def _csi300_members(content: bytes) -> list[dict[str, str]]:
    members = []
    for row in _csv_rows(content):
        symbol = row.get("symbol", "").strip().upper()
        if not symbol or not row.get("name"):
            continue
        if symbol.endswith(".SZ"):
            provider = symbol[:-3] + ".SHE"
        elif symbol.endswith((".SS", ".SH")):
            provider = symbol[:-3] + ".SHG"
        elif symbol.endswith(".BJ"):
            provider = symbol
        else:
            provider = symbol
        members.append({"symbol": provider, "source_symbol": symbol, "name": row["name"],
                        "asset_type": "stock", "verification_status": "pending"})
    return members


def _csi500_members(content: bytes) -> list[dict[str, str]]:
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("读取中证500官方XLS需要xlrd依赖；请安装项目依赖后重试") from None
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    header = [str(value).strip() for value in sheet.row_values(0)]
    code_index = next((i for i, h in enumerate(header) if re.search(r"成份券代码|Constituent\s*Code", h, re.I)), None)
    name_index = next((i for i, h in enumerate(header) if re.search(r"成份券名称|Constituent\s*Name", h, re.I)), None)
    if code_index is None:
        code_index = next((i for i, h in enumerate(header) if re.search(r"代码|code", h, re.I)), None)
    if name_index is None:
        name_index = next((i for i, h in enumerate(header) if re.search(r"名称|name", h, re.I)), None)
    if code_index is None or name_index is None:
        raise ValueError("无法识别中证500官方XLS的代码和名称列")
    members = []
    for row_index in range(1, sheet.nrows):
        raw_code = sheet.cell_value(row_index, code_index)
        if isinstance(raw_code, float) and raw_code.is_integer():
            raw_code = str(int(raw_code))
        code = str(raw_code).strip().removesuffix(".0").zfill(6)
        name = str(sheet.cell_value(row_index, name_index)).strip()
        if not name or not re.fullmatch(r"\d{6}", code):
            continue
        if code.startswith("6"):
            provider = code + ".SHG"
        elif code[0] in "0123":
            provider = code + ".SHE"
        elif code[0] in "489":
            provider = code + ".BJ"
        else:
            continue
        members.append({"symbol": provider, "source_symbol": code, "name": name,
                        "asset_type": "stock", "verification_status": "pending"})
    return members


def _global_index_members(html: bytes) -> list[dict[str, str]]:
    text = html.decode("utf-8", errors="replace")
    scripts = re.findall(r"const\s+rows\s*=\s*\[(.*?)\]\s*;", text, re.S)
    block = next((candidate for candidate in scripts if "{name:" in candidate), None)
    if block is None:
        raise ValueError("全球指数HTML没有找到静态代码表")
    members = []
    for name, code in re.findall(r"\{\s*name:'((?:\\.|[^'])*)'\s*,\s*code:'([A-Z0-9._-]+)'\s*\}", block):
        status = "needs_api_validation" if code in {"WISGP.INDX", "BVSP.INDX"} else "pending"
        note = "HTML标记为待EODHD账户内API验证" if status == "needs_api_validation" else ""
        members.append({"symbol": code, "source_symbol": code, "name": name.replace("\\'", "'"),
                        "asset_type": "index", "verification_status": status, "verification_note": note})
    return members


def fetch_universe_definitions(*, html_dir: str | None = None) -> list[Universe]:
    """Fetch the current lists represented by the delivered HTML pages."""
    from pathlib import Path

    paths = Path(html_dir) if html_dir else Path.home() / "Downloads"
    global_path = paths / "全球15个大盘指数_EODHD代码.html"
    if not global_path.is_file():
        raise FileNotFoundError(f"找不到全球指数HTML：{global_path}")
    global_html = global_path.read_bytes()
    nasdaq = _get(NASDAQ_URL, nasdaq=True)
    sp500 = _get(SP500_URL)
    csi300 = _get(CSI300_URL)
    csi500 = _get(CSI500_URL)
    return [
        Universe("nasdaq_composite", "NASDAQ Composite候选成分", NASDAQ_URL,
                 "Nasdaq筛选器 + HTML证券名称排除规则", "公开重构候选集；数量与官方披露不一致时不得称为官方精确成分表。",
                 nasdaq, _nasdaq_members(nasdaq)),
        Universe("sp500", "S&P 500成分股", SP500_URL,
                 "按用户HTML规则将美国证券映射为EODHD .US代码", "多类别股份可使证券数超过500；清单来自HTML指定的公开CSV源。",
                 sp500, _sp500_members(sp500)),
        Universe("csi300", "沪深300成分股", CSI300_URL,
                 "深交所 .SZ→.SHE；上交所 .SS/.SH→.SHG", "HTML指定的公开CSV清单；需记录每次导入快照。",
                 csi300, _csi300_members(csi300)),
        Universe("csi500", "中证500成分股", CSI500_URL,
                 "中证指数官方000905cons.xls；按六位代码映射 .SHE/.SHG/.BJ", "来自官方成分文件；交易所映射仍需以EODHD验证结果为准。",
                 csi500, _csi500_members(csi500)),
        Universe("global_indices", "全球15个大盘指数", str(global_path),
                 "使用用户HTML中静态列出的15个EODHD代码", "WISGP.INDX与BVSP.INDX保留待账户内API验证标记。",
                 global_html, _global_index_members(global_html)),
    ]


def import_universes(store: MarketStore, definitions: list[Universe]) -> list[dict[str, object]]:
    imported = []
    as_of = datetime.now(timezone.utc).date().isoformat()
    for universe in definitions:
        if not universe.members:
            raise ValueError(f"清单解析结果为空，拒绝保存快照：{universe.name}")
        digest = hashlib.sha256(universe.content).hexdigest()
        snapshot_id = store.import_snapshot(
            universe.id, universe.name, universe.source_url, universe.methodology,
            digest, "unknown", universe.note, universe.members,
        )
        imported.append({"id": universe.id, "name": universe.name, "snapshot_id": snapshot_id,
                         "member_count": len({m["symbol"] for m in universe.members}),
                         "source_sha256": digest, "imported_at": as_of,
                         "membership_as_of": "unknown", "note": universe.note})
    return imported
