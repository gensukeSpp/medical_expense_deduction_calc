"""Tests for app.verify_date_anchor (Issue #47 verification tool)."""

import json
import shutil
from pathlib import Path

import pytest

from app.verify_date_anchor import verify_date_anchor, EXIT_OK, EXIT_UNRESOLVED


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


def test_success_reports_stages_and_keeps_originals(tmp_path: Path):
    work = tmp_path / "verify"
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
    assert "name" in report["extraction"] or "amount" in report["extraction"]
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
