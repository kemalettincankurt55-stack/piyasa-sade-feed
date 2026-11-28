import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from piyasa_sade_engine.bist_calendar import (
    IST_TZ,
    get_market_status,
    is_bist_holiday,
    is_bist_trading_day,
)
from piyasa_sade_engine.daily_briefing_generator import _build_fallback_briefings, _fmt_pct
from piyasa_sade_engine.feed_validator import (
    atomic_write_validated_json,
    validate_daily_briefing,
    validate_economy_feed,
    validate_movers_feed,
)
from piyasa_sade_engine.movers_aggregator import (
    _build_1d_bucket,
    _build_tv_1m_bucket,
    _clean_company_name,
    _has_unadjusted_split_jump,
    _is_bist_session_active,
)
from piyasa_sade_engine.news_aggregator import (
    _dedupe_stories,
    _truncate_clean,
    clean_title_and_source,
    is_breaking_headline,
    is_noise,
    is_seo_clickbait_article,
    sanitize_headline,
)
from piyasa_sade_engine.spk_guardrail import (
    find_spk_violations,
    is_section_spk_compliant,
    is_spk_compliant,
)


class EngineAuditTests(unittest.TestCase):
    def test_breaking_headline_avoids_substring_false_positives(self):
        self.assertFalse(is_breaking_headline("Fabrikada kapasite kullanım oranı açıklandı"))
        self.assertFalse(is_breaking_headline("Yeni vergi paketi kapsam içine alındı"))
        self.assertFalse(is_breaking_headline("Akıllı telefon satışlarında artış yaşandı"))
        self.assertFalse(is_breaking_headline("Şirket bilançosunda konsolidasyon tamamlandı"))
        self.assertFalse(is_breaking_headline("İhracatta rekor beklentisi konuşuluyor"))
        self.assertFalse(is_breaking_headline("Fed faiz kararı ne zaman açıklanacak? Saat kaçta?"))
        self.assertFalse(is_breaking_headline("TCMB'nin faiz indirimi planı değişebilir"))

        self.assertTrue(is_breaking_headline("THYAO'dan KAP bildirimi: Yeni uçak siparişi"))
        self.assertTrue(is_breaking_headline("KAP'a yapılan açıklamada temettü tarihi duyuruldu"))
        self.assertTrue(is_breaking_headline("Son dakika: TCMB faiz kararı açıklandı"))
        self.assertTrue(is_breaking_headline("SPK bülteninde 3 şirkete sermaye artırımı onayı"))
        self.assertTrue(is_breaking_headline("BIST 100 endeksinde devre kesici uygulandı"))
        self.assertTrue(is_breaking_headline("Gram altın rekor kırdı"))

    def test_seo_clickbait_and_story_dedupe(self):
        self.assertTrue(is_seo_clickbait_article("CANLI ALTIN FİYATLARI GÜNCEL TABLO 27 KASIM 2026 (ANLIK TAKİP)"))
        self.assertTrue(is_seo_clickbait_article("MERKEZ BANKASI FAİZ KARARI NE ZAMAN AÇIKLANACAK?"))
        self.assertTrue(is_seo_clickbait_article("En Çok Kaybettiren Hisseler: 27 Kasım 2026 | BIST 100"))
        self.assertTrue(is_seo_clickbait_article("Gram altın 6 bin TL sınırına dayandı: İşte güncel fiyatlar"))
        self.assertFalse(is_seo_clickbait_article("TCMB'den TL likiditesi ve zorunlu karşılıklara yeni düzenleme"))
        self.assertEqual(
            sanitize_headline("KAP *** BORSA İSTANBUL *** PRZMA *** Pazar Değişikliği"),
            "KAP — BORSA İSTANBUL — PRZMA — Pazar Değişikliği",
        )

        deduped = _dedupe_stories(
            [
                {"id": "1", "title": "Gram altın yeniden 6 bin TL sınırını aştı"},
                {"id": "2", "title": "SON DAKİKA: Gram altın yeniden 6 bin TL seviyesini aştı"},
                {"id": "3", "title": "Borsa İstanbul günü yükselişle tamamladı"},
            ],
            min_shared=2,
        )
        self.assertEqual(len(deduped), 2)
        self.assertTrue(len(_truncate_clean("A " * 80, 95)) <= 95)

    def test_1d_and_30d_buckets_reject_pkart_unadjusted_split_artifact(self):
        fake_stocks = [
            {"symbol": "PKART", "name": "PLASTİKKART", "close": 2.64, "prev_close": 35.0, "change_1d": -92.89, "perf_1m": -91.03, "volume": 100000},
            {"symbol": "THYAO", "name": "TÜRK HAVA YOLLARI A.O.", "close": 310.0, "prev_close": 300.0, "change_1d": 3.33, "perf_1m": 12.4, "volume": 5000000},
            {"symbol": "ASELS", "name": "ASELSAN", "close": 85.0, "prev_close": 88.0, "change_1d": -3.41, "perf_1m": -5.2, "volume": 3000000},
        ]
        b1 = _build_1d_bucket(fake_stocks, date(2026, 11, 27), date(2026, 11, 26))
        self.assertNotIn("PKART", [r["symbol"] for r in b1["losers"]])
        self.assertEqual([r["symbol"] for r in b1["losers"]], ["ASELS"])

        b30 = _build_tv_1m_bucket(fake_stocks, date(2026, 11, 27), [])
        self.assertNotIn("PKART", [r["symbol"] for r in b30["losers"]])
        self.assertEqual([r["symbol"] for r in b30["losers"]], ["ASELS"])

    def test_turkish_company_name_cleanup(self):
        self.assertEqual(
            _clean_company_name("A1CAP", "A1 Capital Yatitim Menkul Degerler A.S."),
            "A1 CAPİTAL YATIRIM MENKUL DEĞERLER A.Ş.",
        )
        self.assertNotIn("Class B", _clean_company_name("FAKE99", "Fake Corp Class B"))

    def test_weekend_and_holiday_briefing_never_says_opening_in_30m(self):
        sat = datetime(2026, 11, 28, 10, 0, tzinfo=IST_TZ)  # Saturday
        self.assertEqual(get_market_status(sat), "weekend")
        self.assertFalse(is_bist_trading_day(sat.date()))
        self.assertTrue(is_bist_holiday(date(2026, 10, 29)))

        movers = {"breadth": {"advancers": 400, "decliners": 200, "total": 625}, "indices": {"XU100": {"price": 10977.0, "change_percent": 1.2}}}
        morning, evening = _build_fallback_briefings(
            movers,
            [
                "Bu hisse alım için uygun seviyede!",  # SPK violation -> must be filtered out!
                "TCMB zorunlu karşılık oranlarını güncelledi",
            ],
            sat,
        )
        self.assertNotIn("Açılışına 30 dk", morning["title"])
        self.assertIn("Hafta Sonu", morning["title"])
        self.assertNotIn("günü yükselişle", evening["bullets"][0])
        self.assertTrue(is_section_spk_compliant([morning["title"], morning["headline"], *morning["bullets"]]))
        self.assertTrue(is_section_spk_compliant([evening["title"], evening["headline"], *evening["bullets"]]))

    def test_spk_guardrail_catches_all_6_subtle_audit_leaks(self):
        # Innocent financial terms & disclaimer MUST pass
        self.assertTrue(
            is_spk_compliant(
                "Gram altın %0,82 yükselişle 5.854 TL oldu; satış hacmi ve pay geri alımı arttı. "
                "Yatırım tavsiyesi değildir (YTD)."
            )
        )
        # All 6 subtle advisory sentences flagged in external audit MUST fail
        for bad in (
            "Uzmanlar bu seviyelerin alım için uygun olduğunu belirtiyor.",
            "Hisse 450 TL hedefle takip ediliyor.",
            "Portföyünüzü çeşitlendirmeniz faydalı olur.",
            "Bu seviyelerden biriktirmeye başlayabilirsiniz.",
            "Destek seviyesi üzerinde kalındıkça yukarı yönlü hareket bekleniyor.",
            "Kâr realizasyonu yapmak isteyenler için seviye net.",
            "Bu hisse şu an çok ucuz, mutlaka alın.",
            "THYAO için hedef fiyat 450 TL olarak belirlendi.",
        ):
            self.assertFalse(is_spk_compliant(bad), f"SPK guardrail missed: {bad}")
            self.assertTrue(len(find_spk_violations(bad)) >= 1)

    def test_sanity_validators_reject_corrupt_data_and_preserve_good_file(self):
        # 1) Movers validator must reject -92.89% on 1G
        bad_movers = {
            "scanned_count": 625,
            "breadth": {"total": 625},
            "indices": {"XU100": {"price": 10000}, "USDTRY": {"price": 50}, "GRAM_ALTIN": {"price": 5900}},
            "periods": {
                k: {
                    "scanned_count": 620,
                    "gainers": [{"symbol": f"G{i}", "price": 10, "previous_close": 9.5, "change_percent": 5.0} for i in range(5)],
                    "losers": [{"symbol": "PKART" if (k == "1" and i == 0) else f"L{i}", "price": 2.64, "previous_close": 35.0, "change_percent": -92.89 if (k == "1" and i == 0) else -3.0} for i in range(5)],
                }
                for k in ("1", "10", "20", "30")
            },
        }
        self.assertTrue(any("PKART" in e for e in validate_movers_feed(bad_movers)))

        # 2) Daily briefing validator must reject empty breadth/indices & SPK violations
        bad_briefing = {
            "active_mode": "evening",
            "disclaimer": "Yatırım tavsiyesi değildir (YTD).",
            "breadth": {},
            "indices": {},
            "morning": {"id": "m1", "title": "Sabah", "headline": "Açılış", "bullets": ["B1", "B2"]},
            "evening": {"id": "e1", "title": "Akşam", "headline": "Kapanış", "bullets": ["B1", "Bu hisse alım için uygun."]},
        }
        b_errs = validate_daily_briefing(bad_briefing)
        self.assertTrue(any("breadth" in e for e in b_errs))
        self.assertTrue(any("SPK" in e for e in b_errs))

        # 3) Economy validator must reject 10 identical spam titles
        spam_econ = {
            "disclaimer": "YTD",
            "trending_clusters": [],
            "notification_candidates": [],
            "articles": [
                {"id": f"id{i}", "title": "CANLI ALTIN FİYATLARI", "url": f"https://x/{i}", "source": "X", "published_at": "2026-11-28T00:00:00Z"}
                for i in range(10)
            ],
        }
        self.assertTrue(any("benzersiz" in e for e in validate_economy_feed(spam_econ)))

        # 4) Atomic write preserves last known-good file on failure
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "daily_briefing.json"
            good_payload = {
                "active_mode": "evening",
                "disclaimer": "Yatırım tavsiyesi değildir (YTD).",
                "breadth": {"total": 625},
                "indices": {"XU100": {"price": 10977.0}},
                "morning": {"id": "m1", "title": "Sabah", "headline": "Açılış", "bullets": ["B1", "B2"]},
                "evening": {"id": "e1", "title": "Akşam", "headline": "Kapanış", "bullets": ["B1", "B2"]},
            }
            self.assertTrue(atomic_write_validated_json(target, good_payload, validate_daily_briefing))
            original = target.read_text(encoding="utf-8")
            self.assertFalse(atomic_write_validated_json(target, bad_briefing, validate_daily_briefing))
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_noise_split_and_session_helpers(self):
        self.assertTrue(is_noise("Galatasaray transfer bombasını patlattı", "SporX"))
        self.assertFalse(is_noise("Borsa İstanbul günü yükselişle tamamladı", "Ekonomim"))
        title, src = clean_title_and_source("Merkez Bankası rezervleri arttı - Ekonomim", "")
        self.assertEqual((title, src), ("Merkez Bankası rezervleri arttı", "Ekonomim"))
        t0 = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
        self.assertTrue(_has_unadjusted_split_jump([(date(2026, 11, 20), t0, 100.0), (date(2026, 11, 23), t0 + timedelta(days=3), 12.5)], date(2026, 11, 20)))
        self.assertTrue(_is_bist_session_active(datetime(2026, 11, 26, 11, 0, tzinfo=IST_TZ)))
        self.assertFalse(_is_bist_session_active(datetime(2026, 11, 28, 12, 0, tzinfo=IST_TZ)))
        self.assertEqual(_fmt_pct(2.345), "+%2.35")


if __name__ == "__main__":
    unittest.main()
