"""Tests for app.verify_date_anchor (Issue #47 verification tool)."""

import json
import shutil
from pathlib import Path

import pytest

from app.verify_date_anchor import verify_date_anchor, EXIT_OK, EXIT_UNRESOLVED, main


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _center(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)


def _box(tl, br):
    return [[tl[0], tl[1]], [br[0], tl[1]], [br[0], br[1]], [tl[0], br[1]]]


def _date_basis_geometry():
    """Coherent geometry: the anchor box in TOPMOST raw frame and the template's
    date box (origin) are consistent so date-basis re-normalization overlaps."""
    # template coords are ALREADY date-basis (date at origin ~[0,0]).
    template_coords = {
        "date": _box((0, 0), (273, 35)),
        "name": _box((5, -176), (148, -107)),
        "amount": _box((377, 418), (481, 455)),
    }
    # raw (topmost) frame: anchor box somewhere; field boxes = anchor_top_left + date-basis offset
    anchor_tl = (100, 300)
    raw_coords = {
        "date": _box(anchor_tl, (anchor_tl[0] + 273, anchor_tl[1] + 35)),
        "name": _box(
            (anchor_tl[0] + 5, anchor_tl[1] - 176),
            (anchor_tl[0] + 148, anchor_tl[1] - 107),
        ),
        "amount": _box(
            (anchor_tl[0] + 377, anchor_tl[1] + 418),
            (anchor_tl[0] + 481, anchor_tl[1] + 455),
        ),
    }
    return anchor_tl, template_coords, raw_coords


def _seed_date_basis_template(db_path: Path, clinic_name: str, template_coords: dict):
    """Insert clinic + date-basis template directly (verify tool only reads)."""
    import sqlite3

    con = sqlite3.connect(db_path)
    with con:
        con.execute("CREATE TABLE IF NOT EXISTS clinics (id TEXT PRIMARY KEY, name TEXT UNIQUE)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS templates (id TEXT, clinic_id TEXT, version TEXT, "
            "coords_corrections TEXT, coord_basis TEXT, created_at TEXT, PRIMARY KEY(id))"
        )
        con.execute("INSERT INTO clinics(id,name) VALUES(?,?)", ("clinic-1", clinic_name))
        con.execute(
            "INSERT INTO templates(id,clinic_id,version,coords_corrections,coord_basis,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("tpl-1", "clinic-1", "1", json.dumps(template_coords), "date", "2026-10-01T00:00:00"),
        )
    con.close()


def test_success_reports_stages_and_keeps_originals(tmp_path: Path, capsys):
    work = tmp_path / "verify"
    work.mkdir()
    existing_raw = work / "raw_data.json"
    existing_raw.write_text("keep existing data", encoding="utf-8")
    existing_db = work / "db.sqlite3"
    existing_db.write_text("keep existing database", encoding="utf-8")
    anchor_tl, template_coords, raw_coords = _date_basis_geometry()

    raw = tmp_path / "raw_data.json"
    _write_json(
        raw,
        [
            {"text": "調剤日：2026/09/02", "box": raw_coords["date"]},
            {"text": "なの花薬局", "box": raw_coords["name"]},
            {"text": "1450", "box": raw_coords["amount"]},
        ],
    )
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "なの花薬局", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    _seed_date_basis_template(db, "なの花薬局", template_coords)

    before_raw = raw.read_bytes()
    before_db = db.read_bytes()

    report = verify_date_anchor(raw, structured, db, workdir=work)

    assert report["precondition"]["ok"] is True
    assert len(report["candidates"]) >= 1
    assert report["resolution"]["ok"] is True
    assert report["renormalization"]["ok"] is True
    assert isinstance(report["renormalization"]["offset_x"], (int, float))
    normalized_boxes = report["renormalization"]["normalized_boxes"]
    assert normalized_boxes["date"]["box"] == _box((0, 0), (273, 35))
    assert normalized_boxes["name"]["box"] == _box((5, -176), (148, -107))
    assert normalized_boxes["amount"]["box"] == _box((377, 418), (481, 455))
    assert "name" in report["extraction"] or "amount" in report["extraction"]
    assert Path(report["workdir"]).parent == work

    report_path = tmp_path / "report.json"
    assert (
        main(
            [
                "--raw",
                str(raw),
                "--structured",
                str(structured),
                "--db",
                str(db),
                "--workdir",
                str(work),
                "--report",
                str(report_path),
            ]
        )
        == EXIT_OK
    )
    output = capsys.readouterr().out
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["renormalization"]["normalized_boxes"]["date"]["box"] == _box((0, 0), (273, 35))
    assert "再正規化後 date box=" in output
    assert "box=" in output
    assert Path(report["workdir"]) != Path(output.split("作業コピー: ")[-1].strip())
    assert existing_raw.read_text(encoding="utf-8") == "keep existing data"
    assert existing_db.read_text(encoding="utf-8") == "keep existing database"
    assert raw.read_bytes() == before_raw, "raw が変更されている"
    assert db.read_bytes() == before_db, "DB が変更されている"


def test_precondition_not_date_returns_reason(tmp_path: Path):
    work = tmp_path / "verify"
    _, template_coords, raw_coords = _date_basis_geometry()
    raw = tmp_path / "raw_data.json"
    _write_json(raw, [{"text": "2026/09/02", "box": raw_coords["date"]}])
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "X", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    # template in TOPMOST basis -> precondition must fail with reason
    import sqlite3

    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE clinics (id TEXT PRIMARY KEY, name TEXT UNIQUE)")
        con.execute(
            "CREATE TABLE templates (id TEXT, clinic_id TEXT, version TEXT, "
            "coords_corrections TEXT, coord_basis TEXT, created_at TEXT, PRIMARY KEY(id))"
        )
        con.execute("INSERT INTO clinics(id,name) VALUES(?,?)", ("c1", "X"))
        con.execute(
            "INSERT INTO templates VALUES(?,?,?,?,?,?)",
            ("t1", "c1", "1", json.dumps(template_coords), "topmost", "2026-10-01"),
        )
    con.close()

    report = verify_date_anchor(raw, structured, db, workdir=work)
    assert report["precondition"]["ok"] is False
    assert "topmost" in report["precondition"]["reason"]


