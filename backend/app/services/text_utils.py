"""Pure text helpers used by the ETL and the agent (no I/O, fully unit-testable)."""
import re
import unicodedata
from typing import Optional

import pandas as pd

# Byte sequences that betray UTF-8 text that was decoded as Latin-1/CP1252 ("mojibake").
_MOJIBAKE_MARKERS = ("Ã", "Â", "â€")

_TRIAGE_LEVEL_RE = re.compile(r"TRIAGE\s*[-:]?\s*(V|IV|I{1,3}|[1-5])\b", re.IGNORECASE)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def fix_mojibake(value: object) -> object:
    """Repairs double-encoded UTF-8 (e.g. 'POPAYÃ\\x81N' -> 'POPAYÁN').

    Each character is mapped back to the byte it came from: code points < 256 are
    Latin-1 bytes; higher ones (like '€' or '“') come from CP1252. The byte string
    is then decoded as UTF-8. If anything fails, the original value is returned,
    so already-correct text is never damaged.
    """
    if not isinstance(value, str) or not any(m in value for m in _MOJIBAKE_MARKERS):
        return value
    try:
        raw = bytearray()
        for ch in value:
            code = ord(ch)
            if code < 256:
                raw.append(code)
            else:
                raw.extend(ch.encode("cp1252"))
        return raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def fix_mojibake_series(series: pd.Series) -> pd.Series:
    """Vectorised version: fixes each distinct value once and maps it back."""
    if series.empty:
        return series
    uniques = series.dropna().unique()
    mapping = {u: fix_mojibake(u) for u in uniques}
    changed = {k: v for k, v in mapping.items() if k != v}
    if not changed:
        return series
    return series.map(lambda v: changed.get(v, v) if isinstance(v, str) else v)


def triage_level(classification: object) -> Optional[int]:
    """'PEDIATRIA EXTENDIDO- TRIAGE 4 ( GRIS)' -> 4. CodigoTriage is NOT the level."""
    if not isinstance(classification, str):
        return None
    match = _TRIAGE_LEVEL_RE.search(classification)
    if not match:
        return None
    token = match.group(1).upper()
    return int(token) if token.isdigit() else _ROMAN.get(token)


def normalize_text(text: str) -> str:
    """Lower-case and strip accents: 'Pediatría' -> 'pediatria'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


# ICD-10 chapter keys (broad categories, safe to show; the specific code is not).
def icd10_chapter(code: object) -> Optional[str]:
    if not isinstance(code, str) or not code:
        return None
    letter = code[0].upper()
    digits = re.sub(r"\D", "", code[1:3])
    number = int(digits) if digits else 0
    if letter in "AB":
        return "infectious"
    if letter == "C" or (letter == "D" and number < 50):
        return "neoplasms"
    if letter == "D":
        return "blood"
    if letter == "H":
        return "eye" if number < 60 else "ear"
    return {
        "E": "endocrine", "F": "mental", "G": "nervous", "I": "circulatory",
        "J": "respiratory", "K": "digestive", "L": "skin", "M": "musculoskeletal",
        "N": "genitourinary", "O": "pregnancy", "P": "perinatal", "Q": "congenital",
        "R": "symptoms", "S": "injury", "T": "injury", "V": "external", "W": "external",
        "X": "external", "Y": "external", "Z": "health_factors", "U": "special",
    }.get(letter)


def age_group(age: object) -> Optional[str]:
    if age is None or (isinstance(age, float) and pd.isna(age)):
        return None
    age = float(age)
    if age < 1:
        return "0-1"
    if age < 5:
        return "1-4"
    if age < 15:
        return "5-14"
    if age < 30:
        return "15-29"
    if age < 45:
        return "30-44"
    if age < 60:
        return "45-59"
    if age < 75:
        return "60-74"
    return "75+"
