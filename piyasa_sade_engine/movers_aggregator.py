"""
Piyasa Sade — BIST 100+ Gerçek Yükselen & Düşenler Motoru (1G / 10G / 20G / 30G)
- Yaklaşık 120 likit BIST hissesini (BIST 100 çekirdeği + ana pazar) tek seferde tarar.
- Yahoo Finance günlük grafik kapanışlarını İstanbul takvim günü (UTC+3) bazında
  `meta.regularMarketPrice` ile senkronlar (hem 1 gün gecikmiş bar hatasını hem de
  aynı günün 06:30/15:09 çift yazılma bug'ını %100 önler).
- Günlük (1G) ve Son 10 / 20 / 30 İş Günü en çok yükselen & düşen İLK 15 hisseyi,
  piyasa genişliğini (yükselen/düşen hisse sayısı) ve ana endeks özetini
  `movers_feed.json` olarak üretir ($0 maliyet, 0ms istemci yüklemesi).
"""
from __future__ import annotations

import concurrent.futures
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

USER_AGENT = "Mozilla/5.0 PiyasaSade/1.3"
IST_TZ = timezone(timedelta(hours=3))

# BIST 100 + Yüksek işlem hacimli ~120 ana BIST hissesi
BIST_MOVER_UNIVERSE: List[Tuple[str, str]] = [
    ("THYAO", "Türk Hava Yolları"),
    ("ASELS", "Aselsan"),
    ("TUPRS", "Tüpraş"),
    ("GARAN", "Garanti BBVA"),
    ("AKBNK", "Akbank"),
    ("YKBNK", "Yapı Kredi Bankası"),
    ("ISCTR", "İş Bankası (C)"),
    ("VAKBN", "VakıfBank"),
    ("HALKB", "Halkbank"),
    ("TSKB", "TSKB"),
    ("ALBRK", "Albaraka Türk"),
    ("SKBNK", "Şekerbank"),
    ("KCHOL", "Koç Holding"),
    ("SAHOL", "Sabancı Holding"),
    ("AGHOL", "Anadolu Grubu Holding"),
    ("DOHOL", "Doğan Holding"),
    ("ALARK", "Alarko Holding"),
    ("TKFEN", "Tekfen Holding"),
    ("SISE", "Şişecam"),
    ("ENKAI", "Enka İnşaat"),
    ("GLYHO", "Global Yatırım Holding"),
    ("BIMAS", "BİM Mağazalar"),
    ("MGROS", "Migros Ticaret"),
    ("SOKM", "Şok Marketler"),
    ("ULKER", "Ülker Bisküvi"),
    ("CCOLA", "Coca-Cola İçecek"),
    ("AEFES", "Anadolu Efes"),
    ("MAVI", "Mavi Giyim"),
    ("TABGD", "TAB Gıda"),
    ("FROTO", "Ford Otosan"),
    ("TOASO", "Tofaş Oto Fabrika"),
    ("DOAS", "Doğuş Otomotiv"),
    ("OTKAR", "Otokar"),
    ("TTRAK", "Türk Traktör"),
    ("KARSN", "Karsan Otomotiv"),
    ("BFREN", "Bosch Fren Sistemleri"),
    ("PGSUS", "Pegasus Hava Taşımacılığı"),
    ("TAVHL", "TAV Havalimanları"),
    ("CLEBI", "Çelebi Hava Servisi"),
    ("DOCO", "DO & CO"),
    ("PASEU", "Pasifik Eurasia Lojistik"),
    ("EREGL", "Ereğli Demir Çelik"),
    ("KRDMD", "Kardemir (D)"),
    ("ARCLK", "Arçelik"),
    ("VESBE", "Vestel Beyaz Eşya"),
    ("VESTL", "Vestel Elektronik"),
    ("SASA", "SASA Polyester"),
    ("PETKM", "Petkim"),
    ("HEKTS", "Hektaş"),
    ("GUBRF", "Gübre Fabrikaları"),
    ("BRSAN", "Borusan Boru"),
    ("AKSA", "Aksa Akrilik"),
    ("TCELL", "Turkcell"),
    ("TTKOM", "Türk Telekom"),
    ("ASTOR", "Astor Enerji"),
    ("KONTR", "Kontrolmatik Teknoloji"),
    ("ENJSA", "Enerjisa Enerji"),
    ("AKSEN", "Aksa Enerji"),
    ("ODAS", "Odaş Elektrik"),
    ("ZOREN", "Zorlu Enerji"),
    ("CWENE", "CW Enerji"),
    ("GESAN", "Girişim Elektrik"),
    ("EUPWR", "Europower Enerji"),
    ("SMRTG", "Smart Güneş Enerjisi"),
    ("ALFAS", "Alfa Solar Enerji"),
    ("GWIND", "Galata Wind Enerji"),
    ("CANTE", "Çan2 Termik"),
    ("YEOTK", "YEO Teknoloji"),
    ("ENERY", "Enerya Enerji"),
    ("IZENR", "İzdemir Enerji"),
    ("AKFYE", "Akfen Yenilenebilir Enerji"),
    ("BIOEN", "Biotrend Enerji"),
    ("EUREN", "Europen Endüstri"),
    ("MIATK", "MIA Teknoloji"),
    ("SDTTR", "SDT Uzay ve Savunma"),
    ("ALTNY", "Altınay Savunma"),
    ("PAPIL", "Papilon Savunma"),
    ("LOGO", "Logo Yazılım"),
    ("ARDYZ", "ARD Bilişim"),
    ("NETCD", "Netcad Yazılım"),
    ("REEDR", "Reeder Teknoloji"),
    ("EKGYO", "Emlak Konut GYO"),
    ("ISGYO", "İş GYO"),
    ("TRGYO", "Torunlar GYO"),
    ("AKFGY", "Akfen GYO"),
    ("TRALT", "Türk Altın İşletmeleri"),
    ("TRMET", "TR Anadolu Metal Madencilik"),
    ("TRENJ", "TR Doğal Enerji"),
    ("OYAKC", "Oyak Çimento"),
    ("CIMSA", "Çimsa"),
    ("AKCNS", "Akçansa"),
    ("NUHCM", "Nuh Çimento"),
    ("GOLTS", "Göltaş Çimento"),
    ("BSOKE", "Batısöke Çimento"),
    ("BTCIM", "Batıçim"),
    ("BUCIM", "Bursa Çimento"),
    ("LMKDC", "Limak Doğu Anadolu Çimento"),
    ("BOBET", "Boğaziçi Beton"),
    ("KCAER", "Kocaer Çelik"),
    ("ISMEN", "İş Yatırım Menkul"),
    ("TURSG", "Türkiye Sigorta"),
    ("ANSGR", "Anadolu Sigorta"),
    ("AGESA", "AgeSA Hayat ve Emeklilik"),
    ("KTLEV", "Katılımevim"),
    ("MPARK", "MLP Sağlık (Medical Park)"),
    ("LKMNH", "Lokman Hekim"),
    ("SELEC", "Selçuk Ecza Deposu"),
    ("ECILC", "Eczacıbaşı İlaç"),
    ("ECZYT", "Eczacıbaşı Yatırım"),
    ("BERA", "Bera Holding"),
    ("EGEEN", "Ege Endüstri"),
    ("BRYAT", "Borusan Yatırım"),
    ("KONYA", "Konya Çimento"),
    ("TMSN", "Tümosan Motor"),
    ("SARKY", "Sarkuysan"),
    ("TUKAS", "Tukaş Gıda"),
    ("OBAMS", "Oba Makarnacılık"),
    ("KAYSE", "Kayseri Şeker"),
    ("QUAGR", "QUA Granite"),
    ("BIENY", "Bien Yapı Ürünleri"),
    ("KLSER", "Kaleseramik"),
]

