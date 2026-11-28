"""
Piyasa Sade — Atomik Şema Doğrulama & Güvenli JSON Yazıcı
- Üretilen JSON bozuksa, eksikse veya anlamsızsa mevcut son sağlam JSON'ın üzerine
  ASLA yazmaz; net [ERROR] günlüğü basarak diğer sağlam beslemelerin yayınlanmasına
  izin verir.
- Geçerli JSON önce .tmp dosyasına yazılıp parse doğrulanır, ardından os.replace()
  ile atomik olarak hedef dosyanın yerine taşınır.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List


def _is_finite_positive(val: Any) -> bool:
    try:
        f = float(val)
        return math.isfinite(f) and f > 0
    except (TypeError, ValueError):
        return False


def validate_movers_feed(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kök JSON nesnesi dict değil."]

    scanned = int(data.get("scanned_count") or 0)
    if scanned < 500:
        errors.append(f"scanned_count çok düşük ({scanned} < 500).")

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
                errors.append(f"indices.{required_key}.price geçersiz veya eksik.")

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
                errors.append(f"periods.{pkey}.gainers en az 5 hisse içermeli.")
            if not isinstance(losers, list) or len(losers) < 5:
                errors.append(f"periods.{pkey}.losers en az 5 hisse içermeli.")
            if int(bucket.get("scanned_count") or 0) < 400:
                errors.append(f"periods.{pkey}.scanned_count < 400.")
    return errors


def validate_economy_feed(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kök JSON nesnesi dict değil."]

    articles = data.get("articles")
    if not isinstance(articles, list) or len(articles) < 10:
        errors.append(f"articles listesi eksik veya yetersiz ({len(articles) if isinstance(articles, list) else 0} < 10).")
    else:
        for idx, art in enumerate(articles[:10]):
            if not isinstance(art, dict):
                errors.append(f"articles[{idx}] dict değil.")
                continue
            for field in ("id", "title", "url", "source", "published_at"):
                val = art.get(field)
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"articles[{idx}].{field} boş veya geçersiz.")

    if not isinstance(data.get("trending_clusters"), list):
        errors.append("trending_clusters liste değil.")
    if not isinstance(data.get("notification_candidates"), list):
        errors.append("notification_candidates liste değil.")
    return errors


def validate_daily_briefing(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Kök JSON nesnesi dict değil."]

    if data.get("active_mode") not in ("morning", "evening"):
        errors.append(f"active_mode geçersiz: {data.get('active_mode')!r}")

    for section_name in ("morning", "evening"):
        sec = data.get(section_name)
        if not isinstance(sec, dict):
            errors.append(f"{section_name} bölümü eksik.")
            continue
        for field in ("id", "title", "headline"):
            val = sec.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{section_name}.{field} boş.")
        bullets = sec.get("bullets")
        if not isinstance(bullets, list) or len(bullets) < 2:
            errors.append(f"{section_name}.bullets en az 2 madde içermeli.")
        elif any(not isinstance(b, str) or not b.strip() for b in bullets):
            errors.append(f"{section_name}.bullets içinde boş madde var.")
    return errors


def atomic_write_validated_json(
    target_path: Path,
    data: Dict[str, Any],
    validator: Callable[[Dict[str, Any]], List[str]],
) -> bool:
    """
    Veriyi şema doğrulamasından geçirir; geçerliyse .tmp üzerinden atomik yazar.
    Geçersizse mevcut sağlam dosyayı KORUR, [ERROR] basar ve False döndürür.
    """
    errors = validator(data)
    if errors:
        print(
            f"[ERROR] Şema doğrulaması BAŞARISIZ ({target_path.name}): "
            f"{'; '.join(errors)} — mevcut son sağlam dosya korundu!"
        )
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    # Roundtrip parse sanity check
    json.loads(serialized)

    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    os.replace(tmp_path, target_path)
    return True
