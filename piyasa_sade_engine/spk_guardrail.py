"""
Piyasa Sade — SPK & Mevzuat Sıfır Toleranslı Guardrail Katmanı (v2)
- Gemini AI çıktılarında, küme özetlerinde ve ham haber başlıklarında doğrudan VEYA
  dolaylı yatırım tavsiyesi / yönlendirme sayılabilecek ifadeleri Türkçe tam kelime
  sınırlarıyla tespit eder.
- "altın", "satış hacmi", "pay geri alımı", "tutarı" gibi masum finansal terimlerde
  yanlış pozitif üretmez.
- Sıfır tolerans: İhlal yakalanırsa ilgili AI çıktısı tamamen reddedilir.
"""
from __future__ import annotations

import re
from typing import List

_DISCLAIMER_STRIP = re.compile(
    r"yatırım\s+tavsiyesi\s+değildir|\(ytd\)",
    re.IGNORECASE,
)

_WB_START = r"(?<![a-zçğıöşü0-9])"
_WB_END = r"(?![a-zçğıöşü0-9])"

FORBIDDEN_SPK_PATTERNS = [
    # Doğrudan al / sat / tut emirleri ve önerileri
    r"al",
    r"sat",
    r"tut",
    r"alın",
    r"satın",
    r"tutun",
    r"alınız",
    r"satınız",
    r"almalı",
    r"satmalı",
    r"tutmalı",
    r"alınmalı",
    r"satılmalı",
    r"alım\s+yapın",
    r"satım\s+yapın",
    r"alım\s+zamanı",
    r"satım\s+zamanı",
    r"portföye\s+ekle(?:yin|mek\s+gerekir)?",
    r"portföyden\s+çıkar(?:ın)?",
    r"portföyünüzü\s+çeşitlendir\w*",
    r"çeşitlendirmeniz\s+faydalı",
    r"biriktirmeye\s+başla\w*",
    r"kademeli\s+topla\w*",
    r"kademeli\s+alım",
    r"biriktirin",
    # Dolaylı yönlendirme & teknik hedef / kâr realizasyonu dili
    r"alım\s+için\s+uygun",
    r"satım\s+için\s+uygun",
    r"satış\s+için\s+uygun",
    r"uygun\s+alım\s+seviye\w*",
    r"kâr\s+realizasyonu\s+yapmak\s+isteyen\w*",
    r"kar\s+realizasyonu\s+yapmak\s+isteyen\w*",
    r"kâr\s+realizasyonu\s+fırsatı",
    r"hedef\s+fiyat",
    r"fiyat\s+hedefi",
    r"\d+(?:[.,]\d+)?\s*(?:tl|₺|usd|\$)\s+hedef\w*",
    r"yukarı\s+yönlü\s+hareket\s+bekleniyor",
    r"aşağı\s+yönlü\s+hareket\s+bekleniyor",
    r"yükseliş\s+bekleniyor",
    # Yönlendirici değerleme ve fırsat ifadeleri
    r"ucuz",
    r"pahalı",
    r"alım\s+fırsatı",
    r"satım\s+fırsatı",
    r"satış\s+fırsatı",
    r"kaçırmayın",
    r"kaçırılmayacak",
    r"güçlü\s+al",
    r"güçlü\s+sat",
    r"al\s+sinyali",
    r"sat\s+sinyali",
    r"alım\s+sinyali",
    r"satım\s+sinyali",
    r"al\s+önerisi",
    r"sat\s+önerisi",
    r"tut\s+önerisi",
    r"alım\s+önerisi",
    r"satım\s+önerisi",
    r"yatırım\s+tavsiyesi",
    r"tavsiye\s+edilir",
    r"tavsiye\s+ediyoruz",
    r"öneriyoruz",
    # Kesin getiri / garanti vaatleri
    r"kesin\s+yükselir",
    r"kesin\s+düşer",
    r"garanti\s+kazanç",
    r"garanti\s+getiri",
    r"mutlaka\s+yükselir",
    r"uçacak",
    r"patlayacak",
    r"bedava\s+fiyat",
]

SPK_FORBIDDEN_REGEX = re.compile(
    _WB_START + r"(" + "|".join(FORBIDDEN_SPK_PATTERNS) + r")" + _WB_END,
    re.IGNORECASE,
)


def find_spk_violations(text: str) -> List[str]:
    """Metindeki SPK mevzuatına aykırı yönlendirici ifadeleri döndürür."""
    if not text or not text.strip():
        return []
    cleaned = _DISCLAIMER_STRIP.sub("", text)
    matches = [m.group(0) for m in SPK_FORBIDDEN_REGEX.finditer(cleaned)]
    seen: set[str] = set()
    unique: List[str] = []
    for m in matches:
        key = m.lower()
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


def is_spk_compliant(text: str) -> bool:
    """Metin SPK guardrail denetiminden sıfır ihlalle geçiyorsa True döner."""
    return len(find_spk_violations(text)) == 0


def is_section_spk_compliant(texts: List[str]) -> bool:
    """Bir bülten veya küme bölümündeki tüm alanların SPK uyumlu olduğunu doğrular."""
    for t in texts:
        if t and not is_spk_compliant(t):
            return False
    return True
