"""Verify date-anchor re-normalization + coordinate extraction against real data.

Issue #47: a read-only verification CLI that replays the production
date-basis path (candidate search -> resolution -> anchor re-normalization
-> coordinate extraction) using the app's own implementations, so reviewers
can confirm on real raw JSON + a date-basis template how a candidate was
chosen and what fields were extracted.

The tool operates ONLY on working copies (--raw/--db are copied to a temp
dir) and never calls process_input_json / insert_receipt, so originals and
the receipts table are never modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from app.coord_search import search_fields_by_proximity
from app.coord_normalizer import normalize_coordinates_by_anchor
from app.date_anchor import find_date_candidates, resolve_date_anchor
from app.structural_parser import DEFAULT_PROXIMITY_THRESHOLD

# 検証終了コード
EXIT_OK = 0  # 検証完了（anchor 解決 -> 抽出）
EXIT_UNRESOLVED = 1  # 候補が同点で解決不能（安全側フォールバック）
EXIT_PRECOND = 2  # 前提不成立 or 入力欠落


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _entries(ocr_json: Any) -> list[Dict[str, Any]]:
    """Normalize various raw_data shapes to a list of entry dicts."""
    if isinstance(ocr_json, list):
        return [e for e in ocr_json if isinstance(e, dict)]
    if isinstance(ocr_json, dict):
        for key in ("words", "ocr_entries", "ocr_json", "data"):
            val = ocr_json.get(key)
            if isinstance(val, list):
                return [e for e in val if isinstance(e, dict)]
    return []


def _clinic_id_by_name(db_path: Path, name: str) -> Optional[str]:
    """Look up a clinic id by name (read-only; never INSERT)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT id FROM clinics WHERE name = ? ORDER BY rowid LIMIT 1",
            (name.strip(),),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        con.close()


def _load_template(db_path: Path, clinic_id: str) -> Optional[Dict[str, Any]]:
    from app.db import get_latest_template_by_clinic

    return get_latest_template_by_clinic(db_path, clinic_id)


