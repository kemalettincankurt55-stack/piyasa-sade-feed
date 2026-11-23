"""
Piyasa Sade — Saf Ekonomi & Gündem Haber Toplayıcı + AI Sentez Motoru
- Sadece ekonomi, borsa, KAP, TCMB, altın/döviz ve X/Twitter ekonomi gündemini tarar.
- Futbol, magazin, dizi, burç ve alakasız gürültüyü sert filtreyle eler.
- Telif koruması: Hiçbir haberin gövde metnini kopyalamaz; yalnızca başlık,
  kaynak adı, orijinal link ve Gemini API ile üretilmiş özgün 2-3 maddelik
  SPK uyumlu özet sunar.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

USER_AGENT = "Mozilla/5.0 PiyasaSade/1.2"

# Futbol, magazin ve alakasız gürültü kelimeleri (kesin eleme)
BLOCKED_KEYWORDS = {
    "futbol", "süper lig", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "şampiyonlar ligi", "transfer", "maç sonucu", "teknik direktör", "hakem",
    "magazin", "survivor", "dizi", "fragman", "oyuncu", "şarkıcı", "ünlü isim",
    "boşandı", "evlendi", "aşk", "burç", "astroloji", "tarot", "reyting",
    "masterchef", "müge anlı", "esra erol", "cinayet", "trafik kazası",
}

# Saf ekonomi & gündem RSS sorguları
ECONOMY_FEEDS = [
    ("Borsa & KAP", "Borsa İstanbul OR BIST 100 OR KAP bildirimi when:2d"),
    ("Makro & TCMB", "TCMB OR faiz kararı OR enflasyon OR ekonomi haberleri when:2d"),
    ("Altın & Döviz", "gram altın OR ons altın OR dolar TL OR euro TL when:2d"),
    ("Gündem & X", "fon mağdurları OR SPK OR COP31 ekonomi OR enerji piyasası OR kripto düzenleme when:3d"),
    ("Küresel", "Fed faiz OR Nasdaq OR S&P 500 OR küresel piyasalar when:2d"),
]

STOPWORDS = {
    "ve", "veya", "ile", "için", "son", "dakika", "göre", "bir", "bu", "da", "de",
    "ne", "nasıl", "neden", "kadar", "oldu", "olacak", "yeni", "açıklaması",
    "haberleri", "gündem", "türkiye", "ekonomi", "piyasa", "piyasalar",
}


def is_noise(title: str, source: str) -> bool:
    haystack = f"{title} {source}".lower()
    return any(word in haystack for word in BLOCKED_KEYWORDS)


def clean_title_and_source(raw_title: str, explicit_source: str) -> tuple[str, str]:
    source = explicit_source.strip()
    title = raw_title.strip()
    if not source and " - " in title:
        parts = title.rsplit(" - ", 1)
        title, source = parts[0].strip(), parts[1].strip()
    elif source and title.endswith(f" - {source}"):
        title = title[: -(len(source) + 3)].strip()
    return title, source or "Ekonomi Kaynağı"


def parse_pub_date(raw: str) -> str:
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def fetch_google_news_rss(category: str, query: str, timeout: int = 12) -> List[Dict[str, Any]]:
    url = (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        "&hl=tr&gl=TR&ceid=TR:tr"
    )
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items: List[Dict[str, Any]] = []
    for item in root.findall(".//item")[:15]:
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        explicit_source = (source_el.text or "").strip() if source_el is not None else ""
        title, source = clean_title_and_source(raw_title, explicit_source)
        if not title or not link.startswith("http") or is_noise(title, source):
            continue
        item_id = hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]
        items.append({
            "id": item_id,
            "title": title,
            "source": source,
            "url": link,
            "category": category,
            "published_at": parse_pub_date(pub_date),
            "is_breaking": any(
                k in title.lower()
                for k in ("son dakika", "tcmb", "faiz", "spk", "kap", "rekor", "sert düşüş", "cop31", "fon")
            ),
        })
    return items


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-zçğıöşü0-9]{4,}", title.lower())
    return {w for w in words if w not in STOPWORDS}


def cluster_trending_topics(articles: List[Dict[str, Any]], max_clusters: int = 5) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    used: set[str] = set()

    for article in articles:
        if article["id"] in used:
            continue
        tokens = _title_tokens(article["title"])
        if not tokens:
            continue
        group = [article]
        used.add(article["id"])
        for other in articles:
            if other["id"] in used:
                continue
            other_tokens = _title_tokens(other["title"])
            overlap = tokens & other_tokens
            if len(overlap) >= 2:
                group.append(other)
                used.add(other["id"])
        if len(group) >= 2 or article["is_breaking"]:
            sources = []
            seen_src = set()
            for g in group:
                if g["source"] not in seen_src:
                    seen_src.add(g["source"])
                    sources.append({"name": g["source"], "url": g["url"]})
            clusters.append({
                "id": f"cluster-{article['id']}",
                "headline": article["title"],
                "category": article["category"],
                "is_breaking": any(g["is_breaking"] for g in group),
                "source_count": len(sources),
                "sources": sources[:4],
                "published_at": group[0]["published_at"],
                "raw_headlines": [g["title"] for g in group[:5]],
                "bullets": [
                    f"{len(sources)} farklı ekonomi kaynağında öne çıkan başlık.",
                    "Detaylar için aşağıdaki orijinal kaynak bağlantılarına gidebilirsiniz.",
                ],
                "ai_synthesized": False,
            })
        if len(clusters) >= max_clusters:
            break
    return clusters


def synthesize_clusters_with_gemini(
    clusters: List[Dict[str, Any]],
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key or not clusters:
        return clusters

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={key}"
    )
    for cluster in clusters:
        headlines_text = "\n".join(f"- {h}" for h in cluster["raw_headlines"])
        prompt = (
            "Aşağıdaki ekonomi haber başlıklarını okuyarak telif hakkına saygılı, "
            "tamamen özgün, tarafsız ve SPK mevzuatına uygun (kesinlikle al/sat tavsiyesi "
            "vermeden) kısa bir Türkçe özet üret.\n"
            "Sadece geçerli JSON döndür: {\"headline\": \"kısa net başlık\", "
            "\"bullets\": [\"Ne oldu (1 cümle)\", \"Piyasaya olası etkisi (1 cümle)\"]}\n\n"
            f"Başlıklar:\n{headlines_text}"
        )
        try:
            resp = requests.post(
                endpoint,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(text)
            if isinstance(parsed.get("headline"), str) and isinstance(parsed.get("bullets"), list):
                cluster["headline"] = parsed["headline"].strip()
                cluster["bullets"] = [str(b).strip() for b in parsed["bullets"][:3] if str(b).strip()]
                cluster["ai_synthesized"] = True
        except Exception:
            continue
    return clusters


def batch_summarize_articles_with_gemini(
    articles: List[Dict[str, Any]],
    cached_summaries: Dict[str, str],
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    10.000 kullanici icin 10.000 ayri cagri yerine:
    - Daha once ozetlenmis haberleri cache'ten alir (0 API cagrisi).
    - Yalnizca yeni haberleri TEK bir toplu Gemini istegiyle ozetler.
    """
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    to_summarize: List[Dict[str, Any]] = []
    for article in articles:
        existing = cached_summaries.get(article["id"])
        if existing:
            article["ai_summary"] = existing
        else:
            to_summarize.append(article)

    if not key or not to_summarize:
        return articles

    batch = to_summarize[:25]
    lines = "\n".join(
        f"{idx}. [{a['id']}] ({a['source']}) {a['title']}"
        for idx, a in enumerate(batch, start=1)
    )
    prompt = (
        "Aşağıdaki ekonomi haber başlıklarının her biri için telif hakkına saygılı, "
        "tamamen özgün, tarafsız ve SPK mevzuatına uygun (kesinlikle al/sat tavsiyesi "
        "vermeden) 2 kısa cümlelik Türkçe özet ve olası piyasa etkisi yaz.\n"
        "Sadece geçerli JSON nesnesi döndür: {\"<id>\": \"2 cümlelik sade özet ve olası etki\"}\n\n"
        f"Haberler:\n{lines}"
    )
    for model in ("gemini-2.5-flash", "gemini-3.1-flash-lite"):
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            resp = requests.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": key,
                },
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=35,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for a in batch:
                    summary = parsed.get(a["id"])
                    if isinstance(summary, str) and summary.strip():
                        a["ai_summary"] = summary.strip()
                break
        except Exception:
            continue
    return articles


