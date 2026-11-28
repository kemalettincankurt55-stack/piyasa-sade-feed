"""
Piyasa Sade — Atomik Şema & Makullük (Sanity) Doğrulayıcı (v2)
- Sadece alan varlığına değil, finansal makullüğe ve SPK uyumuna da bakar:
  * 1G değişim sınırı: BIST günlük devre kesici ±%10 -> |change_percent| <= 11.0
  * 30G bölünme sınırı: change_percent > -85.0 (düzeltilmemiş bedelsiz artefaktını engeller)
  * price / previous_close tutarlılığı
  * daily_briefing: breadth.total >= 500, indices.XU100.price > 0, disclaimer zorunlu, SPK guardrail zorunlu
  * economy_feed: benzersiz başlık çeşitliliği (en az 8 farklı başlık)
- Geçersiz veri mevcut son sağlam JSON'ın üzerine ASLA yazılmaz.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

try:
    from piyasa_sade_engine.spk_guardrail import find_spk_violations, is_section_spk_compliant
except ImportError:
    from spk_guardrail import find_spk_violations, is_section_spk_compliant


def _is_finite_positive(val: Any) -> bool:
    try:
        f = float(val)
        return math.isfinite(f) and f > 0
    except (TypeError, ValueError):
        return False


def validate_movers_feed(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kok JSON nesnesi dict degil."]

    scanned = int(data.get("scanned_count") or 0)
    if scanned < 500:
        errors.append(f"scanned_count cok dusuk ({scanned} < 500).")

    breadth = data.get("breadth")
    if not isinstance(breadth, dict) or int(breadth.get("total") or 0) < 500:
        errors.append("breadth.total eksik veya < 500.")

    indices = data.get("indices")
    if not isinstance(indices, dict):
        errors.append("indices nesnesi eksik.")
    else:
        for required_key in ("XU100", "USDTRY", "GRAM_ALTIN"):
            entry = indices.get(required_key)
            if not isinstance(entry, dict) or not _is_finite_positive(entry.get("price")):
                errors.append(f"indices.{required_key}.price gecersiz veya eksik.")

    periods = data.get("periods")
    if not isinstance(periods, dict):
        errors.append("periods nesnesi eksik.")
    else:
        for pkey in ("1", "10", "20", "30"):
            bucket = periods.get(pkey)
            if not isinstance(bucket, dict):
                errors.append(f"periods.{pkey} eksik.")
                continue
            gainers = bucket.get("gainers")
            losers = bucket.get("losers")
            if not isinstance(gainers, list) or len(gainers) < 5:
                errors.append(f"periods.{pkey}.gainers en az 5 hisse icermeli.")
                continue
            if not isinstance(losers, list) or len(losers) < 5:
                errors.append(f"periods.{pkey}.losers en az 5 hisse icermeli.")
                continue
            if int(bucket.get("scanned_count") or 0) < 400:
                errors.append(f"periods.{pkey}.scanned_count < 400.")

            for side_name, rows in (("gainers", gainers), ("losers", losers)):
                for row in rows:
                    sym = row.get("symbol", "?")
                    price = row.get("price")
                    prev = row.get("previous_close")
                    chg = row.get("change_percent")
                    if not _is_finite_positive(price) or not _is_finite_positive(prev):
                        errors.append(f"periods.{pkey}.{side_name}[{sym}] price/prev gecersiz.")
                        continue
                    try:
                        chg_f = float(chg)
                    except (TypeError, ValueError):
                        errors.append(f"periods.{pkey}.{side_name}[{sym}] change_percent gecersiz.")
                        continue
                    if pkey == "1" and abs(chg_f) > 11.0:
                        errors.append(
                            f"periods.1.{side_name}[{sym}] BIST gunluk marjini asiyor ({chg_f}% — bolunme artefakti)."
                        )
                    if pkey == "30" and chg_f < -85.0:
                        errors.append(
                            f"periods.30.{side_name}[{sym}] olagan disi dusus ({chg_f}% — bolunme artefakti)."
                        )
    return errors


def validate_economy_feed(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kok JSON nesnesi dict degil."]

    if not isinstance(data.get("disclaimer"), str) or not data["disclaimer"].strip():
        errors.append("disclaimer alani eksik.")

    articles = data.get("articles")
    if not isinstance(articles, list) or len(articles) < 10:
        errors.append(
            f"articles listesi eksik veya yetersiz ({len(articles) if isinstance(articles, list) else 0} < 10)."
        )
    else:
        unique_titles: set[str] = set()
        for idx, art in enumerate(articles):
            if not isinstance(art, dict):
                errors.append(f"articles[{idx}] dict degil.")
                continue
            for field in ("id", "title", "url", "source", "published_at"):
                val = art.get(field)
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"articles[{idx}].{field} bos veya gecersiz.")
            title = str(art.get("title") or "").strip()
            if title:
                unique_titles.add(title.lower())
        if len(unique_titles) < 8:
            errors.append(
                f"articles icinde yeterli benzersiz baslik yok ({len(unique_titles)} < 8)."
            )

    if not isinstance(data.get("trending_clusters"), list):
        errors.append("trending_clusters liste degil.")
    if not isinstance(data.get("notification_candidates"), list):
        errors.append("notification_candidates liste degil.")
    return errors


def validate_daily_briefing(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kok JSON nesnesi dict degil."]

    if data.get("active_mode") not in ("morning", "evening"):
        errors.append(f"active_mode gecersiz: {data.get('active_mode')!r}")

    if not isinstance(data.get("disclaimer"), str) or "yatırım tavsiyesi değildir" not in data["disclaimer"].lower():
        errors.append("disclaimer alani eksik veya YTD uyarisi icermiyor.")

    breadth = data.get("breadth")
    if not isinstance(breadth, dict) or int(breadth.get("total") or 0) < 500:
        errors.append("breadth.total eksik veya < 500.")

    indices = data.get("indices")
    xu100 = indices.get("XU100") if isinstance(indices, dict) else None
    if not isinstance(xu100, dict) or not _is_finite_positive(xu100.get("price")):
        errors.append("indices.XU100.price eksik veya gecersiz.")

    for section_name in ("morning", "evening"):
        sec = data.get(section_name)
        if not isinstance(sec, dict):
            errors.append(f"{section_name} bolumu eksik.")
            continue
        for field in ("id", "title", "headline"):
            val = sec.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{section_name}.{field} bos.")
        bullets = sec.get("bullets")
        if not isinstance(bullets, list) or len(bullets) < 2:
            errors.append(f"{section_name}.bullets en az 2 madde icermeli.")
        elif any(not isinstance(b, str) or not b.strip() for b in bullets):
            errors.append(f"{section_name}.bullets icinde bos madde var.")
        else:
            texts = [
                str(sec.get("title") or ""),
                str(sec.get("headline") or ""),
                str(sec.get("notification_body") or ""),
                *[str(b) for b in bullets],
            ]
            if not is_section_spk_compliant(texts):
                violations = find_spk_violations(" ".join(texts))
                errors.append(f"{section_name} SPK guardrail ihlali iceriyor: {violations}")
    return errors


def atomic_write_validated_json(
    target_path: Path,
    data: Dict[str, Any],
    validator: Callable[[Dict[str, Any]], List[str]],
) -> bool:
    """
    Veriyi sema + makulluk dogrulamasindan gecirir; gecerliyse .tmp uzerinden atomik yazar.
    Gecersizse mevcut saglam dosyayi KORUR, ASCII-guvenli [ERROR] basar ve False dondurur.
    """
    errors = validator(data)
    if errors:
        msg = f"[ERROR] Schema validation FAILED ({target_path.name}): {'; '.join(errors)} -- preserved last known-good file!"
        print(msg.encode("ascii", errors="replace").decode("ascii"))
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    json.loads(serialized)

    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    os.replace(tmp_path, target_path)
    return True
