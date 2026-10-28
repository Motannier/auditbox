"""Validation and compensating filesystem operations for uploaded content."""

from __future__ import annotations

import zipfile
from pathlib import Path
from uuid import uuid4


class StorageValidationError(ValueError):
    pass


def validate_uploaded_file(path: Path, suffix: str) -> None:
    """Perform lightweight content checks instead of trusting Content-Type alone."""
    suffix = suffix.casefold()
    with path.open("rb") as source:
        header = source.read(16)
    if suffix == ".pdf" and not header.startswith(b"%PDF-"):
        raise StorageValidationError("上传内容不是有效的 PDF 文件")
    if suffix in {".docx", ".xlsx", ".pptx"}:
        if not zipfile.is_zipfile(path):
            raise StorageValidationError("上传内容不是有效的 Office Open XML 文件")
        expected = {
            ".docx": "word/",
            ".xlsx": "xl/",
            ".pptx": "ppt/",
        }[suffix]
        with zipfile.ZipFile(path) as archive:
            if not any(name.startswith(expected) for name in archive.namelist()):
                raise StorageValidationError("上传文件扩展名与实际 Office 类型不一致")
    if suffix in {".txt", ".md", ".csv", ".html", ".htm"}:
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise StorageValidationError("文本类知识文件必须使用 UTF-8 编码") from exc


def checked_storage_path(value: object, root: Path) -> Path | None:
    """Resolve a stored path only when it remains below the upload root."""
    if not isinstance(value, str) or not value:
        return None
    storage_root = root.resolve()
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(storage_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class FileStaging:
    """Reversibly move files aside until the related DB mutation succeeds."""

    def __init__(self, root: Path):
        self.trash = root / ".trash" / uuid4().hex
        self.moves: list[tuple[Path, Path]] = []

    def stage(self, paths: list[Path]) -> None:
        unique = {path.resolve() for path in paths if path.is_file()}
        if not unique:
            return
        self.trash.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(sorted(unique)):
            target = self.trash / f"{index:04d}_{source.name}"
            source.replace(target)
            self.moves.append((source, target))

    def rollback(self) -> None:
        for source, target in reversed(self.moves):
            if target.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
        self._remove_empty_trash()

    def commit(self) -> None:
        for _, target in self.moves:
            target.unlink(missing_ok=True)
        self._remove_empty_trash()

    def _remove_empty_trash(self) -> None:
        if self.trash.exists():
            try:
                self.trash.rmdir()
                self.trash.parent.rmdir()
            except OSError:
                pass
