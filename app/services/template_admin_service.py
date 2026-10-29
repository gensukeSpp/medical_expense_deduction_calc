"""Template admin service: manual coord_basis toggle (topmost -> date)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.coord_normalizer import shift_template_to_date_basis
from app.db import (
    get_all_templates_with_names,
    get_clinic_by_id,
    get_latest_template_by_clinic,
    update_template_basis,
)


class ClinicNotFoundError(Exception):
    """Raised when the target clinic_id does not exist (-> 404)."""


class TemplateBasisConflictError(Exception):
    """Raised when the template cannot be migrated to date basis (-> 409)."""


class InvalidBasisError(ValueError):
    """Raised when the requested basis value is unsupported (-> 400)."""


class TemplateAdminService:
    """Manage clinic templates via the Web admin UI (Issue #42)."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else None

    def list_templates(self) -> List[Dict[str, Any]]:
        """Return the latest template per clinic with admin display fields."""
        if not self.db_path:
            raise RuntimeError("DB が設定されていません")
        templates = get_all_templates_with_names(self.db_path)
        for t in templates:
            coords = t.get("coords_corrections") or {}
            t["date_box_learned"] = coords.get("date") is not None
            t["basis"] = t.get("coord_basis") or "topmost"
        return templates

    def toggle_basis(self, clinic_id: str, basis: str) -> Dict[str, Any]:
        """Manually migrate the clinic's latest template to date basis.

        Raises:
            InvalidBasisError: basis is not 'date'.
            ClinicNotFoundError: clinic_id does not exist.
            TemplateBasisConflictError: no template / date box unlearned.
            RuntimeError: DB not configured.
        """
        if not self.db_path:
            raise RuntimeError("DB が設定されていません")
        if basis != "date":
            raise InvalidBasisError(f"未対応の basis: {basis!r} (サポート: 'date')")

        if get_clinic_by_id(self.db_path, clinic_id) is None:
            raise ClinicNotFoundError(f"clinic not found: {clinic_id}")

        template = get_latest_template_by_clinic(self.db_path, clinic_id)
        if template is None:
            raise TemplateBasisConflictError("テンプレート未学習のため切替できません")
        if (template.get("coord_basis") or "topmost") == "date":
            return {"status": "noop", "reason": "already_date"}

        coords = template.get("coords_corrections") or {}
        new_coords = shift_template_to_date_basis(coords)
        if new_coords is None:
            raise TemplateBasisConflictError("date box 未学習のため切替できません")

        migrated = update_template_basis(
            self.db_path,
            clinic_id,
            new_coords,
            changed_fields=list(coords.keys()),
            history_reason="manual_basis_toggle",
        )
        if not migrated:
            # 競合: 直前に他経路で移行済みの場合（冪等に扱う）
            return {"status": "noop", "reason": "already_date"}
        return {"status": "updated", "coord_basis": "date"}
