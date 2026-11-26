import unittest
from datetime import date, datetime, timedelta, timezone

from piyasa_sade_engine.daily_briefing_generator import _fmt_pct
from piyasa_sade_engine.movers_aggregator import (
    IST_TZ,
    _has_unadjusted_split_jump,
    _is_bist_session_active,
)
from piyasa_sade_engine.news_aggregator import (
    clean_title_and_source,
    is_breaking_headline,
    is_noise,
)


class EngineAuditTests(unittest.TestCase):
    def test_breaking_headline_avoids_substring_false_positives(self):
        # False-positive traps flagged in audit ("kap", "fon", "ons", "rekor")
        self.assertFalse(is_breaking_headline("Fabrikada kapasite kullanım oranı açıklandı"))
        self.assertFalse(is_breaking_headline("Yeni vergi paketi kapsam içine alındı"))
        self.assertFalse(is_breaking_headline("Akıllı telefon satışlarında artış yaşandı"))
        self.assertFalse(is_breaking_headline("Şirket bilançosunda konsolidasyon tamamlandı"))
        self.assertFalse(is_breaking_headline("İhracatta rekor beklentisi konuşuluyor"))
        self.assertFalse(is_breaking_headline("Fed faiz kararı ne zaman açıklanacak? Saat kaçta?"))
        self.assertFalse(is_breaking_headline("TCMB'nin faiz indirimi planı değişebilir"))

        # True positives with Turkish word boundaries
        self.assertTrue(is_breaking_headline("THYAO'dan KAP bildirimi: Yeni uçak siparişi"))
        self.assertTrue(is_breaking_headline("KAP'a yapılan açıklamada temettü tarihi duyuruldu"))
        self.assertTrue(is_breaking_headline("Son dakika: TCMB faiz kararı açıklandı"))
        self.assertTrue(is_breaking_headline("SPK bülteninde 3 şirkete sermaye artırımı onayı"))
        self.assertTrue(is_breaking_headline("BIST 100 endeksinde devre kesici uygulandı"))
        self.assertTrue(is_breaking_headline("Gram altın rekor kırdı"))

    def test_noise_filter_blocks_sports_and_tabloids(self):
        self.assertTrue(is_noise("Galatasaray transfer bombasını patlattı", "SporX"))
        self.assertTrue(is_noise("Survivor adasında eleme heyecanı", "Magazin"))
        self.assertFalse(is_noise("Borsa İstanbul günü yükselişle tamamladı", "Ekonomim"))

    def test_clean_title_and_source(self):
        title, src = clean_title_and_source("Merkez Bankası rezervleri arttı - Ekonomim", "")
        self.assertEqual(title, "Merkez Bankası rezervleri arttı")
        self.assertEqual(src, "Ekonomim")

    def test_unadjusted_split_jump_guard(self):
        t0 = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
        normal_pts = [
            (date(2026, 11, 20), t0, 100.0),
            (date(2026, 11, 23), t0 + timedelta(days=3), 109.5),  # +9.5% BIST ceiling
            (date(2026, 11, 24), t0 + timedelta(days=4), 118.0),
        ]
        self.assertFalse(_has_unadjusted_split_jump(normal_pts, date(2026, 11, 20)))

        split_pts = [
            (date(2026, 11, 20), t0, 100.0),
            (date(2026, 11, 23), t0 + timedelta(days=3), 12.5),  # -87.5% unadjusted split
        ]
        self.assertTrue(_has_unadjusted_split_jump(split_pts, date(2026, 11, 20)))

    def test_bist_session_active_hours(self):
        # Weekday 11:00 TR -> active
        self.assertTrue(_is_bist_session_active(datetime(2026, 11, 26, 11, 0, tzinfo=IST_TZ)))
        # Weekday 22:30 TR -> closed
        self.assertFalse(_is_bist_session_active(datetime(2026, 11, 26, 22, 30, tzinfo=IST_TZ)))
        # Saturday 12:00 TR -> closed
        self.assertFalse(_is_bist_session_active(datetime(2026, 11, 28, 12, 0, tzinfo=IST_TZ)))

    def test_fmt_pct(self):
        self.assertEqual(_fmt_pct(2.345), "+%2.35")
        self.assertEqual(_fmt_pct(-1.2), "-%1.20")
        self.assertEqual(_fmt_pct(None), "-")


if __name__ == "__main__":
    unittest.main()
