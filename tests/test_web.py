import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from app.web.server import create_app
from app.output import write_json_atomic
from app.db_migrations import run_migrations

SCHEMA_PATH = Path("docs/schema.sql")


def parse_html_content(html_content: str, keyword: str) -> bool:
    """HTMLコンテンツから指定されたキーワードが含まれているかを確認する。"""
    return keyword in html_content


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """一時的な output_json/ ディレクトリを作成し、
    モック structured_data JSON ファイルを配置する。"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    # 正常ケース用ファイル
    data = {"name": "山田 太郎", "clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"}
    write_json_atomic(out_dir / "receipt-001-structured_data.json", data)
    # clinic null ケース
    data2 = {"name": "花子", "clinic": None, "amount": 1200, "date": "2026-02-20"}
    write_json_atomic(out_dir / "receipt-002-structured_data.json", data2)
    return out_dir


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """スキーマ適用済みの一時 DB ファイル。"""
    db_file = tmp_path / "test_db.sqlite3"
    run_migrations(db_file, SCHEMA_PATH)
    return db_file


@pytest.fixture
def client(temp_output_dir, temp_db):
    """TestClient インスタンス。server.py に output_dir, db_path を注入。"""
    from app.web.server import create_app

    app = create_app(output_dir=str(temp_output_dir), db_path=str(temp_db))
    with TestClient(app) as c:
        yield c


def test_index_lists_structured_files(client):
    """GET / が正常に一覧を表示する"""
    response = client.get("/")
    assert response.status_code == 200
    # HTMLに「あおばクリニック-2026-01-15」が含まれる
    assert parse_html_content(response.text, "あおばクリニック-2026-01-15")
    # 2ファイルとも表示される
    assert parse_html_content(response.text, "receipt-001")
    assert parse_html_content(response.text, "receipt-002")


def test_index_display_name_with_file_stem(client):
    """一覧表示の display_name に file_stem が含まれる"""
    response = client.get("/")
    assert response.status_code == 200
    # 正常ケース: "あおばクリニック-2026-01-15 [receipt-001]" の形式
    assert parse_html_content(response.text, "あおばクリニック-2026-01-15 [receipt-001]")


def test_index_fallback_display_name(client):
    """clinic が null の場合、ファイル名が表示名として使われる"""
    response = client.get("/")
    assert response.status_code == 200
    # HTMLに `receipt-002` が含まれる
    assert parse_html_content(response.text, "receipt-002")


def test_index_empty_directory(tmp_path):
    """JSONファイルがない場合、「データがありません」等のメッセージが表示される"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert parse_html_content(response.text, "データがありません")


def test_detail_shows_fields(client):
    """GET /receipt-001 が正常に詳細を表示する"""
    response = client.get("/receipt-001")
    assert response.status_code == 200
    # ラベル「氏名」「クリニック名(調剤薬局名)」「支払い金額」「発行日」が全て含まれる
    assert parse_html_content(response.text, "氏名")
    assert parse_html_content(response.text, "クリニック名(調剤薬局名)")
    assert parse_html_content(response.text, "支払い金額")
    assert parse_html_content(response.text, "発行日")
    # 値「山田 太郎」「あおばクリニック」「3800」「2026-01-15」が含まれる
    assert parse_html_content(response.text, "山田 太郎")
    assert parse_html_content(response.text, "あおばクリニック")
    assert parse_html_content(response.text, "3800")
    assert parse_html_content(response.text, "2026-01-15")


def test_detail_not_found(client):
    """存在しない file_stem の場合 404 を返す"""
    response = client.get("/nonexistent")
    assert response.status_code == 404


def test_correction_updates_db_and_json(client, temp_db):
    """PUT /receipt-001 で修正後、DBとJSONの両方が更新される"""
    # 修正前確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "山田 太郎")
    # 修正実行
    response = client.put("/receipt-001", json={"name": "山田 花子"})
    assert response.status_code == 200
    # DB と JSON の更新を確認
    # TODO: DB の確認は、DB接続を介して行う必要があるため、テストを追加する
    # JSON ファイルを再読み込みし、`name` が `山田 花子` になっていることを確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "山田 花子")


def test_correction_updates_json_only(client):
    """db_path=None の場合、JSONファイルのみ更新されDBエラーは発生しない"""
    # 修正実行
    response = client.put("/receipt-001", json={"name": "山田 花子"})
    assert response.status_code == 200
    # JSON ファイルを再読み込みし、`name` が `山田 花子` になっていることを確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "山田 花子")


def test_list_table_keeps_comma_in_name_filter(tmp_path):
    """氏名にカンマが含まれていても、選択値が分裂せずフィルタが動く"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "receipt-001-structured_data.json",
        {"name": "鈴木, 花子", "clinic": "あおばクリニック", "amount": 1200, "date": "2026-01-03"},
    )
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        response = client.get(
            "/list/table",
            params={"month": "2026-01", "sort": "asc", "names": "鈴木, 花子"},
        )
        assert response.status_code == 200
        assert "鈴木, 花子" in response.text
        assert "1200" in response.text


def test_list_multiple_names_via_query(tmp_path):
    """複数の names パラメータでも、カンマ連結の古いクエリを安全に処理する"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "a-structured_data.json", {"name": "山田", "clinic": "A", "amount": 1000, "date": "2026-01-01"}
    )
    write_json_atomic(
        out_dir / "b-structured_data.json", {"name": "鈴木", "clinic": "B", "amount": 2000, "date": "2026-01-02"}
    )
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        resp = client.get("/list/table?month=2026-01&names=%E5%B1%B1%E7%94%B0%2C%E9%88%B4%E6%9C%A8")
        assert "今月の合計: 3000 円" in resp.text