# Spor hisselerini saf ekonomi/sanayi liderliğinden hariç tut
EXCLUDED_SYMBOLS = {"BJKAS", "FENER", "GSRAY", "TSPOR"}

INDEX_UNIVERSE: List[Tuple[str, str, str]] = [
    ("XU100", "XU100.IS", "BIST 100"),
    ("XU030", "XU030.IS", "BIST 30"),
    ("USDTRY", "USDTRY=X", "Dolar / TL"),
    ("EURTRY", "EURTRY=X", "Euro / TL"),
    ("GOLD", "GC=F", "Ons Altın"),
]


def _ist_date(dt: datetime):
    return dt.astimezone(IST_TZ).date()


def _fetch_index_snapshot() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sym, ysym, name in INDEX_UNIVERSE:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?interval=5m&range=1d"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
            if resp.status_code != 200:
                continue
            res = ((resp.json().get("chart") or {}).get("result") or [None])[0]
            if not res:
                continue
            meta = res.get("meta") or {}
            price = meta.get("regularMarketPrice")
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


def _fetch_stock_series(item: Tuple[str, str]) -> Optional[Dict[str, Any]]:
    sym, name = item
    if sym in EXCLUDED_SYMBOLS:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.IS?interval=1d&range=3mo"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            return None
        res = results[0]
        meta = res.get("meta") or {}
        timestamps = res.get("timestamp") or []
        quote_list = ((res.get("indicators") or {}).get("quote") or [{}])
        closes = (quote_list[0] or {}).get("close") or []

        # Deduplicate strictly by Istanbul trading day (YYYY-MM-DD)
        points: List[Tuple[datetime, float]] = []
        for t, c in zip(timestamps, closes):
            if t is None or c is None:
                continue
            val = float(c)
            if not math.isfinite(val) or val <= 0:
                continue
            dt = datetime.fromtimestamp(float(t), tz=timezone.utc)
            if points and _ist_date(dt) == _ist_date(points[-1][0]):
                points[-1] = (dt, val)
            elif not points or _ist_date(dt) > _ist_date(points[-1][0]):
                points.append((dt, val))

        if len(points) < 2:
            return None

        # Sync latest live close from meta.regularMarketPrice strictly by Istanbul calendar date
        reg_price = meta.get("regularMarketPrice")
        reg_time = meta.get("regularMarketTime")
        if reg_price is not None and math.isfinite(float(reg_price)) and float(reg_price) > 0:
            live_price = float(reg_price)
            live_dt = (
                datetime.fromtimestamp(float(reg_time), tz=timezone.utc)
                if reg_time
                else points[-1][0]
            )
            last_day = _ist_date(points[-1][0])
            live_day = _ist_date(live_dt)
            if live_day > last_day:
                points.append((live_dt, live_price))
            elif live_day == last_day:
                points[-1] = (live_dt, live_price)

        if len(points) < 2:
            return None

        # Previous DISTINCT Istanbul trading day close vs latest price
        latest_dt, latest_price = points[-1]
        prev_dt, prev_price = points[-2]
        daily_change_pct = ((latest_price - prev_price) / prev_price) * 100.0

        # Sanity check against 1d endpoint if single-day move exceeds BIST +/-12% band
        if abs(daily_change_pct) > 12.0:
            r1d = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.IS?interval=5m&range=1d",
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            if r1d.status_code == 200:
                m1d = ((r1d.json().get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
                p_live = m1d.get("regularMarketPrice")
                p_prev = m1d.get("previousClose") or m1d.get("chartPreviousClose")
                if p_live and p_prev and float(p_prev) > 0:
                    latest_price = float(p_live)
                    prev_price = float(p_prev)
                    daily_change_pct = ((latest_price - prev_price) / prev_price) * 100.0

        return {
            "symbol": sym,
            "name": name,
            "points": points,
            "latest_price": latest_price,
            "latest_dt": latest_dt,
            "prev_price": prev_price,
            "prev_dt": prev_dt,
            "daily_change_pct": daily_change_pct,
        }
    except Exception:
        return None


def _build_period_bucket(
    series_list: List[Dict[str, Any]],
    trading_days: int,
    top_n: int = 15,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    start_dates: List[datetime] = []
    end_dates: List[datetime] = []

    for s in series_list:
        pts = s["points"]
        if trading_days == 1:
            start_dt = s["prev_dt"]
            start_price = s["prev_price"]
            end_dt = s["latest_dt"]
            end_price = s["latest_price"]
            pct = s["daily_change_pct"]
        else:
            if len(pts) <= trading_days:
                continue
            start_dt, start_price = pts[-(trading_days + 1)]
            end_dt, end_price = pts[-1]
            if start_price <= 0 or end_price <= 0:
                continue
            pct = ((end_price - start_price) / start_price) * 100.0

        if not math.isfinite(pct):
            continue
        start_dates.append(start_dt)
        end_dates.append(end_dt)
        rows.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "price": round(end_price, 2),
            "previous_close": round(start_price, 2),
            "change_percent": round(pct, 2),
            "currency": "TRY",
            "updated_at": end_dt.isoformat(),
        })

    advancers = sum(1 for r in rows if r["change_percent"] > 0)
    decliners = sum(1 for r in rows if r["change_percent"] < 0)
    unchanged = len(rows) - advancers - decliners

    gainers = sorted(
        [r for r in rows if r["change_percent"] > 0],
        key=lambda r: r["change_percent"],
        reverse=True,
    )[:top_n]
    losers = sorted(
        [r for r in rows if r["change_percent"] < 0],
        key=lambda r: r["change_percent"],
    )[:top_n]

    start_label = max(start_dates).astimezone(IST_TZ).strftime("%d.%m.%Y") if start_dates else ""
    end_label = max(end_dates).astimezone(IST_TZ).strftime("%d.%m.%Y") if end_dates else ""

    return {
        "days": trading_days,
        "start_date": start_label,
        "end_date": end_label,
        "scanned_count": len(rows),
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "gainers": gainers,
        "losers": losers,
    }


def generate_movers_feed(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    series_list: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(_fetch_stock_series, BIST_MOVER_UNIVERSE):
            if res is not None:
                series_list.append(res)

    indices = _fetch_index_snapshot()
    p1 = _build_period_bucket(series_list, 1, top_n=15)

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Yahoo Finance",
        "universe": f"BIST Ana Pazar ({len(series_list)} hisse)",
        "scanned_count": len(series_list),
        "breadth": {
            "advancers": p1["advancers"],
            "decliners": p1["decliners"],
            "unchanged": p1["unchanged"],
            "total": p1["scanned_count"],
        },
        "indices": indices,
        "periods": {
            "1": p1,
            "10": _build_period_bucket(series_list, 10, top_n=15),
            "20": _build_period_bucket(series_list, 20, top_n=15),
            "30": _build_period_bucket(series_list, 30, top_n=15),
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
    print(
        f"BIST Yukselen/Dusen uretildi: {data['scanned_count']} hisse "
        f"(+{b['advancers']} / -{b['decliners']} / ={b['unchanged']})."
    )
    for k in ("1", "10", "20", "30"):
        p = data["periods"][k]
        g1 = p["gainers"][0] if p["gainers"] else {}
        l1 = p["losers"][0] if p["losers"] else {}
        print(
            f"  {k:>2}G ({p['start_date']} -> {p['end_date']}) "
            f"[g={len(p['gainers'])}, l={len(p['losers'])}]: "
            f"Top+ {g1.get('symbol')} {g1.get('change_percent')}% | "
            f"Top- {l1.get('symbol')} {l1.get('change_percent')}%"
        )
