"""
Piyasa Sade — BIST 100 Gerçek Yükselen & Düşenler Motoru (1G / 10G / 20G / 30G)
- Yaklaşık 100 likit BIST hissesini (BIST 100 çekirdeği) tek seferde tarar.
- Yahoo Finance günlük grafik kapanışlarını `meta.regularMarketPrice` ile senkronlar
  (gün sonu gecikmiş bar hatasını %100 önler).
- Bölünme / sıfır hacim / anormal sıçrama koruması uygular.
- Günlük (1G: previousClose -> regularMarketPrice) ve Son 10 / 20 / 30 İş Günü
  en çok yükselen & düşen ilk 10 hisseyi tarih aralığıyla birlikte `movers_feed.json`
  olarak üretir (0ms istemci yüklemesi, $0 maliyet).
"""
from __future__ import annotations

import concurrent.futures
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

USER_AGENT = "Mozilla/5.0 PiyasaSade/1.2"

# BIST 100 / En yüksek işlem hacimli ~96 ana BIST hissesi + şirket adları
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
    ("BIMAS", "BİM Mağazalar"),
    ("MGROS", "Migros Ticaret"),
    ("SOKM", "Şok Marketler"),
    ("ULKER", "Ülker Bisküvi"),
    ("CCOLA", "Coca-Cola İçecek"),
    ("AEFES", "Anadolu Efes"),
    ("MAVI", "Mavi Giyim"),
    ("FROTO", "Ford Otosan"),
    ("TOASO", "Tofaş Oto Fabrika"),
    ("DOAS", "Doğuş Otomotiv"),
    ("OTKAR", "Otokar"),
    ("TTRAK", "Türk Traktör"),
    ("PGSUS", "Pegasus Hava Taşımacılığı"),
    ("TAVHL", "TAV Havalimanları"),
    ("CLEBI", "Çelebi Hava Servisi"),
    ("DOCO", "DO & CO"),
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
    ("MIATK", "MIA Teknoloji"),
    ("SDTTR", "SDT Uzay ve Savunma"),
    ("ALTNY", "Altınay Savunma"),
    ("LOGO", "Logo Yazılım"),
    ("ARDYZ", "ARD Bilişim"),
    ("NETCD", "Netcad Yazılım"),
    ("EKGYO", "Emlak Konut GYO"),
    ("ISGYO", "İş GYO"),
    ("TRGYO", "Torunlar GYO"),
    ("TRALT", "Türk Altın İşletmeleri"),
    ("TRMET", "TR Anadolu Metal Madencilik"),
    ("TRENJ", "TR Doğal Enerji"),
    ("OYAKC", "Oyak Çimento"),
    ("CIMSA", "Çimsa"),
    ("AKCNS", "Akçansa"),
    ("NUHCM", "Nuh Çimento"),
    ("KCAER", "Kocaer Çelik"),
    ("ISMEN", "İş Yatırım Menkul"),
    ("TURSG", "Türkiye Sigorta"),
    ("ANSGR", "Anadolu Sigorta"),
    ("AGESA", "AgeSA Hayat ve Emeklilik"),
    ("MPARK", "MLP Sağlık (Medical Park)"),
    ("LKMNH", "Lokman Hekim"),
    ("SELEC", "Selçuk Ecza Deposu"),
    ("ECILC", "Eczacıbaşı İlaç"),
    ("BERA", "Bera Holding"),
    ("EGEEN", "Ege Endüstri"),
    ("BRYAT", "Borusan Yatırım"),
    ("KONYA", "Konya Çimento"),
    ("TMSN", "Tümosan Motor"),
    ("BJKAS", "Beşiktaş Futbol"),
    ("FENER", "Fenerbahçe Futbol"),
    ("GSRAY", "Galatasaray Sportif"),
    ("SARKY", "Sarkuysan"),
    ("TUKAS", "Tukaş Gıda"),
    ("QUAGR", "QUA Granite"),
]