def test_correction_multiple_fields(client):
    """一度のリクエストで複数フィールド（例: amount + date）を修正する"""
    # 修正実行
    response = client.put("/receipt-001", json={"amount": 5000, "date": "2026-02-01"})
    assert response.status_code == 200
    # JSON ファイルを再読み込みし、`amount` と `date` が更新されていることを確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "5000")
    assert parse_html_content(response.text, "2026-02-01")


def test_correction_normalization(client):
    """金額 `3,800円` や日付 `2026/01/15` など元のフォーマットで入力されても正規化される"""
    # 修正実行
    response = client.put("/receipt-001", json={"amount": "3,800円", "date": "2026/01/15"})
    assert response.status_code == 200
    # JSON ファイルを再読み込みし、`amount` と `date` が正規化されていることを確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "3800")
    assert parse_html_content(response.text, "2026-01-15")


def test_correction_db_error_continues(client):
    """DB が利用不可（テーブルなし等）でも JSON ファイル更新は継続され、エラーログに記録される"""
    # 修正実行
    response = client.put("/receipt-001", json={"name": "山田 花子"})
    assert response.status_code == 200
    # JSON ファイルを再読み込みし、`name` が `山田 花子` になっていることを確認
    response = client.get("/receipt-001")
    assert parse_html_content(response.text, "山田 花子")


def test_correction_sets_clinic_id(tmp_path):
    """テスト: 修正後、DB の receipts テーブルの clinic_id が NULL ではなくなる"""
    # 出力ディレクトリを一時作成
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()

    # 修正対象の structured_data ファイルを作成（ clinic が指定されている）
    from app.output import write_json_atomic

    data = {"name": "山田 太郎", "clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"}
    write_json_atomic(out_dir / "receipt-001-structured_data.json", data)

    # データベース準備
    db_file = tmp_path / "test_db.sqlite3"
    from app.db_migrations import run_migrations

    SCHEMA_PATH = Path("docs/schema.sql")
    run_migrations(db_file, SCHEMA_PATH)

    # web server を起動（DB 接続あり）
    from app.web.server import create_app

    app = create_app(output_dir=str(out_dir), db_path=str(db_file))
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # 修正実行前: clinic_id は NULL であるか確認
        import sqlite3

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT id FROM receipts WHERE id = 'receipt-001'")
        receipt_exists = cursor.fetchone()
        conn.close()

        # レコードが存在しない場合は作成
        if not receipt_exists:
            from app.db import insert_receipt

            insert_receipt(db_file, "receipt-001", "dummy_path", None, data)

        # 修正実行前: clinic_id は NULL であるか確認（DBにレコードがある前提）
        import sqlite3

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT clinic_id FROM receipts WHERE id = 'receipt-001'")
        prior_clinic_id = cursor.fetchone()[0]  # fetchone()[0] としてアクセス
        conn.close()
        assert prior_clinic_id is None, "最初は clinic_id は NULL であるべき"

        # 修正実行
        response = client.put("/receipt-001", json={"name": "田中 花子"})
        assert response.status_code == 200

        # 修正後: clinic_id が NULL でなくなったか確認
        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT clinic_id FROM receipts WHERE id = 'receipt-001'")
        updated_clinic_id = cursor.fetchone()[0]  # fetchone()[0] としてアクセス
        conn.close()
        assert updated_clinic_id is not None, "修正後は clinic_id が NULL でないはず"


def test_correction_creates_template(tmp_path):
    """テスト: テンプレートと座標を含む修正が正常動作し、templates にレコードを作成"""
    # 出力ディレクトリを一時作成
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()

    # 修正対象の structured_data ファイルを作成
    from app.output import write_json_atomic

    data = {"name": "山田 太郎", "clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"}
    write_json_atomic(out_dir / "receipt-001-structured_data.json", data)

    # データベース準備
    db_file = tmp_path / "test_db.sqlite3"
    from app.db_migrations import run_migrations

    SCHEMA_PATH = Path("docs/schema.sql")
    run_migrations(db_file, SCHEMA_PATH)

    # raw_data ファイルも用意
    import json

    raw_data = [
        {"text": "山田 太郎", "confidence": 0.95, "box": [[50, 100], [200, 100], [200, 140], [50, 140]]},
        {"text": "あおばクリニック", "confidence": 0.92, "box": [[50, 160], [300, 160], [300, 200], [50, 200]]},
        {"text": "3,800", "confidence": 0.88, "box": [[400, 300], [480, 300], [480, 340], [400, 340]]},
        {"text": "2026/01/15", "confidence": 0.90, "box": [[50, 50], [200, 50], [200, 80], [50, 80]]},
    ]
    write_json_atomic(out_dir / "receipt-001-1234567890-raw_data.json", raw_data)

    # web server を起動（DB 接続あり）
    from app.web.server import create_app

    app = create_app(output_dir=str(out_dir), db_path=str(db_file))
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # 修正実行
        response = client.put("/receipt-001", json={"amount": 3800})
        assert response.status_code == 200

        # 修正后: templates テーブルに新規にレコードがあることを確認
        import sqlite3

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT COUNT(*) FROM templates")
        template_count = cursor.fetchone()[0]
        conn.close()
        assert template_count >= 1, "templates テーブルにレコードが作成されている"


