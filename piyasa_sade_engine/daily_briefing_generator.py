"""
Piyasa Sade — Gemini Destekli Sabah Bülteni (Piyasa Öncesi 09:30) & Akşam Kapanış Özeti (18:25)
- APK içine API anahtarı koymadan, GitHub Actions üzerinde GEMINI_API_KEY ile çalışır ($0 maliyet).
- `movers_feed.json` (BIST piyasa genişliği, endeksler, yükselen/düşen liderler) +
  `economy_feed.json` + güncel Türkiye ekonomik takvim/gündem RSS başlıklarını birleştirir.
- SPK mevzuatına tam uyumlu (al/sat tavsiyesi içermez, yalnızca doğrulanmış veri ve gündem yorumu).
- Çıktı: `output/daily_briefing.json`
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

try:
    from piyasa_sade_engine.bist_calendar import (
        IST_TZ,
        get_market_status,
        is_bist_trading_day,
    )
    from piyasa_sade_engine.feed_validator import atomic_write_validated_json, validate_daily_briefing
    from piyasa_sade_engine.news_aggregator import is_seo_clickbait_article, sanitize_headline
    from piyasa_sade_engine.spk_guardrail import (
        find_spk_violations,
        is_section_spk_compliant,
        is_spk_compliant,
    )
except ImportError:
    from bist_calendar import (
        IST_TZ,
        get_market_status,
        is_bist_trading_day,
    )
    from feed_validator import atomic_write_validated_json, validate_daily_briefing
    from news_aggregator import is_seo_clickbait_article, sanitize_headline
    from spk_guardrail import (
        find_spk_violations,
        is_section_spk_compliant,
        is_spk_compliant,
    )

USER_AGENT = "Mozilla/5.0 PiyasaSade/1.5"

AGENDA_QUERIES = [
    '("TCMB" OR "faiz kararı" OR "enflasyon" OR "veri takvimi" OR "ekonomik takvim" OR "piyasalar güne") when:2d',
    '("KAP" OR "SPK" OR "bilanço" OR "temettü" OR "dava" OR "ihale" OR "bedelsiz") (BIST OR borsa OR hisse) when:2d',
    '("Fed" OR "ECB" OR "petrol" OR "ons altın" OR "Nasdaq" OR "küresel piyasalar") ekonomi when:2d',
]

NOISE_WORDS = {
    "futbol", "süper lig", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "dizi", "survivor", "burç", "reyting", "cinayet", "trafik kazası",
}


def _fetch_agenda_headlines() -> List[str]:
    headlines: List[str] = []
    seen: set[str] = set()
    for q in AGENDA_QUERIES:
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(q)}"
            "&hl=tr&gl=TR&ceid=TR:tr"
        )
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                lower = title.lower()
                if any(w in lower for w in NOISE_WORDS):
                    continue
                if title not in seen:
                    seen.add(title)
                    headlines.append(title)
        except Exception:
            continue
    return headlines[:18]


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "-"
    sign = "+" if val >= 0 else "-"
    return f"{sign}%{abs(val):.2f}"


def _build_fallback_briefings(
    movers: Dict[str, Any],
    headlines: List[str],
    ist_now: datetime,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    date_iso = ist_now.strftime("%Y-%m-%d")
    date_tr = ist_now.strftime("%d.%m.%Y")
    breadth = movers.get("breadth") or {}
    adv = int(breadth.get("advancers") or 0)
    dec = int(breadth.get("decliners") or 0)
    tot = int(breadth.get("total") or movers.get("scanned_count") or 0)
    idx = movers.get("indices") or {}
    xu100 = idx.get("XU100") or {}
    usd = idx.get("USDTRY") or {}
    gram = idx.get("GRAM_ALTIN") or {}
    p1 = (movers.get("periods") or {}).get("1") or {}
    top_g = (p1.get("gainers") or [])[:3]
    top_l = (p1.get("losers") or [])[:3]

    xu100_str = (
        f"BIST 100 {xu100.get('price')} ({_fmt_pct(xu100.get('change_percent'))})"
        if xu100
        else "BIST 100 verisi güncelleniyor"
    )
    usd_str = f"Dolar/TL {usd.get('price')} ({_fmt_pct(usd.get('change_percent'))})" if usd else ""
    gram_str = f"Gram Altın {gram.get('price')} TL ({_fmt_pct(gram.get('change_percent'))})" if gram else ""

    safe_headlines = [
        sanitize_headline(h) for h in headlines
        if is_spk_compliant(h) and not is_seo_clickbait_article(h)
    ]
    trading_day = is_bist_trading_day(ist_now.date())
    market_status = get_market_status(ist_now)

    evening_bullets = [
        (
            f"Taranan {tot} ana BIST hissesinden {adv} tanesi son işlem gününü yükselişle, {dec} tanesi düşüşle tamamladı."
            if not trading_day
            else f"Taranan {tot} ana BIST hissesinden {adv} tanesi günü yükselişle, {dec} tanesi düşüşle tamamladı."
        ),
        f"{xu100_str}" + (f" · {usd_str}" if usd_str else "") + (f" · {gram_str}" if gram_str else "") + ".",
    ]
    if top_g:
        g_txt = ", ".join(f"{x['symbol']} ({_fmt_pct(x['change_percent'])})" for x in top_g)
        evening_bullets.append(f"Son seansta öne çıkan yükselenler: {g_txt}.")
    if top_l:
        l_txt = ", ".join(f"{x['symbol']} ({_fmt_pct(x['change_percent'])})" for x in top_l)
        evening_bullets.append(f"Satış baskısının yoğunlaştığı hisseler: {l_txt}.")
    if safe_headlines:
        evening_bullets.append(f"Öne çıkan ekonomi başlığı: {safe_headlines[0]}.")

    morning_bullets = [
        f"Son işlem gününde {xu100_str} seviyesinden kapandı; {tot} ana hisseden {adv} hisse artıda, {dec} hisse ekside yer aldı.",
    ]
    if usd_str or gram_str:
        morning_bullets.append(f"Döviz ve emtia görünümü: {' · '.join(x for x in (usd_str, gram_str) if x)}.")
    for h in safe_headlines[:3]:
        morning_bullets.append(f"Gündem başlığı: {h}.")

    if not trading_day:
        closed_label = "Hafta Sonu" if market_status == "weekend" else "Resmi Tatil"
        morning_title = f"📅 {closed_label} Piyasa Özeti • {date_tr}"
        morning_headline = f"Borsa İstanbul {closed_label.lower()} nedeniyle kapalı; son seans ve ekonomi gündemi ({date_tr})"
        evening_title = f"📊 Son İşlem Günü Özeti • {xu100_str}"
        evening_headline = f"Borsa İstanbul {closed_label.lower()} nedeniyle kapalı; son seansta {adv} yükselen, {dec} düşen ({date_tr})"
    else:
        morning_title = f"☀️ Piyasa Açılışına 30 dk • {date_tr}"
        morning_headline = f"BIST açılışı öncesi piyasa görünümü ve günün başlıkları ({date_tr})"
        evening_title = f"🌙 Borsa Kapanış Özeti • {xu100_str}"
        evening_headline = f"Borsa İstanbul gün sonu özeti: {adv} yükselen, {dec} düşen ({date_tr})"

    morning = {
        "id": f"morning-{date_iso}",
        "type": "morning",
        "date": date_tr,
        "ready_at_tr": "09:30" if trading_day else "kapali",
        "title": morning_title,
        "headline": morning_headline,
        "notification_body": f"Son kapanış: {xu100_str}. {morning_bullets[1] if len(morning_bullets) > 1 else ''}".strip(),
        "bullets": morning_bullets[:5],
        "ai_generated": False,
    }
    evening = {
        "id": f"evening-{date_iso}",
        "type": "evening",
        "date": date_tr,
        "ready_at_tr": "18:25" if trading_day else "kapali",
        "title": evening_title,
        "headline": evening_headline,
        "notification_body": f"{xu100_str}. {tot} ana BIST hissesinden {dec} tanesi düşüşte, {adv} tanesi yükselişte kapattı.",
        "bullets": evening_bullets[:5],
        "ai_generated": False,
    }
    return morning, evening


def _call_gemini_briefings(
    movers: Dict[str, Any],
    economy_articles: List[Dict[str, Any]],
    agenda_headlines: List[str],
    ist_now: datetime,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    date_tr = ist_now.strftime("%d.%m.%Y")
    breadth = movers.get("breadth") or {}
    idx = movers.get("indices") or {}
    p1 = (movers.get("periods") or {}).get("1") or {}
    top_g = (p1.get("gainers") or [])[:5]
    top_l = (p1.get("losers") or [])[:5]

    news_lines = []
    seen = set()
    for a in economy_articles[:14]:
        t = (a.get("title") or "").strip()
        if t and t not in seen:
            seen.add(t)
            news_lines.append(f"- [{a.get('category', 'Ekonomi')}] {t}")
    for h in agenda_headlines[:12]:
        if h not in seen:
            seen.add(h)
            news_lines.append(f"- [Gündem/Takvim] {h}")

    context_block = json.dumps(
        {
            "tarih_istanbul": date_tr,
            "saat_istanbul": ist_now.strftime("%H:%M"),
            "piyasa_genisligi": breadth,
            "endeksler": idx,
            "gunluk_en_cok_yukselenler_ilk5": [
                {"sembol": x["symbol"], "ad": x["name"], "fiyat": x["price"], "degisim_yuzde": x["change_percent"]}
                for x in top_g
            ],
            "gunluk_en_cok_dusenler_ilk5": [
                {"sembol": x["symbol"], "ad": x["name"], "fiyat": x["price"], "degisim_yuzde": x["change_percent"]}
                for x in top_l
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = (
        "Sen 'Piyasa Sade' finans uygulamasının kıdemli Türkçe piyasa editörüsün.\n"
        "Aşağıdaki DOĞRULANMIŞ gerçek piyasa verileri ve güncel ekonomi/KAP haber başlıklarını kullanarak "
        "iki ayrı bülten üret:\n"
        "1) `morning` (Sabah Bülteni — BIST açılmadan 30 dk önce 09:30 bildirimi): "
        "Önceki işlem gününde BIST/altın/dövizde ne olduğunu kısaca özetle ve BUGÜN takip edilecek en önemli "
        "3-5 gelişmeyi (TCMB/Fed/enflasyon/ekonomik veri, önemli KAP/şirket davası/bilanço/ihale veya küresel etki — "
        "YALNIZCA aşağıdaki haberlerde gerçekten geçen olayları yaz, ASLA uydurma tarih veya olay ekleme!) maddeler halinde ver.\n"
        "2) `evening` (Akşam Kapanış Özeti — BIST kapandıktan 15 dk sonra 18:25 bildirimi): "
        "Bugün Borsa İstanbul'da taranan hisselerin kaçının düştüğünü/yükseldiğini, BIST 100, Dolar ve Gram Altın'daki "
        "hareketi, öne çıkan hisse/sektörleri ve haber başlıklarına göre bu hareketin arkasındaki olası nedenleri "
        "3-5 akıcı, sade Türkçe madde halinde yorumla.\n\n"
        "KESİN KURALLAR:\n"
        "- SPK mevzuatına %100 uy: ASLA al/sat/tut tavsiyesi, hedef fiyat veya kesin getiri vaadi verme.\n"
        "- ASLA aşağıdaki verilerde olmayan bir sayı, hisse veya dava/karar uydurma.\n"
        "- Dilin sade, anlaşılır, haber bülteni gibi net ve profesyonel olsun.\n"
        "- Yalnızca geçerli JSON döndür:\n"
        "{\n"
        '  "morning": {\n'
        '    "title": "☀️ Piyasa Açılışına 30 dk: kısa çarpıcı başlık (max 60 karakter)",\n'
        '    "headline": "Tek cümlelik sabah özeti",\n'
        '    "notification_body": "Bildirim önizlemesi için 1-2 cümlelik net özet (max 180 karakter)",\n'
        '    "bullets": ["Madde 1", "Madde 2", "Madde 3", "Madde 4"]\n'
        "  },\n"
        '  "evening": {\n'
        '    "title": "🌙 Borsa Kapanış Özeti: kısa çarpıcı başlık (max 60 karakter)",\n'
        '    "headline": "Tek cümlelik akşam kapanış özeti",\n'
        '    "notification_body": "Bildirim önizlemesi için 1-2 cümlelik net özet (max 180 karakter)",\n'
        '    "bullets": ["Madde 1", "Madde 2", "Madde 3", "Madde 4"]\n'
        "  }\n"
        "}\n\n"
        f"DOĞRULANMIŞ PİYASA VERİSİ:\n{context_block}\n\n"
        "GÜNCEL EKONOMİ & KAP BAŞLIKLARI:\n" + "\n".join(news_lines)
    )

    for model in ("gemini-2.5-flash", "gemini-2.0-flash"):
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            resp = requests.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.25,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=35,
            )
            if resp.status_code != 200:
                print(f"[WARN] Gemini ({model}) HTTP {resp.status_code}")
                continue
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(text)
            if isinstance(parsed.get("morning"), dict) and isinstance(parsed.get("evening"), dict):
                return parsed
        except Exception as exc:
            print(f"[WARN] Gemini ({model}) call failed: {exc}")
            continue
    return None


def generate_daily_briefing(output_dir: Path) -> Dict[str, Any]:
    ist_now = datetime.now(IST_TZ)
    date_iso = ist_now.strftime("%Y-%m-%d")
    date_tr = ist_now.strftime("%d.%m.%Y")

    movers_path = output_dir / "movers_feed.json"
    economy_path = output_dir / "economy_feed.json"
    briefing_path = output_dir / "daily_briefing.json"

    movers = json.loads(movers_path.read_text(encoding="utf-8")) if movers_path.exists() else {}
    economy = json.loads(economy_path.read_text(encoding="utf-8")) if economy_path.exists() else {}
    prev_briefing = json.loads(briefing_path.read_text(encoding="utf-8")) if briefing_path.exists() else {}

    prev_morning = prev_briefing.get("morning")
    prev_evening = prev_briefing.get("evening")
    has_frozen_morning = (
        isinstance(prev_morning, dict)
        and prev_morning.get("id") == f"morning-{date_iso}"
        and prev_morning.get("ai_generated") is True
        and ist_now.hour >= 10
    )
    # Freeze evening closing summary once generated after BIST closing settlement (18:20 TR)
    # so it stays stable all evening/night and avoids redundant Gemini API calls!
    prev_gen_raw = prev_briefing.get("istanbul_time") or ""
    evening_generated_after_close = False
    if prev_gen_raw.startswith(date_tr) and len(prev_gen_raw) >= 16:
        try:
            hh, mm = map(int, prev_gen_raw[11:16].split(":"))
            evening_generated_after_close = (hh * 60 + mm) >= (18 * 60 + 18)
        except ValueError:
            pass
    has_frozen_evening = (
        isinstance(prev_evening, dict)
        and prev_evening.get("id") == f"evening-{date_iso}"
        and prev_evening.get("ai_generated") is True
        and evening_generated_after_close
        and (ist_now.hour * 60 + ist_now.minute) >= (18 * 60 + 20)
    )

    articles = economy.get("articles") or []
    agenda_headlines = _fetch_agenda_headlines()
    all_headlines = [a.get("title", "") for a in articles[:8] if a.get("title")] + agenda_headlines

    morning, evening = _build_fallback_briefings(movers, all_headlines, ist_now)

    trading_day = is_bist_trading_day(ist_now.date())
    market_status = get_market_status(ist_now)

    # On weekends & official BIST holidays, NEVER call AI for fake "bugün borsa kapattı" or "açılışa 30 dk"!
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if trading_day and api_key and not (has_frozen_morning and has_frozen_evening):
        ai_result = _call_gemini_briefings(movers, articles, agenda_headlines, ist_now, api_key)
        if ai_result:
            for key, target in (("morning", morning), ("evening", evening)):
                src = ai_result.get(key) or {}
                bullets = [str(b).strip() for b in (src.get("bullets") or []) if str(b).strip()][:5]
                if bullets:
                    cand_title = str(src.get("title") or target["title"]).strip()
                    cand_headline = str(src.get("headline") or target["headline"]).strip()
                    cand_notif = str(
                        src.get("notification_body") or target["notification_body"]
                    ).strip()
                    all_texts = [cand_title, cand_headline, cand_notif, *bullets]
                    if is_section_spk_compliant(all_texts):
                        target["title"] = cand_title
                        target["headline"] = cand_headline
                        target["notification_body"] = cand_notif
                        target["bullets"] = bullets
                        target["ai_generated"] = True
                    else:
                        violations = find_spk_violations(" ".join(all_texts))
                        print(f"[WARN][SPK] {key} AI bulteni reddedildi (sifir tolerans): {violations}")

    if trading_day and has_frozen_morning:
        morning = prev_morning
    if trading_day and has_frozen_evening:
        evening = prev_evening

    # Morning mode strictly 06:00 - 12:00 TR on official BIST trading days; Evening/Closed summary otherwise
    active_mode = "morning" if (trading_day and 6 <= ist_now.hour < 12) else "evening"

    payload = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "istanbul_time": ist_now.strftime("%d.%m.%Y %H:%M"),
        "date": date_tr,
        "market_status": market_status,
        "active_mode": active_mode,
        "disclaimer": "Yatırım tavsiyesi değildir (YTD). Doğrulanmış piyasa verileri ve ekonomi başlıklarından üretilmiştir.",
        "breadth": movers.get("breadth") or {},
        "indices": movers.get("indices") or {},
        "morning": morning,
        "evening": evening,
    }

    atomic_write_validated_json(
        briefing_path,
        payload,
        validate_daily_briefing,
    )
    return payload


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "output"
    res = generate_daily_briefing(out)
    print(
        f"Gunluk bulten uretildi ({res['istanbul_time']}): "
        f"morning_ai={res['morning']['ai_generated']} ({len(res['morning']['bullets'])} madde) | "
        f"evening_ai={res['evening']['ai_generated']} ({len(res['evening']['bullets'])} madde)"
    )
    print("  MORNING:", res["morning"]["title"])
    for b in res["morning"]["bullets"]:
        print("   •", b)
    print("  EVENING:", res["evening"]["title"])
    for b in res["evening"]["bullets"]:
        print("   •", b)
