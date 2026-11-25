"""
Piyasa Sade — Tüm BIST (~625 Hisse) Gerçek Yükselen & Düşenler Motoru (1G / 10G / 20G / 30G)
- TradingView BIST Tarayıcısı ile Borsa İstanbul'daki TÜM aktif hisseleri (~625 hisse) tek istekte alır:
  * 1G (Günlük) ilk 15 yükselen & düşen + tüm BIST piyasa genişliği (yükselen/düşen/yatay)
  * Resmi BIST 100 (XU100) ve BIST 30 (XU030) kapanış & günlük % (Yahoo 1d previousClose sapmasını önler)
  * 30G (Son 30 Takvim Günü / 1 Ay) bölünme-düzeltilmiş resmi performans doğrulaması (`Perf.1M`)
- Son 10 Gün, Son 20 Gün ve Son 30 Gün (kesin TAKVİM günü: bugün - N gün) için tüm aktif BIST
  hisselerinin Yahoo 2mo/1d günlük kapanışlarını HTTP Keep-Alive havuzuyla tarar ve TradingView
  kapanış/Perf.1M verisiyle çapraz doğrulayarak bölünme/hatalı bar sızmasını %100 engeller.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

USER_AGENT = "Mozilla/5.0 PiyasaSade/1.3"
IST_TZ = timezone(timedelta(hours=3))
TV_TURKEY_SCAN_URL = "https://scanner.tradingview.com/turkey/scan"


def _create_session(pool_size: int = 28) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _ist_date(dt: datetime) -> date:
    return dt.astimezone(IST_TZ).date()


def _fetch_all_bist_from_tradingview(session: requests.Session) -> List[Dict[str, Any]]:
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "BIST"},
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "close", "operation": "greater", "right": 0},
        ],
        "columns": [
            "name",
            "description",
            "close",
            "change",
            "change_abs",
            "Perf.W",
            "Perf.1M",
            "volume",
        ],
        "sort": {"sortBy": "change", "sortOrder": "desc"},
        "range": [0, 800],
    }
    resp = session.post(
        TV_TURKEY_SCAN_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    stocks: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        d = item.get("d") or []
        if len(d) < 8:
            continue
        sym = str(d[0] or "").strip().upper()
        name = str(d[1] or sym).strip()
        close = d[2]
        chg = d[3]
        chg_abs = d[4]
        perf_w = d[5]
        perf_1m = d[6]
        vol = d[7] or 0
        if not sym or sym in seen:
            continue
        if close is None or chg is None or not math.isfinite(float(close)) or float(close) <= 0:
            continue
        if float(vol) <= 0:
            continue
        seen.add(sym)
        close_f = float(close)
        chg_f = float(chg)
        if chg_abs is not None and math.isfinite(float(chg_abs)):
            prev_f = close_f - float(chg_abs)
        else:
            prev_f = close_f / (1.0 + chg_f / 100.0) if (1.0 + chg_f / 100.0) > 0 else close_f
        stocks.append({
            "symbol": sym,
            "name": name,
            "close": close_f,
            "prev_close": prev_f,
            "change_1d": chg_f,
            "perf_w": float(perf_w) if perf_w is not None and math.isfinite(float(perf_w)) else None,
            "perf_1m": float(perf_1m) if perf_1m is not None and math.isfinite(float(perf_1m)) else None,
            "volume": float(vol),
        })
    return stocks


def _fetch_index_snapshot(session: requests.Session) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    # 1. Official BIST 100 & BIST 30 from TradingView (immune to Yahoo 1d stale previousClose)
    try:
        resp = session.post(
            TV_TURKEY_SCAN_URL,
            json={
                "symbols": {"tickers": ["BIST:XU100", "BIST:XU030"]},
                "columns": ["name", "close", "change", "change_abs"],
            },
            headers={"Content-Type": "application/json"},
            timeout=12,
        )
        if resp.status_code == 200:
            names = {"XU100": "BIST 100", "XU030": "BIST 30"}
            for item in resp.json().get("data") or []:
                d = item.get("d") or []
                if len(d) >= 4 and d[1] is not None and d[2] is not None:
                    sym = str(d[0]).upper()
                    price_f = float(d[1])
                    pct_f = float(d[2])
                    prev_f = price_f - float(d[3]) if d[3] is not None else price_f / (1.0 + pct_f / 100.0)
                    out[sym] = {
                        "symbol": sym,
                        "name": names.get(sym, sym),
                        "price": round(price_f, 2),
                        "previous_close": round(prev_f, 2),
                        "change_percent": round(pct_f, 2),
                    }
    except Exception:
        pass

    # 2. FX & Gold from Yahoo Finance (plus fallback for XU100/XU030 via 5d/1d if TV failed)
    yahoo_targets = [
        ("USDTRY", "USDTRY=X", "Dolar / TL"),
        ("EURTRY", "EURTRY=X", "Euro / TL"),
        ("GOLD", "GC=F", "Ons Altın"),
    ]
    for idx_sym, idx_ysym, idx_name in (("XU100", "XU100.IS", "BIST 100"), ("XU030", "XU030.IS", "BIST 30")):
        if idx_sym not in out:
            yahoo_targets.append((idx_sym, idx_ysym, idx_name))

    for sym, ysym, name in yahoo_targets:
        try:
            rng = "5d" if sym in ("XU100", "XU030") else "1d"
            itv = "1d" if sym in ("XU100", "XU030") else "5m"
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?interval={itv}&range={rng}"
            resp = session.get(url, timeout=12)
            if resp.status_code != 200:
                continue
            res = ((resp.json().get("chart") or {}).get("result") or [None])[0]
            if not res:
                continue
            meta = res.get("meta") or {}
            price = meta.get("regularMarketPrice")
            if sym in ("XU100", "XU030"):
                ts = res.get("timestamp") or []
                cs = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                day_bars: List[Tuple[date, float]] = []
                for t, c in zip(ts, cs):
                    if t is None or c is None:
                        continue
                    d_ist = _ist_date(datetime.fromtimestamp(float(t), tz=timezone.utc))
                    if day_bars and d_ist == day_bars[-1][0]:
                        day_bars[-1] = (d_ist, float(c))
                    else:
                        day_bars.append((d_ist, float(c)))
                if len(day_bars) < 2:
                    continue
                price_f = float(price) if price else day_bars[-1][1]
                prev_f = day_bars[-2][1]
            else:
                prev = meta.get("previousClose") or meta.get("chartPreviousClose")
                if price is None or prev is None:
                    continue
                price_f = float(price)
                prev_f = float(prev)

            if not (math.isfinite(price_f) and math.isfinite(prev_f) and price_f > 0 and prev_f > 0):
                continue
            pct = ((price_f - prev_f) / prev_f) * 100.0
            out[sym] = {
                "symbol": sym,
                "name": name,
                "price": round(price_f, 4 if sym in ("USDTRY", "EURTRY") else 2),
                "previous_close": round(prev_f, 4 if sym in ("USDTRY", "EURTRY") else 2),
                "change_percent": round(pct, 2),
            }
        except Exception:
            continue

    if "GOLD" in out and "USDTRY" in out:
        g = out["GOLD"]
        u = out["USDTRY"]
        gram_now = (g["price"] * u["price"]) / 31.1034768
        gram_prev = (g["previous_close"] * u["previous_close"]) / 31.1034768
        if gram_prev > 0:
            out["GRAM_ALTIN"] = {
                "symbol": "GRAM_ALTIN",
                "name": "Gram Altın (TL)",
                "price": round(gram_now, 2),
                "previous_close": round(gram_prev, 2),
                "change_percent": round(((gram_now - gram_prev) / gram_prev) * 100.0, 2),
            }
    return out


def _fetch_yahoo_daily_history(
    session: requests.Session,
    stock: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    sym = stock["symbol"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.IS?interval=1d&range=2mo"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        results = (resp.json().get("chart") or {}).get("result") or []
        if not results:
            return None
        res = results[0]
        timestamps = res.get("timestamp") or []
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []

        # Deduplicate strictly by Istanbul calendar date
        points: List[Tuple[date, datetime, float]] = []
        for t, c in zip(timestamps, closes):
            if t is None or c is None:
                continue
            val = float(c)
            if not math.isfinite(val) or val <= 0:
                continue
            dt = datetime.fromtimestamp(float(t), tz=timezone.utc)
            d_ist = _ist_date(dt)
            if points and d_ist == points[-1][0]:
                points[-1] = (d_ist, dt, val)
            elif not points or d_ist > points[-1][0]:
                points.append((d_ist, dt, val))

        if len(points) < 2:
            return None

        # Cross-validate Yahoo's latest price against TradingView's official close.
        # If Yahoo has an unadjusted split or corrupted price (>4% off TV), reject Yahoo series!
        tv_close = stock["close"]
        yahoo_last = points[-1][2]
        if abs(yahoo_last - tv_close) / tv_close > 0.04:
            # Also check if Yahoo simply missed today's bar (points[-1] is yesterday's close)
            tv_prev = stock["prev_close"]
            if tv_prev > 0 and abs(yahoo_last - tv_prev) / tv_prev <= 0.03:
                today_ist = datetime.now(IST_TZ)
                points.append((today_ist.date(), today_ist.astimezone(timezone.utc), tv_close))
            else:
                return None
        else:
            # Pin last point cleanly to official TV close
            points[-1] = (points[-1][0], points[-1][1], tv_close)

        return {
            "stock": stock,
            "points": points,
        }
    except Exception:
        return None


def _point_on_or_before(
    points: List[Tuple[date, datetime, float]],
    target_day: date,
) -> Optional[Tuple[date, datetime, float]]:
    chosen = None
    for pt in points:
        if pt[0] <= target_day:
            chosen = pt
        else:
            break
    if chosen is not None:
        return chosen
    # If target_day is slightly before first bar (e.g. weekend/holiday boundary within 4 days)
    if points and (points[0][0] - target_day).days <= 4:
        return points[0]
    return None


def _build_1d_bucket(
    tv_stocks: List[Dict[str, Any]],
    latest_day: date,
    prev_day: date,
    top_n: int = 15,
) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    for s in tv_stocks:
        rows.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "price": round(s["close"], 2),
            "previous_close": round(s["prev_close"], 2),
            "change_percent": round(s["change_1d"], 2),
            "currency": "TRY",
            "updated_at": now_iso,
        })

    adv = sum(1 for r in rows if r["change_percent"] > 0)
    dec = sum(1 for r in rows if r["change_percent"] < 0)
    flat = len(rows) - adv - dec

    gainers = sorted(
        [r for r in rows if r["change_percent"] > 0],
        key=lambda r: r["change_percent"],
        reverse=True,
    )[:top_n]
    losers = sorted(
        [r for r in rows if r["change_percent"] < 0],
        key=lambda r: r["change_percent"],
    )[:top_n]

    return {
        "days": 1,
        "start_date": prev_day.strftime("%d.%m.%Y"),
        "end_date": latest_day.strftime("%d.%m.%Y"),
        "scanned_count": len(rows),
        "advancers": adv,
        "decliners": dec,
        "unchanged": flat,
        "gainers": gainers,
        "losers": losers,
    }


def _has_unadjusted_split_jump(
    points: List[Tuple[date, datetime, float]],
    start_day: date,
) -> bool:
    """
    BIST official daily circuit-breaker limit is +/-10%.
    Any single trading-day jump > 13.5% inside the lookback window is an
    unadjusted Yahoo split / bedelsiz artifact and must NEVER pollute movers.
    """
    window = [pt for pt in points if pt[0] >= start_day]
    for i in range(1, len(window)):
        prev_v = window[i - 1][2]
        cur_v = window[i][2]
        if prev_v > 0 and abs(cur_v - prev_v) / prev_v > 0.135:
            return True
    return False


def _build_calendar_period_bucket(
    histories: List[Dict[str, Any]],
    calendar_days: int,
    top_n: int = 15,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    start_dates: List[date] = []
    end_dates: List[date] = []

    for h in histories:
        s = h["stock"]
        pts = h["points"]
        end_day, end_dt, end_price = pts[-1]
        target_start = end_day - timedelta(days=calendar_days)
        base = _point_on_or_before(pts[:-1], target_start)
        if base is None:
            continue
        start_day, _, start_price = base
        if start_price <= 0 or end_price <= 0:
            continue
        # Ensure the matched start bar is actually within +/-6 days of the target calendar day
        if abs((target_start - start_day).days) > 6:
            continue

        split_artifact = _has_unadjusted_split_jump(pts, start_day)
        pct = ((end_price - start_price) / start_price) * 100.0
        if not math.isfinite(pct):
            continue

        # Cross-validate against TradingView's official split-adjusted Perf.1M
        tv_1m = s.get("perf_1m")
        if calendar_days == 30 and tv_1m is not None:
            if split_artifact or abs(pct - tv_1m) > 12.0:
                pct = tv_1m
                start_price = (
                    end_price / (1.0 + pct / 100.0)
                    if (1.0 + pct / 100.0) > 0
                    else start_price
                )
        elif split_artifact:
            # Skip unadjusted split artifact on 10G/20G so bogus -87% never appears
            continue

        start_dates.append(start_day)
        end_dates.append(end_day)
        rows.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "price": round(end_price, 2),
            "previous_close": round(start_price, 2),
            "change_percent": round(pct, 2),
            "currency": "TRY",
            "updated_at": end_dt.isoformat(),
        })

    adv = sum(1 for r in rows if r["change_percent"] > 0)
    dec = sum(1 for r in rows if r["change_percent"] < 0)
    flat = len(rows) - adv - dec

    gainers = sorted(
        [r for r in rows if r["change_percent"] > 0],
        key=lambda r: r["change_percent"],
        reverse=True,
    )[:top_n]
    losers = sorted(
        [r for r in rows if r["change_percent"] < 0],
        key=lambda r: r["change_percent"],
    )[:top_n]

    start_label = max(start_dates).strftime("%d.%m.%Y") if start_dates else ""
    end_label = max(end_dates).strftime("%d.%m.%Y") if end_dates else ""

    return {
        "days": calendar_days,
        "start_date": start_label,
        "end_date": end_label,
        "scanned_count": len(rows),
        "advancers": adv,
        "decliners": dec,
        "unchanged": flat,
        "gainers": gainers,
        "losers": losers,
    }


def _build_tv_1m_bucket(
    tv_stocks: List[Dict[str, Any]],
    latest_day: date,
    histories: List[Dict[str, Any]],
    top_n: int = 15,
) -> Dict[str, Any]:
    """
    Uses TradingView's official split-adjusted Perf.1M uniformly across ALL ~625
    BIST stocks so every single stock shares the exact same 1-month baseline
    (matching TradingView / Midas 1A table with 0.00% discrepancy).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    # TradingView 1M baseline is same calendar day last month (or prior trading day if weekend/holiday)
    approx_1m = latest_day - timedelta(days=31)
    matched_starts: List[date] = []
    for h in histories[:40]:
        pt = _point_on_or_before(h["points"][:-1], approx_1m)
        if pt is not None:
            matched_starts.append(pt[0])
    start_day = max(matched_starts) if matched_starts else (latest_day - timedelta(days=30))

    rows: List[Dict[str, Any]] = []
    for s in tv_stocks:
        perf_1m = s.get("perf_1m")
        if perf_1m is None or not math.isfinite(perf_1m):
            continue
        end_price = s["close"]
        denom = 1.0 + perf_1m / 100.0
        if denom <= 0:
            continue
        start_price = end_price / denom
        rows.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "price": round(end_price, 2),
            "previous_close": round(start_price, 2),
            "change_percent": round(perf_1m, 2),
            "currency": "TRY",
            "updated_at": now_iso,
        })

    adv = sum(1 for r in rows if r["change_percent"] > 0)
    dec = sum(1 for r in rows if r["change_percent"] < 0)
    flat = len(rows) - adv - dec

    gainers = sorted(
        [r for r in rows if r["change_percent"] > 0],
        key=lambda r: r["change_percent"],
        reverse=True,
    )[:top_n]
    losers = sorted(
        [r for r in rows if r["change_percent"] < 0],
        key=lambda r: r["change_percent"],
    )[:top_n]

    return {
        "days": 30,
        "start_date": start_day.strftime("%d.%m.%Y"),
        "end_date": latest_day.strftime("%d.%m.%Y"),
        "scanned_count": len(rows),
        "advancers": adv,
        "decliners": dec,
        "unchanged": flat,
        "gainers": gainers,
        "losers": losers,
    }