def generate_economy_feed(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    cached_summaries: Dict[str, str] = {}
    if output_dir is not None:
        existing_file = output_dir / "economy_feed.json"
        if existing_file.exists():
            try:
                old_data = json.loads(existing_file.read_text(encoding="utf-8"))
                for old_a in old_data.get("articles", []):
                    if old_a.get("id") and old_a.get("ai_summary"):
                        cached_summaries[old_a["id"]] = old_a["ai_summary"]
            except Exception:
                pass

    all_articles: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    for category, query in ECONOMY_FEEDS:
        try:
            items = fetch_google_news_rss(category, query)
            for item in items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    all_articles.append(item)
        except Exception:
            continue

    all_articles.sort(key=lambda x: x["published_at"], reverse=True)
    all_articles = all_articles[:40]
    all_articles = batch_summarize_articles_with_gemini(all_articles, cached_summaries)
    trending = cluster_trending_topics(all_articles)
    trending = synthesize_clusters_with_gemini(trending)
    for c in trending:
        c.pop("raw_headlines", None)

    notification_candidates = [
        {
            "id": a["id"],
            "title": f"[{a['category']}] {a['source']}",
            "body": a["title"],
            "url": a["url"],
            "published_at": a["published_at"],
        }
        for a in all_articles
        if a["is_breaking"]
    ][:5]

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Haber başlıkları orijinal kaynaklara aittir; yatırım tavsiyesi değildir (YTD).",
        "trending_clusters": trending,
        "articles": all_articles[:40],
        "notification_candidates": notification_candidates,
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "economy_feed.json").write_text(
            json.dumps(feed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return feed


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "output"
    result = generate_economy_feed(out)
    print(
        f"Ekonomi akışı üretildi: {len(result['articles'])} haber, "
        f"{len(result['trending_clusters'])} gündem kartı, "
        f"{len(result['notification_candidates'])} bildirim adayı."
    )
