import pytest

from app.plate_fmt import normalize_manual, normalize_plate, normalize_query


@pytest.mark.parametrize("raw, expected", [
    ("A123BC77", "А123ВС77"),      # латиница -> кириллица
    ("А123ВС77", "А123ВС77"),      # уже кириллица
    ("a123bc777", "А123ВС777"),    # нижний регистр, 3-значный регион
    ("A 123 BC 77", "А123ВС77"),   # пробелы
    ("A-123-BC|77", "А123ВС77"),   # мусорные разделители
    ("А12ЗВС77", "А123ВС77"),      # З на месте цифры -> 3
    ("A12OBC77", "А120ВС77"),      # O на месте цифры -> 0
    ("0123BC77", "О123ВС77"),      # 0 на месте буквы -> О
    ("A123B077", "А123ВО77"),      # 0 на месте буквы -> О
    ("A123BC7O", "А123ВС70"),      # O в регионе -> 0
    ("8123BC77", "В123ВС77"),      # 8 на месте буквы -> В
    ("A1B3BC77", "А183ВС77"),      # B на месте цифры -> 8
    ("A123BC77RUS", "А123ВС77"),   # хвост RUS
])
def test_normalize_plate_valid(raw, expected):
    assert normalize_plate(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "A123BC7",       # мало символов
    "A123BC7777",    # много символов
    "Z123BC77",      # Z не бывает на номерах
    "A123ZC77",
    "Д123ВС77",      # Д не бывает на номерах
    "ABCDEFGH",
    "A1234C77X",
])
def test_normalize_plate_invalid(raw):
    assert normalize_plate(raw) is None


@pytest.mark.parametrize("q, expected", [
    ("a123", "А123"),
    ("вс 77", "ВС77"),
    ("bc77", "ВС77"),
    ("", ""),
])
def test_normalize_query(q, expected):
    assert normalize_query(q) == expected


def test_normalize_manual():
    assert normalize_manual("ab 1234 77") == "АВ123477"   # формат не проверяется
    assert normalize_manual("   ") is None
    assert normalize_manual("X" * 13) is None