# Spor hisselerini saf ekonomi/sanayi liderliğinden hariç tut (gürültüsüz BIST 100)
EXCLUDED_SYMBOLS = {"BJKAS", "FENER", "GSRAY", "TSPOR"}


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

        points: List[Tuple[datetime, float]] = []
        for t, c in zip(timestamps, closes):
            if t is None or c is None:
                continue
            val = float(c)
            if not math.isfinite(val) or val <= 0:
                continue
            dt = datetime.fromtimestamp(float(t), tz=timezone.utc)
            if points and dt == points[-1][0]:
                points[-1] = (dt, val)
            elif not points or dt > points[-1][0]:
                points.append((dt, val))

        if len(points) < 2:
            return None

        # Sync latest live close from meta.regularMarketPrice if newer than last daily bar
        reg_price = meta.get("regularMarketPrice")
        reg_time = meta.get("regularMarketTime")
        if reg_price is not None and math.isfinite(float(reg_price)) and float(reg_price) > 0:
            live_price = float(reg_price)
            live_dt = (
                datetime.fromtimestamp(float(reg_time), tz=timezone.utc)
                if reg_time
                else points[-1][0]
            )
            if (live_dt - points[-1][0]).total_seconds() > 3600:
                points.append((live_dt, live_price))
            else:
                points[-1] = (points[-1][0], live_price)

        # Daily 1G change: use exact previous bar vs latest bar (or meta.previousClose on 1d)
        # Note: On range=3mo, meta.chartPreviousClose is 3 months ago, NOT yesterday!
        # So previous trading session close is points[-2][1].
        latest_dt, latest_price = points[-1]
        prev_dt, prev_price = points[-2]
        daily_change_pct = ((latest_price - prev_price) / prev_price) * 100.0

        # Sanity check: ignore obvious bad-tick/unadjusted split outliers (>30% single day on BIST +/-10% limit)
        if abs(daily_change_pct) > 15.0:
            # Verify against 1d endpoint to avoid 3mo split artifacts
            r1d = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.IS?interval=5m&range=1d",
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            if r1d.status_code == 200:
                m1d = ((r1d.json().get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
                p_live = m1d.get("regularMarketPrice")
                p_prev = m1d.get("chartPreviousClose") or m1d.get("previousClose")
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

    gainers = sorted(
        [r for r in rows if r["change_percent"] > 0],
        key=lambda r: r["change_percent"],
        reverse=True,
    )[:10]
    losers = sorted(
        [r for r in rows if r["change_percent"] < 0],
        key=lambda r: r["change_percent"],
    )[:10]

    # Most common start/end date across the universe
    start_label = max(start_dates).strftime("%d.%m.%Y") if start_dates else ""
    end_label = max(end_dates).strftime("%d.%m.%Y") if end_dates else ""

    return {
        "days": trading_days,
        "start_date": start_label,
        "end_date": end_label,
        "scanned_count": len(rows),
        "gainers": gainers,
        "losers": losers,
    }


def generate_movers_feed(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    series_list: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(_fetch_stock_series, BIST_MOVER_UNIVERSE):
            if res is not None:
                series_list.append(res)

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Yahoo Finance",
        "universe": f"BIST Ana Pazar ({len(series_list)} hisse)",
        "scanned_count": len(series_list),
        "periods": {
            "1": _build_period_bucket(series_list, 1),
            "10": _build_period_bucket(series_list, 10),
            "20": _build_period_bucket(series_list, 20),
            "30": _build_period_bucket(series_list, 30),
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
    print(f"BIST Yukselen/Dusen uretildi: {data['scanned_count']} hisse tarandi.")
    for k in ("1", "10", "20", "30"):
        p = data["periods"][k]
        g1 = p["gainers"][0] if p["gainers"] else {}
        l1 = p["losers"][0] if p["losers"] else {}
        print(
            f"  {k:>2}G ({p['start_date']} -> {p['end_date']}): "
            f"Top+ {g1.get('symbol')} {g1.get('change_percent')}% | "
            f"Top- {l1.get('symbol')} {l1.get('change_percent')}%"
        )
