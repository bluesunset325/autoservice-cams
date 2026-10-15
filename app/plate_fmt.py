"""Нормализация госномеров РФ формата А123ВС77(7)."""
import re

LETTERS = "АВЕКМНОРСТУХ"
_LATIN_TO_CYRILLIC = str.maketrans("ABEKMHOPCTYX", LETTERS)
_LETTER_POSITIONS = {0, 4, 5}
# Спорные символы: что подставлять на месте цифры и на месте буквы.
_TO_DIGIT = {
    "О": "0", "D": "0", "Q": "0", "В": "8", "З": "3", "Б": "6", "G": "6",
    "Т": "7", "А": "4", "Ч": "4", "S": "5", "I": "1", "L": "1", "Z": "2",
}
_TO_LETTER = {"0": "О", "D": "О", "Q": "О", "8": "В", "4": "А", "7": "Т"}
PLATE_RE = re.compile(rf"^[{LETTERS}]\d{{3}}[{LETTERS}]{{2}}\d{{2,3}}$")
_NOT_ALNUM_RE = re.compile(r"[^0-9A-ZА-ЯЁ]")
MAX_MANUAL_LEN = 12


def _prepare(raw: str) -> str:
    return _NOT_ALNUM_RE.sub("", raw.upper()).translate(_LATIN_TO_CYRILLIC)


def normalize_plate(raw: str) -> str | None:
    s = _NOT_ALNUM_RE.sub("", raw.upper())
    if s.endswith("RUS"):
        s = s[:-3]
    s = s.translate(_LATIN_TO_CYRILLIC)
    if len(s) not in (8, 9):
        return None
    fixed = "".join(
        _TO_LETTER.get(ch, ch) if i in _LETTER_POSITIONS else _TO_DIGIT.get(ch, ch)
        for i, ch in enumerate(s)
    )
    return fixed if PLATE_RE.match(fixed) else None


def normalize_query(q: str) -> str:
    return _prepare(q)


def normalize_manual(raw: str) -> str | None:
    s = _prepare(raw)
    if not s or len(s) > MAX_MANUAL_LEN:
        return None
    return s