def test_missing_template_returns_reason(tmp_path: Path):
    work = tmp_path / "verify"
    raw = tmp_path / "raw_data.json"
    _write_json(raw, [{"text": "2026/09/02", "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}])
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "存在しない", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    import sqlite3

    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE clinics (id TEXT PRIMARY KEY, name TEXT UNIQUE)")
        con.execute(
            "CREATE TABLE templates (id TEXT, clinic_id TEXT, version TEXT, "
            "coords_corrections TEXT, coord_basis TEXT, created_at TEXT, PRIMARY KEY(id))"
        )
    con.close()

    report = verify_date_anchor(raw, structured, db, workdir=work)
    assert report["precondition"]["ok"] is False
    assert "見つかりません" in report["precondition"]["reason"]


def test_tie_date_candidates_returns_unresolved(tmp_path: Path):
    """Two date candidates with the SAME top-left tie in layout scoring -> unresolved."""
    work = tmp_path / "verify"
    anchor_tl, template_coords, _ = _date_basis_geometry()
    raw_coords = template_coords
    # Two date candidates sharing the SAME min-x/min-y (anchor_tl) so layout
    # scoring cannot separate them -> resolution must fail (safe fallback).
    raw = tmp_path / "raw_data.json"
    _write_json(
        raw,
        [
            {"text": "調剤日：2026/09/02", "box": _box(anchor_tl, (anchor_tl[0] + 273, anchor_tl[1] + 35))},
            {"text": "発行日：2026/09/02", "box": _box(anchor_tl, (anchor_tl[0] + 273, anchor_tl[1] + 55))},
            {"text": "なの花薬局", "box": raw_coords["name"]},
            {"text": "1450", "box": raw_coords["amount"]},
        ],
    )
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "なの花薬局", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    _seed_date_basis_template(db, "なの花薬局", template_coords)

    before_raw = raw.read_bytes()
    before_db = db.read_bytes()

    report = verify_date_anchor(raw, structured, db, workdir=work)

    assert report["precondition"]["ok"] is True
    assert len(report["candidates"]) == 2
    assert report["resolution"]["ok"] is False
    assert "renormalization" not in report
    assert raw.read_bytes() == before_raw, "raw が変更されている"
    assert db.read_bytes() == before_db, "DB が変更されている"


def test_verify_force_extracts_when_anchor_none(tmp_path: Path):
    """force=True のとき、anchor 解決が不能でも coords_corrections で近傍抽出する (Issue #50)."""
    work = tmp_path / "verify"
    _, template_coords, _ = _date_basis_geometry()
    # date 候補 0 件: raw に date と一致する OCR テキストを含めない
    # （座標は date 基準の template coords と一致させる -> 近傍抽出が成立する）
    raw = tmp_path / "raw_data.json"
    _write_json(
        raw,
        [
            {"text": "なの花薬局", "box": template_coords["name"]},
            {"text": "1450", "box": template_coords["amount"]},
        ],
    )
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "なの花薬局", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    _seed_date_basis_template(db, "なの花薬局", template_coords)

    report = verify_date_anchor(raw, structured, db, workdir=work, force=True)

    assert report["precondition"]["ok"] is True
    assert report["resolution"]["ok"] is True
    assert report["resolution"]["mode"] == "force"
    assert report["resolution"]["anchor_box"] is None
    assert "name" in report["force_extraction"]
    assert report["force_extraction"]["name"] == "なの花薬局"
    assert report["force_extraction"]["amount"] == "1450"
    assert "renormalization" not in report


def test_main_force_mode_prints_force_extraction(tmp_path: Path, capsys):
    """--force 実行時、main() は KeyError にならず force 抽出結果を表示する (Issue #50)."""
    work = tmp_path / "verify"
    _, template_coords, _ = _date_basis_geometry()
    raw = tmp_path / "raw_data.json"
    _write_json(
        raw,
        [
            {"text": "なの花薬局", "box": template_coords["name"]},
            {"text": "1450", "box": template_coords["amount"]},
        ],
    )
    structured = tmp_path / "structured_data.json"
    _write_json(structured, {"clinic": "なの花薬局", "date": "2026-09-02"})
    db = tmp_path / "db.sqlite3"
    _seed_date_basis_template(db, "なの花薬局", template_coords)

    exit_code = main(
        [
            "--raw",
            str(raw),
            "--structured",
            str(structured),
            "--db",
            str(db),
            "--workdir",
            str(work),
            "--force",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "強制抽出" in output
    assert "なの花薬局" in output
    assert "1450" in output


def test_default_workdir_is_cleaned_on_return_and_exception(tmp_path: Path, monkeypatch):
    import app.verify_date_anchor as verify_module

    original_temporary_directory = verify_module.tempfile.TemporaryDirectory
    created = []

    def track_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(verify_module.tempfile, "TemporaryDirectory", track_temporary_directory)

    raw = tmp_path / "raw.json"
    _write_json(raw, [])
    structured = tmp_path / "structured.json"
    _write_json(structured, {"clinic": "Clinic", "date": "2026-09-02"})
    report = verify_date_anchor(raw, structured, None)
    assert report["precondition"]["ok"] is False
    assert created[-1].exists() is False

    structured.write_text("{invalid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        verify_date_anchor(raw, structured, None)
    assert created[-1].exists() is False
