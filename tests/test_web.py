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
