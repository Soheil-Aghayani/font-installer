"""Safe, platform-aware font installation primitives.

The Qt window is intentionally kept out of this module so the file operation
and validation logic can be tested without starting a GUI.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterable
from typing import Literal

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only available on Windows
    winreg = None


FONT_EXTENSIONS = frozenset({".ttf", ".otf"})
FONT_SIGNATURES = frozenset({b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"})
FontScope = Literal["user", "system"]


@dataclass
class OperationResult:
    """Result of a file operation, including optional undo metadata."""

    changed: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    history_id: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class IndexedFont:
    path: Path
    digest: str


class FontIndex:
    """Persistent size/mtime/hash index for fast library rescans."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = Path(cache_path)

    def scan(
        self,
        asset_dir: Path,
        digest_func: Callable[[Path], str],
    ) -> list[IndexedFont]:
        paths = FontManager.list_font_files(asset_dir)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self.cache_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS font_files (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    digest TEXT NOT NULL
                )
                """
            )

            records: list[IndexedFont] = []
            path_keys: list[str] = []
            for path in paths:
                resolved_path = path.resolve()
                path_key = str(resolved_path)
                try:
                    stat = resolved_path.stat()
                except OSError:
                    continue

                row = connection.execute(
                    "SELECT size, mtime_ns, digest FROM font_files WHERE path = ?",
                    (path_key,),
                ).fetchone()
                if row and row[0] == stat.st_size and row[1] == stat.st_mtime_ns:
                    digest = row[2]
                else:
                    try:
                        digest = digest_func(resolved_path)
                    except OSError:
                        continue
                    connection.execute(
                        """
                        INSERT INTO font_files(path, size, mtime_ns, digest)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            size = excluded.size,
                            mtime_ns = excluded.mtime_ns,
                            digest = excluded.digest
                        """,
                        (path_key, stat.st_size, stat.st_mtime_ns, digest),
                    )
                path_keys.append(path_key)
                records.append(IndexedFont(resolved_path, digest))

            current_keys = set(path_keys)
            cached_keys = {
                row[0] for row in connection.execute("SELECT path FROM font_files")
            }
            for stale_key in cached_keys - current_keys:
                connection.execute("DELETE FROM font_files WHERE path = ?", (stale_key,))
            connection.commit()
        return records


class BackupStore:
    """Persist reversible operation history and backup removed files."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.backup_root = self.data_dir / "backups"
        self.history_file = self.data_dir / "history.json"

    def begin(self, action: str) -> dict:
        return {
            "id": uuid.uuid4().hex,
            "action": action,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": [],
            "undone": False,
        }

    def backup_file(self, record: dict, original: Path) -> Path:
        backup_dir = self.backup_root / record["id"]
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{len(record['items']):04d}_{original.name}"
        shutil.copy2(original, backup_path)
        return backup_path

    @staticmethod
    def add_created(record: dict, path: Path, digest: str, scope: FontScope) -> None:
        record["items"].append(
            {
                "kind": "created",
                "path": str(path),
                "digest": digest,
                "scope": scope,
            }
        )

    @staticmethod
    def add_removed(
        record: dict,
        original: Path,
        backup: Path,
        digest: str,
        scope: FontScope | None,
    ) -> None:
        record["items"].append(
            {
                "kind": "removed",
                "path": str(original),
                "backup": str(backup),
                "digest": digest,
                "scope": scope,
            }
        )

    def records(self) -> list[dict]:
        if not self.history_file.exists():
            return []
        try:
            with self.history_file.open("r", encoding="utf-8") as history_file:
                records = json.load(history_file)
            return records if isinstance(records, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def commit(self, record: dict) -> str | None:
        if not record["items"]:
            return None
        records = self.records()
        records.append(record)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary_file = self.history_file.with_name(f".{self.history_file.name}.tmp")
        try:
            with temporary_file.open("w", encoding="utf-8") as history_file:
                json.dump(records, history_file, indent=2, ensure_ascii=False)
            os.replace(temporary_file, self.history_file)
        finally:
            temporary_file.unlink(missing_ok=True)
        return record["id"]

    def latest_undoable(self) -> dict | None:
        for record in reversed(self.records()):
            if not record.get("undone", False):
                return record
        return None

    def mark_undone(self, record_id: str) -> None:
        records = self.records()
        for record in records:
            if record.get("id") == record_id:
                record["undone"] = True
                break
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary_file = self.history_file.with_name(f".{self.history_file.name}.tmp")
        try:
            with temporary_file.open("w", encoding="utf-8") as history_file:
                json.dump(records, history_file, indent=2, ensure_ascii=False)
            os.replace(temporary_file, self.history_file)
        finally:
            temporary_file.unlink(missing_ok=True)


class FontManager:
    """Provide safe font discovery and installation for the host platform."""

    def __init__(
        self,
        platform_name: str | None = None,
        user_font_dir: Path | None = None,
        system_font_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.platform_name = platform_name or platform.system()

        if self.platform_name == "Windows":
            windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
            local_app_data = Path(
                os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
            )
            default_system_dir = windir / "Fonts"
            default_user_dir = local_app_data / "Microsoft/Windows/Fonts"
        elif self.platform_name == "Darwin":
            default_system_dir = Path("/Library/Fonts")
            default_user_dir = Path.home() / "Library/Fonts"
        else:
            default_system_dir = Path("/usr/share/fonts")
            default_user_dir = Path.home() / ".local/share/fonts"

        self.user_font_dir = Path(user_font_dir) if user_font_dir else default_user_dir
        self.system_font_dir = Path(system_font_dir) if system_font_dir else default_system_dir
        self.data_dir = Path(data_dir) if data_dir else self.default_data_dir(self.platform_name)
        self.backup_store = BackupStore(self.data_dir)
        self._installed_index: dict[FontScope, dict[str, Path]] | None = None
        self._digest_cache: dict[Path, tuple[int, int, str]] = {}

    @staticmethod
    def default_data_dir(platform_name: str | None = None) -> Path:
        """Return the per-user directory for caches, history, and backups."""

        current_platform = platform_name or platform.system()
        if current_platform == "Windows":
            root = Path(
                os.environ.get(
                    "LOCALAPPDATA",
                    os.environ.get("APPDATA", str(Path.home() / "AppData/Local")),
                )
            )
        elif current_platform == "Darwin":
            root = Path.home() / "Library/Application Support"
        else:
            root = Path(
                os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
            )
        return root / "soheil-os" / "font-installer"

    def directory(self, scope: FontScope) -> Path:
        """Return the destination directory for ``user`` or ``system``."""

        normalized = scope.lower()
        if normalized == "user":
            return self.user_font_dir
        if normalized == "system":
            return self.system_font_dir
        raise ValueError("scope must be 'user' or 'system'")

    def all_font_directories(self) -> tuple[Path, ...]:
        """Return unique system and user directories in stable order."""

        directories: list[Path] = []
        for directory in (self.user_font_dir, self.system_font_dir):
            if directory not in directories:
                directories.append(directory)
        return tuple(directories)

    @staticmethod
    def is_font_file(path: Path) -> bool:
        return path.is_file() and not path.is_symlink() and path.suffix.lower() in FONT_EXTENSIONS

    @staticmethod
    def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def file_digest(self, path: Path) -> str:
        """Return a hash reused while a file's size and mtime stay unchanged."""

        resolved_path = Path(path).resolve()
        stat = resolved_path.stat()
        cached = self._digest_cache.get(resolved_path)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2]
        digest = self.sha256(resolved_path)
        self._digest_cache[resolved_path] = (stat.st_size, stat.st_mtime_ns, digest)
        return digest

    @classmethod
    def list_font_files(cls, asset_dir: Path) -> list[Path]:
        """Return all font files in stable order, including exact duplicates."""

        asset_dir = Path(asset_dir)
        if not asset_dir.exists():
            return []
        try:
            candidates = asset_dir.rglob("*")
            return sorted(
                (path for path in candidates if cls.is_font_file(path)),
                key=lambda path: str(path).casefold(),
            )
        except OSError:
            return []

    @classmethod
    def indexed_fonts(
        cls,
        asset_dir: Path,
        cache_path: Path | None = None,
    ) -> list[IndexedFont]:
        """Return all asset fonts and their hashes, using a persistent cache when set."""

        if cache_path is not None:
            try:
                return FontIndex(Path(cache_path)).scan(Path(asset_dir), cls.sha256)
            except (OSError, sqlite3.Error):
                # A locked or unavailable cache should never make the catalog
                # unusable; fall back to a direct scan for this operation.
                pass

        records: list[IndexedFont] = []
        for path in cls.list_font_files(asset_dir):
            try:
                records.append(IndexedFont(path, cls.sha256(path)))
            except OSError:
                continue
        return records

    @classmethod
    def discover_all_fonts(
        cls,
        asset_dir: Path,
        cache_path: Path | None = None,
    ) -> list[Path]:
        """Find all asset fonts without hiding duplicate copies."""

        return [record.path for record in cls.indexed_fonts(asset_dir, cache_path)]

    @classmethod
    def discover_fonts(
        cls,
        asset_dir: Path,
        cache_path: Path | None = None,
    ) -> list[Path]:
        """Find fonts recursively, removing only byte-identical duplicates.

        The previous implementation deduplicated by filename stem. That can
        hide unrelated fonts that happen to share a filename such as
        ``Regular.ttf``.
        """

        fonts: list[Path] = []
        seen_digests: set[str] = set()
        for record in cls.indexed_fonts(asset_dir, cache_path):
            if record.digest in seen_digests:
                continue
            seen_digests.add(record.digest)
            fonts.append(record.path)
        return fonts

    @classmethod
    def duplicate_groups(
        cls,
        asset_dir: Path,
        cache_path: Path | None = None,
    ) -> dict[str, list[Path]]:
        """Return groups of asset files with byte-identical contents."""

        groups: dict[str, list[Path]] = defaultdict(list)
        for record in cls.indexed_fonts(asset_dir, cache_path):
            groups[record.digest].append(record.path)
        return {digest: paths for digest, paths in groups.items() if len(paths) > 1}

    @classmethod
    def deduplicate_fonts(cls, paths: Iterable[Path]) -> list[Path]:
        """Deduplicate an arbitrary font path collection by file content."""

        fonts: list[Path] = []
        seen_digests: set[str] = set()
        for path in sorted((Path(item) for item in paths), key=lambda item: str(item).lower()):
            if not cls.is_font_file(path):
                continue
            try:
                digest = cls.sha256(path)
            except OSError:
                continue
            if digest in seen_digests:
                continue
            seen_digests.add(digest)
            fonts.append(path)
        return fonts

    @staticmethod
    def validate_font_file(path: Path) -> tuple[bool, str]:
        """Perform inexpensive structural checks before copying a font."""

        if path.suffix.lower() not in FONT_EXTENSIONS:
            return False, "unsupported extension"
        try:
            if path.stat().st_size < 12:
                return False, "file is too small"
            with path.open("rb") as source:
                signature = source.read(4)
        except OSError as error:
            return False, str(error)
        if signature not in FONT_SIGNATURES:
            return False, "unrecognized TrueType/OpenType signature"
        return True, "ok"

    def installed_paths(self, source: Path) -> dict[FontScope, Path]:
        """Find installed copies with the same filename as ``source``."""

        index = self._build_installed_index()
        filename = source.name.casefold()
        return {
            scope: paths[filename]
            for scope, paths in index.items()
            if filename in paths
        }

    def invalidate_installed_index(self) -> None:
        """Clear the installed-font index after an external operation."""

        self._installed_index = None
        self._digest_cache.clear()

    def _build_installed_index(self) -> dict[FontScope, dict[str, Path]]:
        if self._installed_index is not None:
            return self._installed_index

        index: dict[FontScope, dict[str, Path]] = {"user": {}, "system": {}}
        for scope in ("user", "system"):
            directory = self.directory(scope)
            if not directory.exists():
                continue
            for candidate in directory.rglob("*"):
                if self.is_font_file(candidate):
                    index[scope].setdefault(candidate.name.casefold(), candidate)
        self._installed_index = index
        return index

    @staticmethod
    def _copy_atomically(source: Path, destination: Path) -> None:
        """Copy a file beside its destination, then publish it atomically."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _commit_history(self, record: dict, result: OperationResult) -> None:
        try:
            result.history_id = self.backup_store.commit(record)
        except (OSError, TypeError, ValueError) as error:
            result.warnings.append(f"Undo history could not be saved: {error}")

    def install(
        self,
        sources: Iterable[Path],
        scope: FontScope = "user",
        should_cancel: Callable[[], bool] | None = None,
    ) -> OperationResult:
        """Install fonts atomically and refuse same-name/different-content conflicts."""

        scope = scope.lower()
        destination_dir = self.directory(scope)
        result = OperationResult()
        history = self.backup_store.begin("install")

        for source in sources:
            if should_cancel and should_cancel():
                result.cancelled = True
                break

            source = Path(source)
            valid, reason = self.validate_font_file(source)
            if not valid:
                result.failed.append((source, reason))
                continue

            destination = destination_dir / source.name
            try:
                source_digest = self.file_digest(source)
            except OSError as error:
                result.failed.append((source, str(error)))
                continue

            existing = self.installed_paths(source).get(scope)
            same_copy = False
            if existing is not None:
                try:
                    same_copy = self.file_digest(existing) == source_digest
                except OSError:
                    same_copy = False
            if same_copy:
                result.skipped.append((source, "already installed"))
                continue

            if destination.exists() or destination.is_symlink():
                result.failed.append(
                    (source, f"destination already contains a different file: {destination}")
                )
                continue

            try:
                self._copy_atomically(source, destination)

                if self.platform_name == "Windows":
                    self._activate_windows_font(destination, scope, result)
                BackupStore.add_created(history, destination, source_digest, scope)
                result.changed.append(destination)
            except PermissionError as error:
                result.failed.append((source, f"permission denied: {error}"))
            except OSError as error:
                result.failed.append((source, str(error)))

            if result.changed and result.changed[-1] == destination and self._installed_index is not None:
                self._installed_index[scope][destination.name.casefold()] = destination

        if result.changed and self.platform_name == "Windows":
            self._broadcast_font_change()
        self._commit_history(history, result)
        self.invalidate_installed_index()
        return result

    def uninstall(
        self,
        source: Path,
        scope: FontScope | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> OperationResult:
        """Remove only copies whose content matches the selected asset font."""

        return self.uninstall_many([source], scope, should_cancel)

    def uninstall_many(
        self,
        sources: Iterable[Path],
        scope: FontScope | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> OperationResult:
        """Remove matching installed copies in one undoable operation."""

        result = OperationResult()
        scopes = (scope.lower(),) if scope else ("user", "system")
        for selected_scope in scopes:
            self.directory(selected_scope)
        history = self.backup_store.begin("uninstall")

        for source in sources:
            if should_cancel and should_cancel():
                result.cancelled = True
                break

            source = Path(source)
            try:
                source_digest = self.file_digest(source)
            except OSError as error:
                result.failed.append((source, str(error)))
                continue

            found_candidate = False
            for selected_scope in scopes:
                if should_cancel and should_cancel():
                    result.cancelled = True
                    break

                destination = self.directory(selected_scope) / source.name
                if not destination.is_file() or destination.is_symlink():
                    continue
                found_candidate = True

                try:
                    installed_digest = self.file_digest(destination)
                except OSError as error:
                    result.failed.append((destination, str(error)))
                    continue

                if source_digest != installed_digest:
                    result.failed.append(
                        (destination, "refused: installed file differs from the asset copy")
                    )
                    continue

                backup_path: Path | None = None
                try:
                    backup_path = self.backup_store.backup_file(history, destination)
                    if self.platform_name == "Windows":
                        self._deactivate_windows_font(destination, selected_scope, result)
                    destination.unlink()
                    BackupStore.add_removed(
                        history,
                        destination,
                        backup_path,
                        installed_digest,
                        selected_scope,
                    )
                    result.backups.append(backup_path)
                    result.changed.append(destination)
                except PermissionError as error:
                    result.failed.append((destination, f"permission denied: {error}"))
                except OSError as error:
                    result.failed.append((destination, str(error)))
                finally:
                    if backup_path is not None and backup_path not in result.backups:
                        try:
                            backup_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            if result.cancelled:
                break
            if not found_candidate:
                result.skipped.append((source, "no matching installed copy found"))

        if result.changed and self.platform_name == "Windows":
            self._broadcast_font_change()
        self._commit_history(history, result)
        self.invalidate_installed_index()
        return result

    def remove_asset_files(
        self,
        sources: Iterable[Path],
        should_cancel: Callable[[], bool] | None = None,
    ) -> OperationResult:
        """Remove selected asset copies after backing them up for undo."""

        result = OperationResult()
        history = self.backup_store.begin("remove-assets")

        for source in sources:
            if should_cancel and should_cancel():
                result.cancelled = True
                break

            source = Path(source)
            if not source.exists() and not source.is_symlink():
                result.skipped.append((source, "already absent"))
                continue
            if not self.is_font_file(source):
                result.failed.append((source, "not a regular TTF or OTF file"))
                continue

            backup_path: Path | None = None
            try:
                source_digest = self.file_digest(source)
                backup_path = self.backup_store.backup_file(history, source)
                source.unlink()
                BackupStore.add_removed(history, source, backup_path, source_digest, None)
                result.backups.append(backup_path)
                result.changed.append(source)
            except PermissionError as error:
                result.failed.append((source, f"permission denied: {error}"))
                if backup_path is not None:
                    try:
                        backup_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            except OSError as error:
                result.failed.append((source, str(error)))
                if backup_path is not None:
                    try:
                        backup_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        self._commit_history(history, result)
        return result

    def undo_last(self, should_cancel: Callable[[], bool] | None = None) -> OperationResult:
        """Undo the latest operation whose changes have not already been undone."""

        result = OperationResult()
        record = self.backup_store.latest_undoable()
        if record is None:
            result.skipped.append((Path("history"), "no undoable operation found"))
            return result

        for item in reversed(record.get("items", [])):
            if should_cancel and should_cancel():
                result.cancelled = True
                break

            if not isinstance(item, dict):
                result.failed.append((Path("history"), "invalid history item"))
                continue

            try:
                item_path = Path(item["path"])
                expected_digest = str(item["digest"])
            except (KeyError, TypeError, ValueError) as error:
                result.failed.append((Path("history"), f"invalid history item: {error}"))
                continue

            kind = item.get("kind")
            if kind == "created":
                if not item_path.exists() and not item_path.is_symlink():
                    result.skipped.append((item_path, "already absent"))
                    continue
                if item_path.is_symlink() or not item_path.is_file():
                    result.failed.append((item_path, "refused: destination is not a regular file"))
                    continue
                try:
                    if self.file_digest(item_path) != expected_digest:
                        result.failed.append((item_path, "refused: file changed after installation"))
                        continue
                    scope = item.get("scope")
                    if self.platform_name == "Windows" and scope in {"user", "system"}:
                        self._deactivate_windows_font(item_path, scope, result)
                    item_path.unlink()
                    result.changed.append(item_path)
                except PermissionError as error:
                    result.failed.append((item_path, f"permission denied: {error}"))
                except OSError as error:
                    result.failed.append((item_path, str(error)))
            elif kind == "removed":
                backup_value = item.get("backup")
                if not backup_value:
                    result.failed.append((item_path, "backup path is missing"))
                    continue
                backup_path = Path(backup_value)
                if not backup_path.is_file() or backup_path.is_symlink():
                    result.failed.append((item_path, "backup file is missing"))
                    continue

                try:
                    if self.file_digest(backup_path) != expected_digest:
                        result.failed.append((item_path, "refused: backup file changed"))
                        continue
                    if item_path.exists() or item_path.is_symlink():
                        if item_path.is_symlink() or not item_path.is_file():
                            result.failed.append((item_path, "refused: restore destination is not a file"))
                            continue
                        if self.file_digest(item_path) == expected_digest:
                            result.skipped.append((item_path, "already restored"))
                        else:
                            result.failed.append((item_path, "refused: restore destination contains different content"))
                        continue

                    self._copy_atomically(backup_path, item_path)
                    scope = item.get("scope")
                    if self.platform_name == "Windows" and scope in {"user", "system"}:
                        self._activate_windows_font(item_path, scope, result)
                    result.changed.append(item_path)
                except PermissionError as error:
                    result.failed.append((item_path, f"permission denied: {error}"))
                except OSError as error:
                    result.failed.append((item_path, str(error)))
            else:
                result.failed.append((item_path, f"unknown history action: {kind}"))

        if not result.failed and not result.cancelled:
            try:
                self.backup_store.mark_undone(record["id"])
                result.history_id = record["id"]
            except (OSError, TypeError, ValueError, KeyError) as error:
                result.warnings.append(f"Undo state could not be saved: {error}")

        if result.changed and self.platform_name == "Windows":
            self._broadcast_font_change()
        self.invalidate_installed_index()
        return result

    def history_records(self) -> list[dict]:
        """Return persisted operation history for display in the UI."""

        return self.backup_store.records()

    def validate(self, sources: Iterable[Path]) -> OperationResult:
        result = OperationResult()
        for source in sources:
            valid, reason = self.validate_font_file(Path(source))
            if valid:
                result.changed.append(Path(source))
            else:
                result.failed.append((Path(source), reason))
        return result

    def refresh_font_cache(self, scope: FontScope | None = None) -> str | None:
        """Refresh the Linux font cache when the platform provides ``fc-cache``."""

        if self.platform_name != "Linux":
            return None
        executable = shutil.which("fc-cache")
        if executable is None:
            return None

        directories = (self.directory(scope),) if scope else self.all_font_directories()
        for directory in directories:
            if not directory.exists():
                continue
            try:
                completed = subprocess.run(
                    [executable, "-f", str(directory)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                return f"font cache refresh timed out for {directory}"
            except OSError as error:
                return f"font cache refresh failed: {error}"
            if completed.returncode != 0:
                return f"font cache refresh failed for {directory}"
        return None

    def _activate_windows_font(
        self,
        destination: Path,
        scope: FontScope,
        result: OperationResult,
    ) -> None:
        try:
            loaded = ctypes.windll.gdi32.AddFontResourceW(str(destination))
            if loaded <= 0:
                result.warnings.append(f"Windows session did not load {destination.name}")
        except OSError as error:
            result.warnings.append(f"Windows font notification failed for {destination.name}: {error}")

        if winreg is None:
            return
        try:
            root = winreg.HKEY_CURRENT_USER if scope == "user" else winreg.HKEY_LOCAL_MACHINE
            key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
            with winreg.CreateKeyEx(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self._registry_name(destination), 0, winreg.REG_SZ, destination.name)
        except OSError as error:
            result.warnings.append(f"Windows registry update failed for {destination.name}: {error}")

    def _deactivate_windows_font(
        self,
        destination: Path,
        scope: FontScope,
        result: OperationResult,
    ) -> None:
        try:
            ctypes.windll.gdi32.RemoveFontResourceW(str(destination))
        except OSError as error:
            result.warnings.append(f"Windows session cleanup failed for {destination.name}: {error}")

        if winreg is None:
            return
        try:
            root = winreg.HKEY_CURRENT_USER if scope == "user" else winreg.HKEY_LOCAL_MACHINE
            key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
            with winreg.OpenKey(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self._registry_name(destination))
        except OSError:
            # The file can still be safely removed if an older version did not
            # create the matching registry value.
            pass

    @staticmethod
    def _registry_name(path: Path) -> str:
        font_type = "OpenType" if path.suffix.lower() == ".otf" else "TrueType"
        return f"{path.stem} ({font_type})"

    @staticmethod
    def _broadcast_font_change() -> None:
        try:
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
        except OSError:
            pass