def verify_date_anchor(
    raw_path: Path,
    structured_path: Path,
    db_path: Optional[Path],
    threshold: float = DEFAULT_PROXIMITY_THRESHOLD,
    workdir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run and report each stage of date-anchor verification on working copies.

    Returns a dict with the stage results (and does not mutate the originals).
    """
    # --- 入力の存在確認 ---
    for p, label in ((raw_path, "raw"), (structured_path, "structured")):
        if not Path(p).exists():
            raise FileNotFoundError(f"{label} ファイルが存在しません: {p}")
    if db_path is not None and not Path(db_path).exists():
        raise FileNotFoundError(f"DB ファイルが存在しません: {db_path}")

    # --- 作業領域 ---
    tmp = Path(tempfile.mkdtemp(prefix="verify-date-anchor-"))
    work = Path(workdir) if workdir is not None else tmp
    work.mkdir(parents=True, exist_ok=True)

    # --- 作業コピー ---
    raw_copy = work / "raw_data.json"
    shutil.copy2(raw_path, raw_copy)
    db_copy: Optional[Path] = None
    if db_path is not None:
        db_copy = work / "db.sqlite3"
        shutil.copy2(db_path, db_copy)

    report: Dict[str, Any] = {"workdir": str(work)}
    structured = _read_json(structured_path)
    clinic_name = (structured.get("clinic") or "").strip()
    structured_date = structured.get("date")

    report["input"] = {
        "raw": str(raw_path),
        "structured_clinic": clinic_name,
        "structured_date": structured_date,
        "db": str(db_path) if db_path else None,
        "threshold": threshold,
    }

    # --- 前提条件判定（作業コピー上で実施） ---
    if db_copy is None:
        report["precondition"] = {"ok": False, "reason": "DB が指定されていないため date 基準は判定不可"}
        return report
    if not clinic_name:
        report["precondition"] = {"ok": False, "reason": "structured に clinic がありません"}
        return report
    if not structured_date:
        report["precondition"] = {"ok": False, "reason": "structured に date がありません"}
        return report

    clinic_id = _clinic_id_by_name(db_copy, clinic_name)
    template = _load_template(db_copy, clinic_id) if clinic_id else None
    pre = {"ok": True}
    basis = "topmost"
    coords: Dict[str, Any] = {}
    if not clinic_id or not template:
        pre = {"ok": False, "reason": f"対象テンプレートが見つかりません（clinic={clinic_name!r}）"}
    else:
        basis = template.get("coord_basis") or "topmost"
        coords = template.get("coords_corrections") or {}
        if basis != "date":
            pre = {"ok": False, "reason": f"coord_basis が 'date' ではありません（現在: {basis}）"}
        elif coords.get("date") is None:
            pre = {"ok": False, "reason": "date box が未学習です（coords_corrections['date'] が None）"}
    report["precondition"] = pre
    if not pre["ok"]:
        return report

    # --- 段階1: 候補検索 ---
    entries = _entries(_read_json(raw_copy))
    candidates = find_date_candidates(entries, structured_date)
    report["candidates"] = [{"text": e.get("text"), "box": e.get("box")} for e in candidates]

    # --- 段階2: 候補解決 ---
    anchor = resolve_date_anchor(
        entries,
        structured_date,
        template_date_box=coords["date"],
        template_basis=basis,
        template_coords=coords,
        proximity_threshold=threshold,
    )
    if anchor is None:
        report["resolution"] = {
            "ok": False,
            "reason": "date 候補が解決不能（同点/曖昧）。安全側フォールバックとして誤候補は採用しない",
        }
        return report
    report["resolution"] = {"ok": True, "anchor_box": anchor}

    # --- 段階3: 座標再正規化（作業コピーを date 基準に上書き） ---
    norm = normalize_coordinates_by_anchor(raw_copy, anchor)
    if not norm.get("normalized"):
        report["renormalization"] = {"ok": False, "reason": "再正規化に失敗しました"}
        return report
    offset = (norm.get("offset_x"), norm.get("offset_y"))
    report["renormalization"] = {"ok": True, "offset_x": offset[0], "offset_y": offset[1]}

    # --- 段階4: 座標ベース抽出 ---
    entries_date = _entries(_read_json(raw_copy))
    proximity = search_fields_by_proximity(entries_date, coords, threshold=threshold)
    report["extraction"] = {k: v for k, v in proximity.items() if k != "date"}

    # --- 終了コード判定 ---
    report["reports_balance"] = {
        "originals_untouched": True,
        "receipts_not_inserted": True,
    }
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="date anchor 再正規化・座標抽出の実データ検証ツール (Issue #47)")
    parser.add_argument("--raw", required=True, help="date anchor 適用前の raw_data.json（topmost 基準）")
    parser.add_argument("--structured", required=True, help="対応する structured_data.json")
    parser.add_argument("--db", default=None, help="対象テンプレートを含む SQLite DB（coord_basis='date'）")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_PROXIMITY_THRESHOLD,
        help=f"proximity 閾値 (px, default={DEFAULT_PROXIMITY_THRESHOLD})",
    )
    parser.add_argument("--workdir", default=None, help="作業領域（残したい場合。未指定は一時領域）")
    parser.add_argument("--report", default=None, help="任意で JSON 報告の出力先")
    args = parser.parse_args(argv)

    try:
        report = verify_date_anchor(
            Path(args.raw),
            Path(args.structured),
            Path(args.db) if args.db else None,
            threshold=args.threshold,
            workdir=Path(args.workdir) if args.workdir else None,
        )
    except FileNotFoundError as exc:
        print(f"PRECONDITION FAIL: {exc}")
        return EXIT_PRECOND

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    # --- 表示 ---
    input_ = report["input"]
    print(f"== date anchor 検証 ==")
    print(f"  raw       : {input_['raw']}")
    print(f"  clinic    : {input_['structured_clinic']}")
    print(f"  date      : {input_['structured_date']}")
    print(f"  threshold : {input_['threshold']}")
    pre = report["precondition"]
    if not pre["ok"]:
        print(f"\n[前提不成立] {pre['reason']}")
        return EXIT_PRECOND
    print(f"\n[前提OK] coord_basis='date', date box 学習済み")

    print(f"\n[段階1] date 候補一覧 ({len(report['candidates'])} 件)")
    for c in report["candidates"]:
        print(f"  - {c['text']!r}  box={c['box']}")

    res = report["resolution"]
    if not res["ok"]:
        print(f"\n[段階2] {res['reason']}")
        return EXIT_UNRESOLVED
    print(f"\n[段階2] 採用候補 box={res['anchor_box']}")

    ren = report["renormalization"]
    if not ren["ok"]:
        print(f"\n[段階3] {ren['reason']}")
        return EXIT_PRECOND
    print(f"\n[段階3] 適用オフセット=({ren['offset_x']}, {ren['offset_y']})")

    print(f"\n[段階4] 座標ベース抽出結果")
    for k, v in report["extraction"].items():
        print(f"  {k}: {v!r}")

    print("\n[確認] 元の raw / DB / structured は不変更。レシートは追加されていません。")
    print(f"作業コピー: {report['workdir']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
