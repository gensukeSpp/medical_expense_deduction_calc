"""
Simple LLM extractor wrapper with a Mock client for local testing.
- MockLLMClient.extract_fields(ocr_json) returns a best-effort extraction using heuristics.
- Real client placeholder (not implemented) should follow same interface.
"""

from __future__ import annotations
import re
import unicodedata
from typing import Dict, Any, Optional, List
from app.normalization import parse_amount, parse_date


def normalize_lines(ocr_json: Dict[str, Any]) -> List[str]:
    # Handle list format from ocr_pipeline: [{text, confidence, box}, ...]
    if isinstance(ocr_json, list):
        return [unicodedata.normalize("NFKC", str(entry.get("text", ""))) for entry in ocr_json if entry.get("text")]

    lines = ocr_json.get("text_lines") or []
    if not lines and "words" in ocr_json:
        lines = [w.get("text", "") for w in ocr_json.get("words", [])]
    return [unicodedata.normalize("NFKC", line) for line in lines]


def extract_name_from_lines(lines: List[str]) -> Optional[str]:
    # name heuristic: look for '様' or lines starting with 患者
    # Prefer line-wise extraction to avoid greedy cross-line matches
    for line in lines:
        if "様" in line:
            # take text before 様
            name = line.split("様")[0].strip()
            return name
        if line.startswith("氏名") or "氏名:" in line or "氏名：" in line:
            m2 = re.search(r"氏名[:：]?\s*(.+)", line)
            if m2:
                candidate = m2.group(1).strip()
                candidate = candidate.replace("様", "").strip()
                if 1 < len(candidate) <= 40:
                    return candidate
        if line.startswith("患者") or "患者:" in line or "患者：" in line:
            m2 = re.search(r"患者[:：]?\s*(.+)", line)
            if m2:
                candidate = m2.group(1).strip()
                candidate = candidate.replace("様", "").strip()
                if 1 < len(candidate) <= 40:
                    return candidate
    # fallback: look for lines that look like a personal name (two or three kanji/space)
    for line in lines:
        if re.search(r"^[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\s]{2,40}$", line.strip()):
            return line.strip()
    return None


def extract_clinic_from_lines(lines: List[str]) -> Optional[str]:
    # clinic heuristic: look for lines containing クリニック|医院|診療所|薬局|調剤
    for line in lines:
        if re.search(r"クリニック|医院|診療所|薬局|調剤|clinic", line):
            return line.strip()
    return None


def extract_amount_from_text(text: str) -> Optional[int]:
    # amount heuristic: numbers with yen or 万 (simple)
    # try common patterns
    return parse_amount(text)


def extract_date_from_text(text: str) -> Optional[str]:
    # date heuristic: YYYY/MM/DD or YYYY年M月D日 or YY/MM/DD or M/D/YY
    return parse_date(text)


class MockLLMClient:
    """A deterministic, local heuristic-based extractor used for tests and offline runs."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or "mock"

    def extract_fields(self, ocr_json: Dict[str, Any]) -> Dict[str, Any]:
        lines = normalize_lines(ocr_json)
        text = "\n".join(lines)
        name = extract_name_from_lines(lines)
        clinic = extract_clinic_from_lines(lines)
        amount = extract_amount_from_text(text)
        date = extract_date_from_text(text)
        return {"name": name, "clinic": clinic, "amount": amount, "date": date}


# Placeholder for a real LLM client interface
class RealLLMClient:
    def __init__(self, model_name: str, api_key: Optional[str] = None):
        self.model_name = model_name
        self.api_key = api_key

    def extract_fields(self, ocr_json: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("Real client not implemented in this branch")


def get_llm_client(model_name: str):
    """Factory function to get LLM client based on model name."""
    if model_name == "mock":
        return MockLLMClient()
    return RealLLMClient(model_name)
