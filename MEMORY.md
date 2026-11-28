# Piyasa Sade — Kalıcı Mühendislik ve Kalite Kuralları (`MEMORY.md`)

Bu dosyadaki kurallar gerçek denetimlerde yakalanan P0/P1 hatalarından çıkarılmış **değişmez kalite kapılarıdır**. Yeni özellik geliştirirken bu kuralların hiçbiri gevşetilemez.

---

## 1. BIST Veri Doğruluğu & Bölünme (Split) Koruması
- **BIST ±%10 Devre Kesici Kuralı:** Borsa İstanbul günlük tavan/taban marjı ±%10'dur.
  - `1G` kova üreticisi (`_build_1d_bucket`) ve `validate_movers_feed` içinde `|change_percent| > 11.0` olan hiçbir kayıt **asla** yayınlanmaz (düzeltilmemiş bedelsiz/bölünme artefaktlarını — ör. `PKART -%92,89` — %100 engeller).
  - `10G` / `20G` için tek günde `>%13,5` sıçrama (`_has_unadjusted_split_jump`), `30G` için ise hem geçmiş bölünme çapraz kontrolü hem `perf_1m <= -82.0` / `>= 600.0` sınırı zorunludur.
- **Resmi Türkçe Şirket Adları:** `movers_aggregator.py` her zaman `data/bist_companies_v1.json` içindeki resmi KAP Türkçe şirket adlarını kullanır; TradingView'in bozuk ASCII / `"Class B"` ekleri doğrudan yayınlanmaz.
- **Gram Altın Şeffaflığı:** Gram Altın (`Ons × USD/TL ÷ 31,1034768`) arayüzde mutlaka **`Gram Altın (Spot/Banka)`** olarak etiketlenir; Kapalıçarşı fiziki piyasa farkı yasal metinlerde açıkça belirtilir.
- **İleriye Bakmama Kuralı:** `_point_on_or_before` yalnızca `pt[0] <= target_day` kapanışlarını seçer; asla gelecek tarihe atlamaz.

## 2. Resmi BIST Tatil & Hafta Sonu Takvimi
- `bist_calendar.py` (`2025–2027` Türkiye resmi tatilleri + Cumartesi/Pazar) hem `movers_aggregator.py` hem `daily_briefing_generator.py` için tek gerçek kaynaktır.
- Hafta sonu ve resmi BIST tatillerinde:
  - `market_status` alanı `"weekend"` veya `"holiday"` olur.
  - `active_mode` asla `"morning"` olmaz; başlıklar **"📅 Hafta Sonu Piyasa Özeti"** / **"📊 Son İşlem Günü Özeti"** olarak üretilir (**asla** *"Açılışa 30 dk"* veya *"bugün borsa kapattı"* denmez).

## 3. Ekonomi Haber Kalitesi, SEO Elemesi & Bildirim Tekilleştirme
- **Sert SEO / Tık Tuzağı Filtresi (`is_seo_clickbait_article`):** `CANLI ALTIN FİYATLARI`, `ALTIN FİYATLARI CANLI`, `GÜNCEL TABLO`, `NE ZAMAN`, `SAAT KAÇTA`, `KAÇ TL?`, `ÇEYREK/YARIM/TAM`, `EN ÇOK KAYBETTİREN HİSSELER` gibi SEO tablo başlıkları doğrudan RSS alım kapısında elenir (sadece `is_breaking` bastırılmakla bırakılmaz).
- **Olay Bazlı Tekilleştirme (`_dedupe_stories`):** Hem 40 haberlik akışta (`min_shared=3`) hem `notification_candidates` listesinde (`min_shared=2`) aynı olayın 2–3 kez tekrarlanması engellenir.
- **Bildirim Formatı:** Bildirim başlıkları en fazla **95 karakter** olacak şekilde kelime ortasından bölmeden kısaltılır ve her bildirim adayı deterministik `notification_key` taşır.
- **Tam AI Kapsama:** 40 haberin tamamı (2×20 toplu istek + önbellek) özetlenir; prompt düzeyinde *"Bu durum..."*, *"göstermektedir"*, *"yansıtabilir"* gibi boş kalıplar ve metinde olmayan sayı uydurma yasaklanır.

## 4. Sıfır Toleranslı SPK & Mevzuat Guardrail (Python + Flutter)
- Hem `piyasa_sade_engine/spk_guardrail.py` hem Flutter `lib/src/features/analysis/spk_guardrail.dart` birebir aynı Türkçe tam kelime sınırı (`(?<![a-zçğıöşü0-9])`) kurallarını uygular.
- **Doğrudan VE Dolaylı Tavsiye Engeli:** Kaba emirlerin (`al`, `sat`, `tut`, `ucuz`, `pahalı`, `hedef fiyat`) yanı sıra dolaylı yönlendirmeler (*"alım için uygun"*, *"450 TL hedefle"*, *"portföyünüzü çeşitlendirmeniz"*, *"biriktirmeye başlayabilirsiniz"*, *"yukarı yönlü hareket bekleniyor"*, *"kâr realizasyonu ... seviye net"*) de sıfır toleransla reddedilir.
- **Yanlış Pozitif Koruması:** *"altın"*, *"satış hacmi"*, *"pay geri alımı"*, *"tutarı"* ve *"Yatırım tavsiyesi değildir (YTD)"* temiz geçer.
- **Son Kapı:** Fallback bülten başlıkları da SPK + SEO filtresinden geçirilir ve `validate_daily_briefing` tüm metinlerde SPK uyumu + `disclaimer` varlığını zorunlu kılar.

## 5. Atomik Yazma, Bayatlama (Staleness) Kapısı & CDN Hijyeni
- Tüm JSON beslemeleri `atomic_write_validated_json` (`.tmp` → parse kontrolü → `os.replace`) ile yazılır; şema veya makullük doğrulaması başarısız olursa mevcut son sağlam dosya korunur.
- GitHub Actions (`economy_feed_cron.yml`) `timeout-minutes: 20` ve **Çıktı Makullük + Bayatlık (Staleness > 18 saat) Kapısı** taşır; sessiz bayatlama engellenir.
- GitHub Pages paketine (`_pages_dist/`) yalnızca 3 doğrulanmış JSON (`economy_feed.json`, `movers_feed.json`, `daily_briefing.json`) ve yasal sayfalar (`privacy.html`, `terms.html`) kopyalanır; alakasız dosya/PNG yayınlanmaz.
- Mobil istemci ve kapalı uygulama bildirim motoru **3 katmanlı CDN failover** kullanır: `GitHub Pages → jsDelivr → GitHub Raw`.

## 6. Güvenlik & Gizlilik Değişmezleri
- APK içine **asla** `GEMINI_API_KEY` gömülmez.
- Portföy dışa aktarımı yalnızca zorunlu parolalı şifreli `.psbackup` (`PBKDF2-HMAC-SHA256` + `AES-256-GCM`) formatındadır.
- Google ile giriş yapan kullanıcıların Firestore verileri yalnızca kendi `uid` alanına (`users/{uid}/private/portfolio_v2`) kilitlidir ve uygulama içinden tek tıkla kalıcı silinebilir.
