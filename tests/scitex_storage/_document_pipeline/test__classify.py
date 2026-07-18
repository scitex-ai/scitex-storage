"""Unit tests for the deterministic classifier (pure functions on strings).

NO MOCKS (PA-306): every function here is pure, so each case is a plain
string in / value out -- no PDF, no disk, no env. The Japanese cases live
here (not in the PDF fixture, which cannot carry CJK glyphs). ONE assertion
per test (PA-307), AAA-structured.
"""

from scitex_storage._document_pipeline._classify import (
    Classification,
    classify,
    detect_keep_original,
    extract_date,
    extract_issuer,
    slugify,
)

# --------------------------------------------------------------------------
# extract_date
# --------------------------------------------------------------------------


def test_extract_date_iso_dash():
    # Arrange
    text = "Date: 2026-07-18 due"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2026-07-18"


def test_extract_date_iso_slash():
    # Arrange
    text = "2026/7/18"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2026-07-18"


def test_extract_date_reiwa():
    # Arrange
    text = "令和6年7月18日"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2024-07-18"


def test_extract_date_reiwa_gannen():
    # Arrange
    text = "令和元年5月1日"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2019-05-01"


def test_extract_date_heisei():
    # Arrange
    text = "平成31年4月30日"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2019-04-30"


def test_extract_date_western_kanji():
    # Arrange
    text = "2026年7月18日"
    # Act
    result = extract_date(text)
    # Assert
    assert result == "2026-07-18"


def test_extract_date_none_when_absent():
    # Arrange
    text = "no date here"
    # Act
    result = extract_date(text)
    # Assert
    assert result is None


def test_extract_date_skips_implausible_month():
    # Arrange
    text = "2026-13-01"
    # Act
    result = extract_date(text)
    # Assert
    assert result is None


# --------------------------------------------------------------------------
# classify -- category
# --------------------------------------------------------------------------


def test_classify_finance_japanese():
    # Arrange
    text = "請求書 振込 令和6年7月18日"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "finance"


def test_classify_finance_english():
    # Arrange
    text = "INVOICE payment total"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "finance"


def test_classify_medical_japanese():
    # Arrange
    text = "診断 病院 処方"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "medical"


def test_classify_contracts_japanese():
    # Arrange
    text = "契約書 同意書 覚書"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "contracts"


def test_classify_academic_english():
    # Arrange
    text = "journal abstract conference research"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "academic"


def test_classify_empty_text_is_misc():
    # Arrange
    text = ""
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "misc"


def test_classify_unmatched_is_misc():
    # Arrange
    text = "こんにちは、いい天気ですね"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "misc"


def test_classify_single_weak_hit_is_misc():
    # Arrange -- 1 keyword, no date -> confidence below threshold.
    text = "manual"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.category == "misc"


def test_classify_respects_configured_categories():
    # Arrange -- finance excluded from the allowed set.
    text = "請求書 振込"
    # Act
    verdict = classify(text, categories=["misc", "medical"])
    # Assert
    assert verdict.category == "misc"


# --------------------------------------------------------------------------
# classify -- other fields
# --------------------------------------------------------------------------


def test_classify_sets_date():
    # Arrange
    text = "INVOICE 2026-07-18 payment"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.date == "2026-07-18"


def test_classify_sets_issuer():
    # Arrange
    text = "東京電力株式会社 請求書 振込"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.issuer != "unknown"


def test_classify_issuer_unknown_when_absent():
    # Arrange
    text = "just some text"
    # Act
    verdict = classify(text)
    # Assert
    assert verdict.issuer == "unknown"


def test_classify_confidence_in_unit_range():
    # Arrange
    text = "INVOICE payment total 2026-07-18"
    # Act
    conf = classify(text).confidence
    # Assert
    assert 0.0 <= conf <= 1.0


def test_classify_returns_classification():
    # Arrange
    text = "hello"
    # Act
    verdict = classify(text)
    # Assert
    assert isinstance(verdict, Classification)


def test_classify_more_hits_higher_confidence():
    # Arrange
    one = classify("契約").confidence
    # Act
    many = classify("契約書 同意書 覚書").confidence
    # Assert
    assert many > one


# --------------------------------------------------------------------------
# extract_issuer
# --------------------------------------------------------------------------


def test_extract_issuer_finds_kabushiki_kaisha():
    # Arrange
    text = "東京電力株式会社\n請求書"
    # Act
    issuer = extract_issuer(text)
    # Assert
    assert issuer != "unknown"


def test_extract_issuer_english_inc():
    # Arrange
    text = "Acme Widgets Inc.\nInvoice"
    # Act
    issuer = extract_issuer(text)
    # Assert
    assert issuer != "unknown"


# --------------------------------------------------------------------------
# detect_keep_original
# --------------------------------------------------------------------------


def test_detect_keep_original_passport_japanese():
    # Arrange
    text = "旅券番号 A1234"
    # Act
    result = detect_keep_original(text, ["passport"])
    # Assert
    assert result == "passport"


def test_detect_keep_original_passport_english():
    # Arrange
    text = "PASSPORT"
    # Act
    result = detect_keep_original(text, ["passport"])
    # Assert
    assert result == "passport"


def test_detect_keep_original_mynumber():
    # Arrange
    text = "マイナンバーカード"
    # Act
    result = detect_keep_original(text, ["mynumber_card"])
    # Assert
    assert result == "mynumber_card"


def test_detect_keep_original_none_when_type_not_listed():
    # Arrange
    text = "旅券"
    # Act
    result = detect_keep_original(text, [])
    # Assert
    assert result is None


def test_detect_keep_original_none_when_absent():
    # Arrange
    text = "ただの請求書です"
    # Act
    result = detect_keep_original(text, ["passport"])
    # Assert
    assert result is None


# --------------------------------------------------------------------------
# slugify
# --------------------------------------------------------------------------


def test_slugify_replaces_spaces_with_dash():
    # Arrange
    value = "Tokyo Electric"
    # Act
    result = slugify(value)
    # Assert
    assert result == "tokyo-electric"


def test_slugify_lowercases_ascii():
    # Arrange
    value = "INVOICE"
    # Act
    result = slugify(value)
    # Assert
    assert result == "invoice"


def test_slugify_keeps_japanese():
    # Arrange
    value = "東京電力"
    # Act
    result = slugify(value)
    # Assert
    assert result == "東京電力"


def test_slugify_empty_returns_empty():
    # Arrange
    value = ""
    # Act
    result = slugify(value)
    # Assert
    assert result == ""


def test_slugify_caps_length():
    # Arrange
    value = "a" * 100
    # Act
    result = slugify(value, max_len=10)
    # Assert
    assert len(result) <= 10


def test_slugify_strips_path_separators():
    # Arrange
    value = "finance/2026/report"
    # Act
    result = slugify(value)
    # Assert
    assert "/" not in result
