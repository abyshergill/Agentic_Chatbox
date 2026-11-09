"""Decodes text files whatever encoding they were saved in.

CSV and text files exported from Excel, older Windows tools, or other countries are often not UTF-8: Excel's
"Unicode Text" is UTF-16, and "CSV" on Windows is usually Windows-1252. Reading those as UTF-8 either fails or turns
characters like é, £ and ü into garbage, so every text reader goes through decode() instead.
"""

from __future__ import annotations

import codecs

BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),  # Checked before UTF-16: its BOM starts with the UTF-16 LE BOM.
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
FALLBACKS = ("utf-8", "cp1252", "latin-1")  # latin-1 accepts any byte, so decoding always succeeds.


def detect(data: bytes, partial: bool = False) -> str:
    """The encoding of `data`: from its byte-order mark, a UTF-16 pattern, UTF-8 if valid, else Windows-1252.

    partial=True means `data` is only the start of a file, so a character cut off at the end is not an error.
    """
    for bom, name in BOMS:
        if data.startswith(bom):
            return name
    sample = data[:4096]
    if len(sample) >= 4:
        # UTF-16 without a BOM: plain ASCII text has a zero byte in every other position.
        even_zeros = sample[0::2].count(0)
        odd_zeros = sample[1::2].count(0)
        half = len(sample) // 2
        if odd_zeros > half * 0.6 and even_zeros < half * 0.1:
            return "utf-16-le"
        if even_zeros > half * 0.6 and odd_zeros < half * 0.1:
            return "utf-16-be"
    for name in FALLBACKS:
        try:
            data.decode(name)
            return name
        except UnicodeDecodeError as exc:
            if partial and name == "utf-8" and exc.start >= len(data) - 3 and exc.reason == "unexpected end of data":
                return "utf-8"  # A multibyte character cut off at the end of a sample.
    return "latin-1"


def is_utf16(encoding: str) -> bool:
    return encoding.startswith(("utf-16", "utf-32"))


def decode(data: bytes, partial: bool = False) -> str:
    """Decodes bytes to text, never raising. A leading byte-order mark is removed."""
    encoding = detect(data, partial)
    text = data.decode(encoding, errors="replace")
    return text[1:] if text.startswith("﻿") else text


def looks_binary(data: bytes) -> bool:
    """True for bytes that are not text in any encoding decode() understands."""
    if not data:
        return False
    if is_utf16(detect(data, partial=True)):
        return False
    return b"\x00" in data[:8192]
