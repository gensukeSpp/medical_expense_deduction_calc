from app.normalization import parse_amount, parse_date


def test_parse_amount_numeric():
    assert parse_amount("3,800円") == 3800
    assert parse_amount("４，５００円") == 4500


def test_parse_amount_kanji():
    assert parse_amount("一万二千円") == 12000
    assert parse_amount("二千円") == 2000


def test_parse_amount_plain_number():
    """テスト: プレーンな数字文字列が正しくパースされる"""
    assert parse_amount("3800") == 3800
    assert parse_amount("3,800") == 3800
    assert parse_amount("12345") == 12345
    assert parse_amount("0") == 0


def test_parse_amount_empty_string():
    """テスト: 空文字列は None を返す"""
    assert parse_amount("") is None
    assert parse_amount("   ") is None


def test_parse_amount_non_numeric_string():
    """テスト: 非数字文字列は None を返す（既存動作の維持）"""
    assert parse_amount("abc") is None
    assert parse_amount("3800円abc") == 3800  # 純粋な数字のマッチを許容
    assert parse_amount("3800円と500円") == 3800  # 複数の金額表現があった場合は最初の数字のみ


def test_parse_amount_existing_formats_unchanged():
    """テスト: 既存の日本語形式の動作が変わらない"""
    assert parse_amount("3800円") == 3800
    assert parse_amount("2000") == 2000  # プレーン数字
    assert parse_amount("一万二千円") == 12000


def test_parse_date_standard():
    """テスト: 標準的な日付形式が正しくパースされる"""
    assert parse_date("2026/01/15") == "2026-01-15"
    assert parse_date("2026-01-15") == "2026-01-15"
    assert parse_date("2026年1月15日") == "2026-01-15"
    assert parse_date("1/15/2026") == "2026-01-15"


def test_parse_date_reiwa_long():
    """テスト: 令和N年M月D日 形式がパースされる"""
    assert parse_date("令和6年3月15日") == "2024-03-15"
    assert parse_date("令和元年3月15日") == "2019-03-15"
    assert parse_date("令和7年1月1日") == "2025-01-01"


def test_parse_date_reiwa_short():
    """テスト: 令和短縮形 (R, 令) がパースされる"""
    assert parse_date("R6.3.15") == "2024-03-15"
    assert parse_date("R6/3/15") == "2024-03-15"
    assert parse_date("令6.3.15") == "2024-03-15"
    assert parse_date("令6/3/15") == "2024-03-15"


def test_parse_date_reiwa_gannen():
    """テスト: 令和元年のパース"""
    assert parse_date("R1.5.1") == "2019-05-01"
    assert parse_date("令和元年5月1日") == "2019-05-01"


def test_parse_date_empty():
    """テスト: 空文字列は None を返す"""
    assert parse_date("") is None
    assert parse_date(None) is None


def test_parse_amount_yen_prefix():
    """金額の先頭にある ¥ / ￥ を許容し、既存の日本語表記は維持する"""
    assert parse_amount("¥3800") == 3800
    assert parse_amount("￥3,800") == 3800
    assert parse_amount("¥3,800円") == 3800
    assert parse_amount("¥1万") == 10000
    assert parse_amount("¥-500") is None  # 負値は記号除去より前に拒否