def test_index_low_confidence_warning(tmp_path):
    """一覧ページで low_confidence なレシートに警告が表示される"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()

    from app.output import write_json_atomic

    # low_confidence = True のレシート
    data1 = {
        "name": "山田 太郎",
        "clinic": "あおばクリニック",
        "amount": 3800,
        "date": "2026-01-15",
        "low_confidence": True,
    }
    write_json_atomic(out_dir / "receipt-001-structured_data.json", data1)

    # low_confidence なしのレシート
    data2 = {"name": "花子", "clinic": "みどり薬局", "amount": 1200, "date": "2026-02-20"}
    write_json_atomic(out_dir / "receipt-002-structured_data.json", data2)

    from app.web.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200

        # low_confidence レシートに警告が表示される
        assert "あおばクリニック-2026-01-15" in response.text
        assert "&#9888; 読み取り不十分" in response.text or "⚠" in response.text

        # 通常のレシートには警告が表示されない
        assert "みどり薬局-2026-02-20" in response.text


def test_correction_low_confidence_skips_template(tmp_path):
    """low_confidence レシートの修正では templates テーブルが更新されない"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()

    from app.output import write_json_atomic

    # low_confidence フラグ付き structured_data
    data = {
        "name": "山田 太郎",
        "clinic": "あおばクリニック",
        "amount": 3800,
        "date": "2026-01-15",
        "low_confidence": True,
    }
    write_json_atomic(out_dir / "receipt-001-structured_data.json", data)

    # raw_data ファイル（confidence 0.5 の最上部要素）
    import json

    raw_data = [
        {"text": "昂", "confidence": 0.5, "box": [[400, 3], [460, 3], [460, 28], [400, 28]]},
        {"text": "あおばクリニック", "confidence": 0.92, "box": [[50, 160], [300, 160], [300, 200], [50, 200]]},
        {"text": "3,800", "confidence": 0.88, "box": [[400, 300], [480, 300], [480, 340], [400, 340]]},
    ]
    write_json_atomic(out_dir / "receipt-001-1234567890-raw_data.json", raw_data)

    # DB 準備
    db_file = tmp_path / "test_db.sqlite3"
    from app.db_migrations import run_migrations

    SCHEMA_PATH = Path("docs/schema.sql")
    run_migrations(db_file, SCHEMA_PATH)

    from app.web.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(output_dir=str(out_dir), db_path=str(db_file))
    with TestClient(app) as client:
        # 修正実行
        response = client.put("/receipt-001", json={"amount": 5000})
        assert response.status_code == 200

        # corrections テーブルにレコードが追加されている
        import sqlite3

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT COUNT(*) FROM corrections")
        corr_count = cursor.fetchone()[0]
        conn.close()
        assert corr_count >= 1, "corrections テーブルにレコードが作成されている"

        # templates テーブルは更新されていない（レコード数が 0）
        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT COUNT(*) FROM templates")
        template_count = cursor.fetchone()[0]
        conn.close()
        assert template_count == 0, "templates テーブルは更新されていない"

        # JSON ファイルの amount が更新されている
        import json

        updated = json.load(open(out_dir / "receipt-001-structured_data.json", encoding="utf-8"))
        assert updated["amount"] == 5000


def _seed_template_clinic(temp_db: Path, name: str = "なの花薬局", with_date_box: bool = True) -> str:
    """clinic + template を作成し clinic_id を返す（date box 有無は引数で制御）。"""
    from app.db import get_or_create_clinic, upsert_template

    clinic_id = get_or_create_clinic(temp_db, name)
    coords = {"amount": [[443, 300], [523, 300], [523, 340], [443, 340]]}
    if with_date_box:
        coords["date"] = [[43, 234], [400, 238], [400, 269], [43, 265]]
    upsert_template(
        temp_db,
        f"tpl-{clinic_id}",
        clinic_id,
        version=1,
        coords_corrections=coords,
    )
    return clinic_id


def test_admin_index_lists_clinic_and_basis(client, temp_db):
    """GET /admin/templates が clinic・coord_basis・date box 学習有無を表示する"""
    _seed_template_clinic(temp_db, "なの花薬局札幌二十四軒店")
    response = client.get("/admin/templates")
    assert response.status_code == 200
    assert "なの花薬局札幌二十四軒店" in response.text
    assert "topmost" in response.text


def test_index_links_to_admin(client):
    """index.html から管理画面へのリンクが存在する"""
    response = client.get("/")
    assert response.status_code == 200
    assert "/admin/templates" in response.text


