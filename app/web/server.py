from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Any, Iterable

from app.calc import available_names
from app.services.receipt_service import ReceiptService
from app.services.receipt_list_service import ReceiptListService
from app.services.template_admin_service import (
    ClinicNotFoundError,
    InvalidBasisError,
    TemplateAdminService,
    TemplateBasisConflictError,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _parse_names(values: Iterable[Any], known_names: Iterable[str] | None = None) -> list[str]:
    """氏名リストを正規化する。

    互換性のため、古い `?names=山田,鈴木` のような壊れたクエリは複数氏名として
    解釈する。ただし `鈴木, 花子` のように実際の氏名そのものにカンマが含まれる
    値は、既知の氏名一覧と一致する場合は分割しない。
    """
    known = {name.strip() for name in (known_names or []) if name and str(name).strip()}
    names: list[str] = []
    for value in values:
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        split_values = [part.strip() for part in raw.split(",") if part.strip()]
        if len(split_values) > 1 and raw not in known:
            for part in split_values:
                if part and part not in names:
                    names.append(part)
            continue
        if raw not in names:
            names.append(raw)
    return names


def create_app(output_dir: str = "output_json", db_path: str | None = None) -> FastAPI:
    """FastAPI アプリケーションを生成するファクトリ関数。テストで使用可能。"""
    app = FastAPI()

    def get_output_dir() -> Path:
        return Path(output_dir)

    def get_db_path() -> Path | None:
        return Path(db_path) if db_path else None

    async def get_receipt_service(
        output_dir: Path = Depends(get_output_dir),
        db_path: Path | None = Depends(get_db_path),
    ) -> ReceiptService:
        return ReceiptService(output_dir, db_path)

    def get_template_admin_service(
        db_path: Path | None = Depends(get_db_path),
    ) -> TemplateAdminService:
        return TemplateAdminService(db_path)

    def get_receipt_list_service(
        output_dir: Path = Depends(get_output_dir),
    ) -> ReceiptListService:
        """一覧ページ用サービス（JSON のみ参照、DB 不要）"""
        return ReceiptListService(output_dir)

    async def _list_context(request: Request, service: ReceiptListService, unprocessed: bool = False) -> dict:
        """クエリ（GET）とフォーム（PUT）の双方から一覧の文脈を構築する。

        htmx は include した値をフォーム形式で送るため、PUT では query_params が空になる。
        """
        normal_rows, date_missing_rows = service.load_rows()
        known_names = set()
        for row in normal_rows + date_missing_rows:
            if row.name:
                known_names.add(row.name)

        if request.method == "GET":
            params = request.query_params
            month = params.get("month", "")
            sort = params.get("sort", "asc")
            names = _parse_names(params.getlist("names"), known_names)
            unprocessed = params.get("unprocessed", "").lower() == "true"
        else:
            form = await request.form()
            month = str(form.get("month", ""))
            sort = str(form.get("sort", "asc"))
            names = _parse_names(form.getlist("names"), known_names)
            unprocessed = False
        ctx = service.get_page_context(month, sort, names)
        ctx["unprocessed"] = unprocessed
        if unprocessed:
            normal_rows, date_missing_rows = service.load_rows()
            ctx["date_missing_count"] = len(date_missing_rows)
            ctx["date_missing_rows"] = date_missing_rows
        else:
            ctx["date_missing_count"] = 0
            ctx["date_missing_rows"] = []
        return ctx

    @app.get("/list", response_class=HTMLResponse)
    async def receipt_list_page(
        request: Request,
        service: ReceiptListService = Depends(get_receipt_list_service),
    ):
        """一覧ページ全文（月ナビ + 表）。"""
        context = await _list_context(request, service)
        return templates.TemplateResponse(request=request, name="list.html", context=context)

    @app.get("/list/table", response_class=HTMLResponse)
    async def receipt_list_table(
        request: Request,
        service: ReceiptListService = Depends(get_receipt_list_service),
    ):
        """一覧断片（htmx swap 対象）。氏名チェック・補填額保存後に再描画される。"""
        context = await _list_context(request, service)
        return templates.TemplateResponse(request=request, name="list_table.html", context=context)

    @app.put("/list/items/{file_stem}/compensation", response_class=HTMLResponse)
    async def receipt_list_update_compensation(
        file_stem: str,
        request: Request,
        service: ReceiptListService = Depends(get_receipt_list_service),
    ):
        """補填額を保存し、その月の一覧断片を返す（htmx）。"""
        form = await request.form()
        value = form.get("compensation", "")
        try:
            service.save_compensation(file_stem, value)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")
        context = await _list_context(request, service)
        return templates.TemplateResponse(request=request, name="list_table.html", context=context)

    @app.get("/admin/templates", response_class=HTMLResponse)
    async def admin_template_list(
        request: Request,
        service: TemplateAdminService = Depends(get_template_admin_service),
    ):
        """管理画面: clinic と最新 template の一覧（coord_basis / date box 学習有無）"""
        try:
            template_rows = service.list_templates()
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return templates.TemplateResponse(
            request=request,
            name="admin_templates.html",
            context={"templates": template_rows},
        )

    @app.post("/admin/templates/{clinic_id}/basis")
    async def admin_toggle_basis(
        clinic_id: str,
        request: Request,
        service: TemplateAdminService = Depends(get_template_admin_service),
    ):
        """coord_basis を date へ手動切替（date box 学習済みのみ許可・冪等）"""
        is_json_request = "application/json" in request.headers.get("content-type", "")
        basis = None
        if is_json_request:
            try:
                basis = (await request.json()).get("basis")
            except Exception:
                basis = None
        else:
            basis = (await request.form()).get("basis")
        try:
            result = service.toggle_basis(clinic_id, str(basis) if basis is not None else "")
        except ClinicNotFoundError:
            raise HTTPException(status_code=404, detail="clinic not found")
        except TemplateBasisConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except InvalidBasisError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        if is_json_request:
            return result
        return RedirectResponse(url="/admin/templates", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def read_root(request: Request, service: ReceiptService = Depends(get_receipt_service)):
        """一覧ページを表示"""
        items = service.get_all_receipts()
        return templates.TemplateResponse(request=request, name="index.html", context={"items": items})

    @app.get("/{file_stem}", response_class=HTMLResponse)
    async def read_item(
        request: Request,
        file_stem: str,
        service: ReceiptService = Depends(get_receipt_service),
    ):
        """詳細ページを表示"""
        try:
            detail = service.get_receipt_detail(file_stem)
            return templates.TemplateResponse(
                request=request, name="detail.html", context={"file_stem": file_stem, "fields": detail["fields"]}
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")

    @app.put("/{file_stem}")
    async def update_item(
        file_stem: str,
        request: Request,
        service: ReceiptService = Depends(get_receipt_service),
    ):
        """修正処理"""
        # リクエストボディから修正値を取得（JSONとFormデータの両方に対応）
        content_type = request.headers.get("content-type", "")
        updates = {}
        if "application/json" in content_type:
            try:
                json_data = await request.json()
                for key, value in json_data.items():
                    if key in ["name", "clinic", "amount", "date"]:
                        updates[key] = value
            except Exception:
                pass
        else:
            form_data = await request.form()
            for key, value in form_data.items():
                if key in ["name", "clinic", "amount", "date"]:
                    updates[key] = value

        try:
            result = service.update_receipt(file_stem, updates)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")

        # 座標未検出フィールドがある場合、エラー情報をレスポンスに含める
        coord_errors = result.get("coord_errors", [])

        # 更新後の値を含むHTML断片またはエラー情報を返却
        if coord_errors:
            return templates.TemplateResponse(
                request=request,
                name="coord_error.html",
                context={
                    "file_stem": file_stem,
                    "coord_errors": coord_errors,
                    "updated_data": result["data"],
                },
            )

        return {"status": "updated", "data": result["data"]}

    return app
