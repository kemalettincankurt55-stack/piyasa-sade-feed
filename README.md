# Piyasa Sade — Veri & AI İçerik Motoru (`piyasa-sade-feed`)

**Piyasa Sade** mobil uygulaması (`com.piyasasade.app`) için sunucusuz, sıfır maliyetli ($0/ay) ve API anahtarı sızdırmayan statik JSON CDN üretim hattı.

## Üretilen Canlı Akışlar (`piyasa_sade_engine/output/`)
- `economy_feed.json` — Gürültüden arındırılmış saf ekonomi haberleri, gündem kümeleri ve Gemini toplu özetleri (`news_aggregator.py`).
- `movers_feed.json` — Tüm BIST (~625 aktif hisse) için `1G / 10G / 20G / 30G` ilk 15 yükselen & düşenler, piyasa genişliği ve resmi endeks/kur/altın özeti (`movers_aggregator.py`).
- `daily_briefing.json` — Hafta içi **09:30 Sabah Bülteni** ve **18:25 Akşam Kapanış Özeti** (`daily_briefing_generator.py`).

## Çalıştırma
```bash
pip install -r requirements.txt
python piyasa_sade_engine/news_aggregator.py
python piyasa_sade_engine/movers_aggregator.py
python piyasa_sade_engine/daily_briefing_generator.py
```

> Not: Tüm çıktılar SPK mevzuatına uygundur; yatırım tavsiyesi içermez (YTD).
