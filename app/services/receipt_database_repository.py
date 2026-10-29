"""Receipt database repository: handles database operations for receipts."""

from pathlib import Path
from typing import Dict, Any, Optional

from app.db import (
    insert_receipt,
    get_receipt,
    add_correction,
    get_receipt_by_source_path,
    get_or_create_clinic,
    get_clinic_by_id,
    get_db_connection,
    get_latest_template_by_clinic,
)


def _coerce_receipt_lookup_candidates(file_stem: str, file_path: Path) -> list[str]:
    candidates = [file_stem]
    candidates.append(str(file_path))
    candidates.append(str(file_path.stem))
    candidates.append(str(file_path.with_suffix("")))
    return list(dict.fromkeys(candidates))


class ReceiptDatabaseRepository:
    """Repository for handling database operations related to receipts."""

    def __init__(self, db_path: Optional[Path]):
        """
        Initialize the repository.

        Args:
            db_path: Path to the SQLite database, or None if no database is used.
        """
        self.db_path = db_path

    def is_available(self) -> bool:
        """
        Check if database operations are available.

        Returns:
            True if db_path is set, False otherwise.
        """
        return self.db_path is not None

    def get_receipt_by_id(self, receipt_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a receipt by its ID.

        Args:
            receipt_id: The UUID string of the receipt.

        Returns:
            Receipt data as a dictionary, or None if not found.
        """
        if not self.is_available():
            return None
        return get_receipt(self.db_path, receipt_id)

    def get_receipt_by_source_path(self, source_path: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a receipt by its source path.

        Args:
            source_path: The source path stored in the receipts table.

        Returns:
            Receipt data as a dictionary, or None if not found.
        """
        if not self.is_available():
            return None
        return get_receipt_by_source_path(self.db_path, source_path)

    def insert_receipt(
        self,
        receipt_id: str,
        source_path: str,
        ocr_json: Optional[Dict[str, Any]],
        normalized_json: Optional[Dict[str, Any]],
        clinic_id: Optional[str] = None,
    ) -> None:
        """
        Insert a new receipt record.

        Args:
            receipt_id: UUID string for the receipt.
            source_path: Path of the source image or PDF.
            ocr_json: Raw OCR data as dict.
            normalized_json: Normalized data as dict.
            clinic_id: Optional clinic ID.
        """
        if not self.is_available():
            return
        insert_receipt(self.db_path, receipt_id, source_path, ocr_json, normalized_json, clinic_id)

    def add_correction(
        self,
        receipt_id: str,
        field_name: str,
        old_value: Optional[str],
        new_value: Optional[str],
    ) -> None:
        """
        Add a correction record for a receipt.

        Args:
            receipt_id: ID of the receipt.
            field_name: The field name being corrected.
            old_value: Old value before correction.
            new_value: New corrected value.
        """
        if not self.is_available():
            return
        add_correction(self.db_path, receipt_id, field_name, old_value, new_value)

    def get_or_create_clinic(self, name: str) -> str:
        """
        Get or create a clinic by name.

        Args:
            name: Name of the clinic.

        Returns:
            The clinic ID.
        """
        if not self.is_available():
            raise RuntimeError("Database is not available")
        return get_or_create_clinic(self.db_path, name)

    def get_clinic_by_id(self, clinic_id: str) -> Optional[Dict[str, Any]]:
        """Get a clinic by its ID, or None if it does not exist.

        Args:
            clinic_id: The clinic ID.

        Returns:
            Clinic dict with keys id, name, created_at, or None.
        """
        if not self.is_available():
            return None
        return get_clinic_by_id(self.db_path, clinic_id)

    def get_latest_template_by_clinic(self, clinic_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest template for a clinic.

        Args:
            clinic_id: The clinic ID.

        Returns:
            Template data as a dictionary, or None if not found.
        """
        if not self.is_available():
            return None
        return get_latest_template_by_clinic(self.db_path, clinic_id)

    def update_receipt_clinic_id(self, receipt_id: str, clinic_id: str) -> None:
        """
        Update the clinic_id for a receipt.

        Args:
            receipt_id: The receipt ID.
            clinic_id: The new clinic ID.
        """
        if not self.is_available():
            return
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE receipts SET clinic_id = ? WHERE id = ?",
                    (clinic_id, receipt_id),
                )
        finally:
            conn.close()

    def apply_receipt_updates(
        self,
        file_stem: str,
        file_path: Path,
        old_data: Dict[str, Any],
        updated_data: Dict[str, Any],
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Apply all database updates for a receipt correction in a single call.

        Handles receipt record creation/retrieval, correction insertion,
        and clinic ID resolution. Returns feedback metadata for downstream
        coordinate processing.

        Args:
            file_stem: The stem of the file being updated.
            file_path: Full path to the structured data file.
            old_data: Previous data before updates.
            updated_data: Data after normalization.
            updates: The raw updates applied.

        Returns:
            Dict with keys:
                - receipt_id: str or None
                - clinic_id: str or None
                - error: str or None
        """
        receipt_id: Optional[str] = None
        clinic_id: Optional[str] = None

        if not self.is_available():
            return {"receipt_id": None, "clinic_id": None, "error": "Database not available"}

        try:
            # Get or create receipt record.
            # Web updates often pass the file stem, while the persisted receipt row may
            # already exist under the same stem or a source-path-based identifier.
            receipt = None
            for candidate in _coerce_receipt_lookup_candidates(file_stem, file_path):
                receipt = self.get_receipt_by_id(candidate)
                if receipt:
                    break
                receipt = self.get_receipt_by_source_path(str(file_path))
                if receipt:
                    break

            if receipt:
                receipt_id = receipt["id"]
                clinic_id = receipt.get("clinic_id")
            else:
                receipt_id = file_stem
                source_path = str(file_path)
                self.insert_receipt(
                    receipt_id=receipt_id,
                    source_path=source_path,
                    ocr_json=None,
                    normalized_json=old_data,
                    clinic_id=None,
                )

            # Add corrections for each updated field
            for field_name, new_value in updates.items():
                old_value = old_data.get(field_name)
                self.add_correction(
                    receipt_id=receipt_id,
                    field_name=field_name,
                    old_value=str(old_value) if old_value is not None else None,
                    new_value=str(new_value) if new_value is not None else None,
                )

            # Update clinic ID when clinic information exists, even if the value was
            # already present before and only another field was corrected.
            current_clinic = updated_data.get("clinic")
            old_clinic = old_data.get("clinic")
            # クリニックIDの更新は、「クリニック名が実際に変更された場合（current_clinic != old_clinic）」\n
            # または「レシートが新規作成された場合（receipt is None）」、\n
            # または「既存のクリニックIDが NULL の場合」のみに限定する
            if (
                (current_clinic != old_clinic)
                or (receipt is None and current_clinic)
                or (clinic_id is None and current_clinic)
            ):
                if current_clinic:
                    try:
                        clinic_id = self.get_or_create_clinic(str(current_clinic))
                    except RuntimeError:
                        clinic_id = None
                else:
                    clinic_id = None
                self.update_receipt_clinic_id(receipt_id, clinic_id)

        except Exception as e:
            return {"receipt_id": receipt_id, "clinic_id": clinic_id, "error": str(e)}

        return {"receipt_id": receipt_id, "clinic_id": clinic_id, "error": None}