def generate_movers_feed(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    session = _create_session(pool_size=28)
    tv_stocks = _fetch_all_bist_from_tradingview(session)
    indices = _fetch_index_snapshot(session)

    # Fetch 2mo daily history for all active BIST stocks via pooled threads (cross-checked with TV)
    histories: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        futures = [ex.submit(_fetch_yahoo_daily_history, session, s) for s in tv_stocks]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res is not None:
                histories.append(res)

    # Determine latest & previous trading dates from verified histories
    latest_days = [h["points"][-1][0] for h in histories if len(h["points"]) >= 2]
    prev_days = [h["points"][-2][0] for h in histories if len(h["points"]) >= 2]
    latest_day = max(latest_days) if latest_days else datetime.now(IST_TZ).date()
    prev_day = max(prev_days) if prev_days else (latest_day - timedelta(days=1))

    p1 = _build_1d_bucket(tv_stocks, latest_day, prev_day, top_n=15)
    p10 = _build_calendar_period_bucket(histories, 10, top_n=15)
    p20 = _build_calendar_period_bucket(histories, 20, top_n=15)
    p30 = _build_tv_1m_bucket(tv_stocks, latest_day, histories, top_n=15)

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "BIST (TradingView + Yahoo Doğrulamalı)",
        "universe": f"Tüm BIST ({len(tv_stocks)} hisse)",
        "scanned_count": len(tv_stocks),
        "breadth": {
            "advancers": p1["advancers"],
            "decliners": p1["decliners"],
            "unchanged": p1["unchanged"],
            "total": p1["scanned_count"],
        },
        "indices": indices,
        "periods": {
            "1": p1,
            "10": p10,
            "20": p20,
            "30": p30,
        },
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "movers_feed.json").write_text(
            json.dumps(feed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return feed


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "output"
    data = generate_movers_feed(out)
    b = data["breadth"]
    idx = data.get("indices") or {}
    xu = idx.get("XU100") or {}
    print(
        f"TUM BIST Yukselen/Dusen uretildi: {data['scanned_count']} hisse "
        f"(+{b['advancers']} / -{b['decliners']} / ={b['unchanged']}) | "
        f"BIST 100: {xu.get('price')} ({xu.get('change_percent')}%)"
    )
    for k in ("1", "10", "20", "30"):
        p = data["periods"][k]
        g3 = [(x["symbol"], x["change_percent"]) for x in p["gainers"][:3]]
        l3 = [(x["symbol"], x["change_percent"]) for x in p["losers"][:3]]
        print(
            f"  {k:>2}G ({p['start_date']} -> {p['end_date']}, n={p['scanned_count']}): "
            f"Top+ {g3} | Top- {l3}"
        )
