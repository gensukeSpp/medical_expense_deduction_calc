import sys
from pathlib import Path

# Ensure project root is on sys.path for tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm_extractor import MockLLMClient
import json


def load_sample(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_mock_extractor_normal():
    client = MockLLMClient()
    ocr = load_sample("tasks/issue_4/ocr_sample_normal.json")
    out = client.extract_fields(ocr)
    assert out["clinic"] is not None
    assert out["name"] in ("山田 太郎", "山田 太郎 ", "山田 太郎")
    assert out["amount"] == 3800
    assert out["date"] == "2026-01-15"


def test_mock_extractor_missing_labels():
    client = MockLLMClient()
    ocr = load_sample("tasks/issue_4/ocr_sample_missing_labels.json")
    out = client.extract_fields(ocr)
    assert out["clinic"] == "青葉薬局"
    assert out["amount"] == 4200
    assert out["date"] == "2026-01-10"


def test_mock_extractor_noisy():
    client = MockLLMClient()
    ocr = load_sample("tasks/issue_4/ocr_sample_noisy.json")
    out = client.extract_fields(ocr)
    assert out["amount"] == 12000
    assert out["name"] in ("山田太郎", "山田 太郎", "山田太郎")


def test_extract_name_uses_shimei_prefix():
    """氏名: / 氏名： のプレフィックスから名前を抽出する"""
    from app.llm_extractor import extract_name_from_lines

    assert extract_name_from_lines(["氏名: 山田太郎", "金額: 500円"]) == "山田太郎"
    assert extract_name_from_lines(["氏名：鈴木花子", "500円"]) == "鈴木花子"


def test_extract_name_keeps_kanja_fallback():
    """患者: プレフィックスは下位互換として残す（test_mock_extractor_noisy が依存）"""
    from app.llm_extractor import extract_name_from_lines

    assert extract_name_from_lines(["患者: 山田太郎", "500円"]) == "山田太郎"


def test_extract_name_still_uses_sama_branch():
    """様 サフィックス branch は変更しない（既存動作の維持）"""
    from app.llm_extractor import extract_name_from_lines

    assert extract_name_from_lines(["山田太郎様", "500円"]) == "山田太郎"