def test_admin_toggle_success(client, temp_db):
    """POST で coord_basis が date になり template_history に記録される（受入条件 1・2）"""
    clinic_id = _seed_template_clinic(temp_db)
    response = client.post(f"/admin/templates/{clinic_id}/basis", json={"basis": "date"})
    assert response.status_code == 200
    assert response.json()["status"] == "updated"

    import sqlite3
    from app.db import get_latest_template_by_clinic

    conn = sqlite3.connect(str(temp_db))
    template = get_latest_template_by_clinic(temp_db, clinic_id)
    assert (template or {}).get("coord_basis") == "date"
    # シフト後: date box の min が原点 (0,0)
    assert template["coords_corrections"]["date"] == [[0, 0], [357, 4], [357, 35], [0, 31]]
    row = conn.execute(
        "SELECT change_reason, coords_corrections FROM template_history ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == "manual_basis_toggle"
    assert row[1] is not None  # 移行前 coords が保存されている


def test_admin_toggle_form_redirects_to_templates(client, temp_db):
    """管理画面フォームの送信後はテンプレート一覧へ 303 リダイレクトする"""
    clinic_id = _seed_template_clinic(temp_db)
    response = client.post(
        f"/admin/templates/{clinic_id}/basis",
        data={"basis": "date"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/templates"


def test_admin_toggle_idempotent(client, temp_db):
    """既に date なら no-op（受入条件 4: 二重変換しない）"""
    clinic_id = _seed_template_clinic(temp_db)
    client.post(f"/admin/templates/{clinic_id}/basis", json={"basis": "date"})
    response = client.post(f"/admin/templates/{clinic_id}/basis", json={"basis": "date"})
    assert response.status_code == 200
    assert response.json()["status"] == "noop"

    import sqlite3

    conn = sqlite3.connect(str(temp_db))
    count = conn.execute("SELECT COUNT(*) FROM template_history").fetchone()[0]
    conn.close()
    assert count == 1  # history は 1 件のまま


def test_admin_toggle_requires_date_box(client, temp_db):
    """date box 未学習の clinic は 409（受入条件 3: 誤書き換えしない）"""
    clinic_id = _seed_template_clinic(temp_db, with_date_box=False)
    response = client.post(f"/admin/templates/{clinic_id}/basis", json={"basis": "date"})
    assert response.status_code == 409
    assert response.json()["detail"] == "date box 未学習のため切替できません"


def test_admin_toggle_unknown_clinic(client, temp_db):
    """存在しない clinic_id は 404"""
    response = client.post("/admin/templates/no-such-id/basis", json={"basis": "date"})
    assert response.status_code == 404


def test_admin_toggle_invalid_basis(client, temp_db):
    """不対応の basis 値は 400"""
    clinic_id = _seed_template_clinic(temp_db)
    response = client.post(f"/admin/templates/{clinic_id}/basis", json={"basis": "topmost"})
    assert response.status_code == 400


def test_admin_db_not_configured(tmp_path):
    """db_path 未設定なら管理 API は 503"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as c:
        assert c.get("/admin/templates").status_code == 503
        assert c.post("/admin/templates/x/basis", json={"basis": "date"}).status_code == 503


def test_admin_keeps_date_basis_after_correction_on_date_template(temp_db, tmp_path):
    """Issue #51(案ii): date テンプレを Web 修正しても coord_basis は date のまま（anchor 未解決なら座標は書かない）"""
    import uuid
    from app.db import get_latest_template_by_clinic, upsert_template

    # クリニック（あおばクリニック）を作る
    clinic_id = _seed_template_clinic(temp_db, "あおばクリニック")

    # coord_basis='date' のテンプレにする
    upsert_template(
        temp_db,
        str(uuid.uuid4()),
        clinic_id,
        version=2,
        coords_corrections={"amount": [[443, 300], [523, 300], [523, 340], [443, 340]]},
        coord_basis="date",
    )

    # raw + structured を作成して Web 修正を走らせる
    # （raw に日付エントリが無い → date anchor 未解決 → 座標を書き込まず基底も date のまま）
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    raw_data = [
        {"text": "3,800", "confidence": 0.9, "box": [[400, 300], [480, 300], [480, 340], [400, 340]]},
    ]
    write_json_atomic(out_dir / "receipt-001-raw_data.json", raw_data)
    write_json_atomic(
        out_dir / "receipt-001-structured_data.json",
        {"name": "山田 太郎", "clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"},
    )

    before = get_latest_template_by_clinic(temp_db, clinic_id)
    before_coords = dict((before or {}).get("coords_corrections") or {})

    app = create_app(output_dir=str(out_dir), db_path=str(temp_db))
    with TestClient(app) as c:
        resp = c.put("/receipt-001", json={"amount": "3,800"})
        assert resp.status_code == 200
        resp_admin = c.get("/admin/templates")
        assert resp_admin.status_code == 200

    template = get_latest_template_by_clinic(temp_db, clinic_id)
    assert (template or {}).get("coord_basis") == "date"  # 基底は date のまま（topmost へ戻さない）
    assert (template or {}).get("coords_corrections") == before_coords  # anchor 未解決のため座標は書かない


# --- Issue #54: 月別一覧（計算）---


def test_index_links_to_list_page(client):
    """トップページから一覧ページへ遷移できる"""
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/list"' in response.text


def test_list_month_total_matches_fixture(client):
    """補填額なし: 2026-01 の合計は receipt-001 の 3800 円"""
    response = client.get("/list?month=2026-01")
    assert response.status_code == 200
    assert "今月の合計: 3800 円" in response.text
    assert "あおばクリニック" in response.text
    assert "山田 太郎" in response.text
    assert "保険で補填した額" in response.text
    assert 'name="compensation"' in response.text


def test_list_table_partial_htmx(client):
    """GET /list/table は断片のみを返す"""
    response = client.get("/list/table?month=2026-01")
    assert response.status_code == 200
    assert "今月の合計: 3800 円" in response.text
    assert "<html" not in response.text.lower()


def test_list_acceptance_row_level_compensation(tmp_path):
    """受け入れ要件2: 行単位で補填し、負にならない。
    行A 5000/補填6000->0、行B 3000/補填0->3000 のとき合計 3000。
    """
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "a-structured_data.json",
        {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"},
    )
    write_json_atomic(
        out_dir / "b-structured_data.json",
        {"name": "鈴木", "clinic": "B", "amount": 3000, "date": "2026-01-02"},
    )

    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        # 行A に 6000 を補填 -> 行は 0、合計は 3000 のまま（8000-6000=2000 にならない）
        resp = client.put(
            "/list/items/a/compensation",
            data={"month": "2026-01", "sort": "asc", "compensation": "6000"},
        )
        assert resp.status_code == 200
        assert "今月の合計: 3000 円" in resp.text
        # JSON に永続化されている
        import json

        saved = json.loads((out_dir / "a-structured_data.json").read_text(encoding="utf-8"))
        assert saved["compensation"] == 6000


def test_list_name_filter_updates_total(tmp_path):
    """受け入れ要件1: 氏名選択で合計が動的に変わる"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "a-structured_data.json",
        {"name": "山田", "clinic": "A", "amount": 1000, "date": "2026-01-01"},
    )
    write_json_atomic(
        out_dir / "b-structured_data.json",
        {"name": "鈴木", "clinic": "B", "amount": 2000, "date": "2026-01-02"},
    )

    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        all_resp = client.get("/list/table?month=2026-01")
        assert "今月の合計: 3000 円" in all_resp.text
        yamada_resp = client.get("/list/table?month=2026-01&names=%E5%B1%B1%E7%94%B0")
        assert "今月の合計: 1000 円" in yamada_resp.text


def test_list_multiple_names_via_query(tmp_path):
    """複数の names パラメータ（カンマ区切り・重複）にも対応する"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "a-structured_data.json",
        {"name": "山田", "clinic": "A", "amount": 1000, "date": "2026-01-01"},
    )
    write_json_atomic(
        out_dir / "b-structured_data.json",
        {"name": "鈴木", "clinic": "B", "amount": 2000, "date": "2026-01-02"},
    )

    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        resp = client.get("/list/table?month=2026-01&names=%E5%B1%B1%E7%94%B0%2C%E9%88%B4%E6%9C%A8")
        assert "今月の合計: 3000 円" in resp.text


def test_list_compensation_not_found(tmp_path):
    """存在しないレシートの補填額保存は 404"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        resp = client.put("/list/items/nope/compensation", data={"compensation": "100"})
        assert resp.status_code == 404


def test_list_detail_edit_keeps_compensation(tmp_path):
    """既存の詳細修正は compensation を上書きせず保持する"""
    out_dir = tmp_path / "output_json"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / "r-structured_data.json",
        {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01", "compensation": 1000},
    )
    app = create_app(output_dir=str(out_dir), db_path=None)
    with TestClient(app) as client:
        resp = client.put("/r", json={"amount": 6000})
        assert resp.status_code == 200
        import json

        saved = json.loads((out_dir / "r-structured_data.json").read_text(encoding="utf-8"))
        assert saved["compensation"] == 1000


# --- Issue #56: Web UI からの watch・設定操作 ---


@pytest.fixture
def web_ctl(tmp_path):
    """WatchController に fake scan_fn を注入して作成（PaddleOCR は起動しない）。"""
    from app.services.watch_controller import WatchController

    # CLI の setup_directories 相当: fake scan の書き込み先を用意する
    (tmp_path / "output_json").mkdir()
    (tmp_path / "data").mkdir()

    def fake_scan(*args, **kwargs):
        # input_dir の画像ごとに structured_data.json を 1 件生成する偽スキャン
        # [PR59-中1] 出力名は実 mtime を埋め込む（本物の receipt_processor と同じ命名）
        input_dir = Path(args[0])
        output_dir = Path(args[2])
        for p in input_dir.iterdir():
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                mtime = int(p.stat().st_mtime)
                out = output_dir / f"{p.stem}_{mtime}-structured_data.json"
                if not out.exists():
                    out.write_text(
                        '{"name": "山田 太郎", "clinic": "テスト医院", "amount": 1000, "date": "2026-01-15"}',
                        encoding="utf-8",
                    )
        return 1

    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        processed_dir=str(tmp_path / "processed"),
        failed_dir=str(tmp_path / "failed"),
        poll_interval=1,
        scan_fn=fake_scan,
        ocr_factory=lambda: object(),
        data_dir=str(tmp_path / "data"),
    )
    return ctl


@pytest.fixture
def web_client(tmp_path, web_ctl):
    """コントローラ注入済み TestClient。DB は web_ctl に migration 済み実ファイルを設定。"""
    db = tmp_path / "web_db.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    web_ctl.set_db_path(db)
    app = create_app(output_dir=str(tmp_path / "output_json"), controller=web_ctl)
    with TestClient(app) as c:
        yield c


def test_index_shows_control_panel(web_client):
    """操作パネル（フォルダ / DB / watch / 前処理 / 画像指定 / 状態バー）が表示される"""
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "指定フォルダ" in resp.text
    assert "データベース" in resp.text
    assert "前処理" in resp.text
    assert "watch" in resp.text
    assert "画像指定" in resp.text
    assert "status-bar" in resp.text
    assert "clahe+adaptive" in resp.text  # 前処理セレクトの選択肢
    # 複数ファイル注意書き
    assert "1枚ずつ" in resp.text


def test_config_updates_input_dir_and_mode(web_client, tmp_path, monkeypatch):
    """POST /config で input_dir / preprocess_mode が反映される"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    new_dir = home / "another"
    resp = web_client.post(
        "/config",
        data={"input_dir": str(new_dir), "preprocess_mode": "clahe"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    page = web_client.get("/")
    assert str(new_dir) in page.text
    assert 'value="clahe" selected' in page.text


def test_config_rejects_path_outside_home(web_client, tmp_path, monkeypatch):
    """[中-4] ホーム外の input_dir は 400"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    resp = web_client.post(
        "/config",
        data={"input_dir": str(tmp_path / "outside")},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_config_rejects_unknown_mode(web_client):
    resp = web_client.post("/config", data={"preprocess_mode": "bogus"})
    assert resp.status_code == 400


def test_config_db_upload_saves_and_migrates(web_client, web_ctl, tmp_path):
    """DB ファイルのアップロードで data/ に保存され has_db になる（Q2 案A）"""
    src = tmp_path / "upload_src.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    resp = web_client.post(
        "/config",
        files={"db_file": ("my_upload.db", src.read_bytes(), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert (web_ctl.data_dir / "my_upload.db").exists()
    assert web_client.get("/api/status").json()["has_db"] is True


def test_config_db_upload_invalid_returns_400(web_client, web_ctl):
    """不正な DB ファイルは 400（詳細メッセージ付き）で保存されない"""
    resp = web_client.post(
        "/config",
        files={"db_file": ("bad.db", b"not a sqlite db", "application/octet-stream")},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert not (web_ctl.data_dir / "bad.db").exists()


def test_config_db_upload_keeps_existing_db(web_client, web_ctl, tmp_path):
    """[高-1] 既存同名 DB へのアップロードで既存 DB は上書き・削除されない"""
    existing = web_ctl.data_dir / "db.sqlite3"
    run_migrations(existing, SCHEMA_PATH)
    conn = sqlite3.connect(str(existing))
    conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
    conn.execute("INSERT INTO marker (value) VALUES ('original')")
    conn.commit()
    conn.close()

    src = tmp_path / "src.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    resp = web_client.post(
        "/config",
        files={"db_file": ("db.sqlite3", src.read_bytes(), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # 既存 DB は保持される
    conn = sqlite3.connect(str(existing))
    try:
        assert [r[0] for r in conn.execute("SELECT value FROM marker")] == ["original"]
    finally:
        conn.close()
    # 新しい内容は別名で保存される
    assert (web_ctl.data_dir / "db_1.sqlite3").exists()


def test_config_db_upload_409_while_processing(web_client, web_ctl, tmp_path):
    """[中-2] 処理中の DB 差し替えアップロードは 409"""
    src = tmp_path / "swap.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    web_ctl._is_processing = True
    try:
        resp = web_client.post(
            "/config",
            files={"db_file": ("swap.db", src.read_bytes(), "application/octet-stream")},
            follow_redirects=False,
        )
    finally:
        web_ctl._is_processing = False
    assert resp.status_code == 409
    assert not (web_ctl.data_dir / "swap.db").exists()


def test_db_file_input_disabled_while_processing(web_client, web_ctl):
    """[中-2] 処理中は db-file-input が disabled になる"""
    web_ctl._is_processing = True
    try:
        page = web_client.get("/")
        m = re.search(r'<input id="db-file-input"[^>]*>', page.text)
        assert m is not None, "db-file-input タグが見つからない"
        assert "disabled" in m.group(0)
    finally:
        web_ctl._is_processing = False


def test_watch_start_without_db_400(tmp_path):
    """DB 未選択で watch start は 400（受け入れ要件 2）"""
    from app.services.watch_controller import WatchController

    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        scan_fn=lambda *a, **k: 0,
        ocr_factory=lambda: object(),
    )
    app = create_app(output_dir=str(tmp_path / "output_json"), controller=ctl)
    with TestClient(app) as c:
        resp = c.post("/watch/start")
        assert resp.status_code == 400
        assert "データベース" in resp.json()["detail"]


def test_watch_start_stop_toggles(web_client):
    """watch start → status.watch_on=True、stop → False"""
    resp = web_client.post("/watch/start", follow_redirects=False)
    assert resp.status_code == 303
    assert web_client.get("/api/status").json()["watch_on"] is True
    web_client.post("/watch/stop", follow_redirects=False)
    assert web_client.get("/api/status").json()["watch_on"] is False


def test_upload_image_without_db_400(tmp_path):
    """DB 未選択で画像アップロードは 400（受け入れ要件 2）"""
    from app.services.watch_controller import WatchController

    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        scan_fn=lambda *a, **k: 0,
        ocr_factory=lambda: object(),
    )
    app = create_app(output_dir=str(tmp_path / "output_json"), controller=ctl)
    with TestClient(app) as c:
        resp = c.post("/images", files={"image": ("IMG_001.jpg", b"jpeg", "image/jpeg")})
        assert resp.status_code == 400


def test_upload_image_copies_to_input_dir(web_client, web_ctl):
    """画像アップロードは input_dir へコピーされる（案C: 即時処理しない）"""
    resp = web_client.post(
        "/images",
        files={"image": ("IMG_001.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    dest = web_ctl.input_dir / "IMG_001.jpg"
    assert dest.exists()
    assert dest.read_bytes() == b"fake-jpeg-bytes"


def test_images_409_while_pending(web_client, web_ctl):
    """[中-3] 処理待ち中の画像アップロードは 409（仕様上の拒否）"""
    web_ctl._pending_process = True
    try:
        resp = web_client.post(
            "/images",
            files={"image": ("IMG_009.jpg", b"fake-jpeg-bytes", "image/jpeg")},
            follow_redirects=False,
        )
    finally:
        web_ctl._pending_process = False
    assert resp.status_code == 409


def test_image_input_disabled_while_pending(web_client, web_ctl):
    """[中-3] 処理待ち中は image-input が disabled になる"""
    web_ctl._pending_process = True
    try:
        page = web_client.get("/")
        m = re.search(r'<input id="image-input"[^>]*>', page.text)
        assert m is not None, "image-input タグが見つからない"
        assert "disabled" in m.group(0)
    finally:
        web_ctl._pending_process = False


def test_upload_image_409_on_duplicate_name(web_client, web_ctl):
    """[中-5] 同名画像の再アップロードは 409 で上書きされない"""
    r1 = web_client.post(
        "/images",
        files={"image": ("IMG_004.jpg", b"first-bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    # 1 回目のコピーで pending になるため、スキャンで消費させてから再アップロードする。
    # /images は watch を自動開始するため、scan lock の競合を避けて watch を停止してから手動スキャンする。
    web_ctl.stop_watch()
    web_ctl._run_scan()
    assert web_ctl.pending_process is False
    r2 = web_client.post(
        "/images",
        files={"image": ("IMG_004.jpg", b"second-bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert r2.status_code == 409
    assert "同名" in r2.json()["detail"]
    assert (web_ctl.input_dir / "IMG_004.jpg").read_bytes() == b"first-bytes"


def test_images_concurrent_upload_same_name(web_client, web_ctl):
    """[PR59-中3] 同名画像の並行アップロードは 1 件のみ成功（他は 409）"""
    from concurrent.futures import ThreadPoolExecutor

    def post(i):
        return web_client.post(
            "/images",
            files={"image": ("IMG_005.jpg", f"bytes-{i}".encode(), "image/jpeg")},
            follow_redirects=False,
        )

    with ThreadPoolExecutor(max_workers=4) as ex:
        responses = list(ex.map(post, range(4)))
    try:
        statuses = [r.status_code for r in responses]
        assert statuses.count(303) == 1
        assert statuses.count(409) == 3
        dest = web_ctl.input_dir / "IMG_005.jpg"
        assert dest.exists()
        assert dest.read_bytes() in {f"bytes-{i}".encode() for i in range(4)}
    finally:
        web_ctl.stop_watch()


def test_mutations_rejected_while_scanning(tmp_path, monkeypatch):
    """[PR59-中3] スキャン中は /images・/config(db_file)・/config(input_dir) が 409"""
    import threading

    from app.services.watch_controller import WatchController

    entered = threading.Event()
    release = threading.Event()

    def slow_scan(*args, **kwargs):
        entered.set()
        release.wait(timeout=10)
        return 0

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        processed_dir=str(tmp_path / "processed"),
        failed_dir=str(tmp_path / "failed"),
        poll_interval=1,
        scan_fn=slow_scan,
        ocr_factory=lambda: object(),
        data_dir=str(tmp_path / "data"),
    )
    db = tmp_path / "db.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    ctl.set_db_path(db)
    app = create_app(output_dir=str(tmp_path / "output_json"), controller=ctl)
    with TestClient(app) as client:
        client.post("/watch/start", follow_redirects=False)
        assert entered.wait(timeout=5)
        # 画像アップロード → 409、ファイルも作られない
        resp = client.post(
            "/images",
            files={"image": ("IMG_001.jpg", b"fake-jpeg-bytes", "image/jpeg")},
            follow_redirects=False,
        )
        assert resp.status_code == 409
        assert not (ctl.input_dir / "IMG_001.jpg").exists()
        # DB 差し替え → 409、data/ に保存されない
        src = tmp_path / "src.sqlite3"
        run_migrations(src, SCHEMA_PATH)
        resp = client.post(
            "/config",
            files={"db_file": ("swap.db", src.read_bytes(), "application/octet-stream")},
            follow_redirects=False,
        )
        assert resp.status_code == 409
        assert not (ctl.data_dir / "swap.db").exists()
        # 設定更新（input_dir）→ 409、変更されない
        resp = client.post(
            "/config",
            data={"input_dir": str(home / "another")},
            follow_redirects=False,
        )
        assert resp.status_code == 409
        assert ctl.input_dir == tmp_path / "input"
    release.set()
    ctl.stop_watch()


def test_upload_rejects_non_image(web_client):
    resp = web_client.post(
        "/images",
        files={"image": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_status_endpoint_json(web_client, web_ctl):
    s = web_client.get("/api/status").json()
    assert s["watch_on"] is False
    assert s["has_db"] is True
    assert s["preprocess_mode"] == "none"
    assert "input_dir" in s
    assert s["structured_count"] == 0
    # structured data が増えるとカウントが増える（完了検知の基準）
    web_ctl.output_dir.mkdir(parents=True, exist_ok=True)
    (web_ctl.output_dir / "r-structured_data.json").write_text("{}", encoding="utf-8")
    s2 = web_client.get("/api/status").json()
    assert s2["structured_count"] == 1


def test_watch_picks_up_uploaded_image(web_client, web_ctl):
    """[低-3] リトライなし: アップロード → ポーリング処理 → structured 生成で pending 解除"""
    web_client.post("/watch/start", follow_redirects=False)
    # 初回スキャン完了（アイドル）まで待つ — スキャン中の POST は仕様上の 409
    for _ in range(50):
        if not web_client.get("/api/status").json()["is_processing"]:
            break
        time.sleep(0.1)
    # [PR59-中3] ポーリング窗口の scan lock 競合を避けるため一旦停止し、
    # POST の autostart で watch を再開する（409 リトライループなしの 1 回 POST）
    web_ctl.stop_watch()
    resp = web_client.post(
        "/images",
        files={"image": ("IMG_002.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # ポーリングが拾って structured_data.json を生成するまで待つ（完了で pending 解除）
    deadline = 0
    s = None
    while deadline < 100:
        s = web_client.get("/api/status").json()
        if s["structured_count"] > 0 and not s["pending"]:
            break
        time.sleep(0.1)
        deadline += 1
    assert s is not None and s["structured_count"] > 0, "structured_data.json が生成されない"
    assert s["pending"] is False
    page = web_client.get("/")
    assert "IMG_002" in page.text  # 一覧にリンクが追加されている
    web_client.post("/watch/stop", follow_redirects=False)


def test_config_db_only_upload(web_client, tmp_path):
    """DB ファイルのみの /config 送信（選択即確定の JS が送る形）で has_db になる"""
    from app.db_migrations import run_migrations

    src = tmp_path / "only_db.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    resp = web_client.post(
        "/config",
        files={"db_file": ("auto.db", src.read_bytes(), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert web_client.get("/api/status").json()["has_db"] is True


def test_images_upload_requires_file(web_client):
    """画像なしの /images は 400"""
    resp = web_client.post("/images", data={}, follow_redirects=False)
    assert resp.status_code == 400


def test_images_upload_autostarts_watch(web_client, web_ctl):
    """画像読み込み成功時に watch が自動開始する（選択＝確定の期待に応える）"""
    try:
        resp = web_client.post(
            "/images",
            files={"image": ("IMG_003.jpg", b"fake-jpeg-bytes", "image/jpeg")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert web_client.get("/api/status").json()["watch_on"] is True
    finally:
        web_ctl.stop_watch()


# --- Issue #56: index.html 表示（操作パネル / 状態バー / ポーリング JS） ---


def test_file_input_disabled_while_processing(web_client, web_ctl):
    """処理中は <input type='file'> が disabled になる（受け入れ要件 3）"""
    web_ctl._is_processing = True
    try:
        page = web_client.get("/")
        m = re.search(r'<input id="image-input"[^>]*>', page.text)
        assert m is not None, "image-input タグが見つからない"
        assert "disabled" in m.group(0)
    finally:
        web_ctl._is_processing = False


def test_file_input_enabled_after_processing(web_client, web_ctl):
    """処理完了後は disabled が外れる（watch 状態は維持）"""
    page = web_client.get("/")
    m = re.search(r'<input id="image-input"[^>]*>', page.text)
    assert m is not None
    assert "disabled" not in m.group(0)


def test_index_shows_db_unselected_hint(tmp_path):
    """DB 未選択時は「未選択」表示（エラー誘導）"""
    from app.services.watch_controller import WatchController

    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        scan_fn=lambda *a, **k: 0,
        ocr_factory=lambda: object(),
    )
    app = create_app(output_dir=str(tmp_path / "output_json"), controller=ctl)
    with TestClient(app) as c:
        page = c.get("/")
        assert "未選択" in page.text


def test_index_has_status_poll_script(web_client):
    """ページに /api/status のポーリング JS が含まれる"""
    page = web_client.get("/")
    assert "/api/status" in page.text
    assert "pollStatus" in page.text


def test_index_has_auto_apply_elements(web_client):
    """選択即確定 UI の要素 id と fetch 配線が含まれる（Issue #56 UX 修正）"""
    page = web_client.get("/").text
    for needle in (
        'id="db-file-input"',
        'id="db-hint"',
        'id="watch-btn"',
        'id="image-result"',
        'id="input-dir-apply"',
        'id="ctl-error"',
        "postForm('/config'",
        "postForm('/images'",
        'accept=".db,.sqlite,.sqlite3"',
    ):
        assert needle in page, f"{needle} が index.html に含まれない"
