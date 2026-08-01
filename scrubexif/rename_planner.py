"""Bounded, collision-safe planning for batch filename changes."""

from __future__ import annotations

import math
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .renaming import resolve_rename

DEFAULT_MAX_PLAN_FILES = 250_000
DEFAULT_MAX_PLAN_SECONDS = 1_800.0
DEFAULT_MAX_PLAN_BYTES = 512 * 1024 * 1024
DEFAULT_SQLITE_CACHE_KIB = 16 * 1024
DEFAULT_PROGRESS_SECONDS = 5.0
MAX_COLLISION_REROLLS = 3

_COMMIT_INTERVAL = 1_000
_REROLL_TOKEN_PATTERN = re.compile(r"%(?:r\d*|u)")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class RenamePlanningError(RuntimeError):
    """Raised when a collision-safe rename plan cannot be completed.

    Args:
        message: Human-readable planning failure.
    """


@dataclass(frozen=True)
class RenamePlanLimits:
    """Resource limits for one rename-planning operation.

    Args:
        max_files: Maximum number of unique source files.
        max_seconds: Maximum wall-clock planning duration.
        max_database_bytes: Maximum on-disk SQLite database size.
        sqlite_cache_kib: Maximum SQLite page-cache target in KiB.
        progress_seconds: Minimum interval between progress messages.
    """

    max_files: int = DEFAULT_MAX_PLAN_FILES
    max_seconds: float = DEFAULT_MAX_PLAN_SECONDS
    max_database_bytes: int = DEFAULT_MAX_PLAN_BYTES
    sqlite_cache_kib: int = DEFAULT_SQLITE_CACHE_KIB
    progress_seconds: float = DEFAULT_PROGRESS_SECONDS

    def __post_init__(self) -> None:
        """Validate every resource limit.

        Raises:
            ValueError: If any limit is non-positive.
        """
        integer_values = {
            "max_files": self.max_files,
            "max_database_bytes": self.max_database_bytes,
            "sqlite_cache_kib": self.sqlite_cache_kib,
        }
        for name, value in integer_values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        numeric_values = {
            "max_seconds": self.max_seconds,
            "progress_seconds": self.progress_seconds,
        }
        for name, value in numeric_values.items():
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a positive number")


@dataclass(frozen=True)
class RenamePlanEntry:
    """One validated source-to-destination mapping.

    Args:
        entry_id: Stable database identifier for this mapping.
        source_path: Existing JPEG source path.
        destination_path: Collision-free planned destination.
    """

    entry_id: int
    source_path: Path
    destination_path: Path


class RenamePlan:
    """Disk-backed iterable of validated rename mappings.

    Args:
        database_path: Owned temporary SQLite database.
        count: Number of planned mappings.
        rename_format: Validated format used to create the plan.
        output_directory: Shared output directory, or None for inline mode.
    """

    def __init__(
        self,
        database_path: Path,
        count: int,
        rename_format: str,
        output_directory: Path | None,
    ) -> None:
        """Initialize ownership of a completed plan.

        Args:
            database_path: Owned temporary SQLite database.
            count: Number of planned mappings.
            rename_format: Validated format used to create the plan.
            output_directory: Shared output directory, or None for inline mode.

        Raises:
            ValueError: If the path or count is invalid.
        """
        if not isinstance(database_path, Path):
            raise ValueError("database_path must be a pathlib.Path")
        if not database_path.is_file():
            raise ValueError("database_path must identify an existing file")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("count must be a non-negative integer")
        if not isinstance(rename_format, str) or not rename_format:
            raise ValueError("rename_format must be a non-empty string")
        if output_directory is not None and not isinstance(output_directory, Path):
            raise ValueError("output_directory must be None or a pathlib.Path")
        self._database_path = database_path
        self._count = count
        self._rename_format = rename_format
        self._output_directory = output_directory
        self._connection = sqlite3.connect(database_path)
        self._closed = False

    @property
    def count(self) -> int:
        """Return the number of planned mappings.

        Returns:
            Number of mappings in the plan.
        """
        return self._count

    def __enter__(self) -> RenamePlan:
        """Enter a context that owns the temporary database.

        Returns:
            This rename plan.
        """
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Remove the temporary database when leaving the context.

        Args:
            exc_type: Active exception type, if any.
            exc: Active exception, if any.
            traceback: Active traceback, if any.

        Returns:
            None.
        """
        self.close()

    def __iter__(self) -> Iterator[RenamePlanEntry]:
        """Stream planned mappings in input order.

        Yields:
            Source-to-destination mappings.

        Raises:
            RuntimeError: If the plan has already been closed.
        """
        if self._closed:
            raise RuntimeError("rename plan is closed")
        entry_id = 0
        while True:
            cursor = self._connection.execute(
                """
                SELECT id, source_path, destination_path
                FROM rename_plan
                WHERE id > ?
                ORDER BY id
                LIMIT 1
                """,
                (entry_id,),
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return
            entry_id, source_path, destination_path = row
            yield RenamePlanEntry(entry_id, Path(source_path), Path(destination_path))

    def reassign_destination(
        self,
        entry_id: int,
        occupied_destination: Path,
    ) -> Path:
        """Reserve a new random destination after late filesystem activity.

        Args:
            entry_id: Stable identifier of the affected plan entry.
            occupied_destination: Destination that became occupied.

        Returns:
            Newly reserved collision-free destination.

        Raises:
            RuntimeError: If the plan is closed or the entry is missing.
            ValueError: If an argument is invalid or does not match the plan.
            RenamePlanningError: If the format cannot produce a safe alternative.
            sqlite3.Error: If the plan database cannot be updated.
        """
        if self._closed:
            raise RuntimeError("rename plan is closed")
        if not isinstance(entry_id, int) or isinstance(entry_id, bool) or entry_id <= 0:
            raise ValueError("entry_id must be a positive integer")
        if not isinstance(occupied_destination, Path) or not occupied_destination.name:
            raise ValueError("occupied_destination must be a pathlib.Path with a filename")

        row = self._connection.execute(
            """
            SELECT source_path, source_key, destination_path, counter_start
            FROM rename_plan
            WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"rename plan entry {entry_id} does not exist")
        raw_source_path, source_key, raw_destination_path, counter_start = row
        current_destination = Path(raw_destination_path)
        if _path_key(occupied_destination) != _path_key(current_destination):
            raise ValueError(
                "occupied_destination does not match the entry's current reservation"
            )

        source_path = Path(raw_source_path)
        destination_directory = self._output_directory or source_path.parent

        def is_collision(candidate: Path) -> bool:
            """Check a replacement against filesystem and plan reservations.

            Args:
                candidate: Proposed replacement destination.

            Returns:
                True when the candidate is unavailable.
            """
            candidate_key = _path_key(candidate)
            if candidate_key == source_key:
                reserved = self._connection.execute(
                    "SELECT 1 FROM rename_plan WHERE destination_key = ? AND id != ?",
                    (candidate_key, entry_id),
                ).fetchone()
                return reserved is not None
            if _path_exists(candidate):
                return True
            source_collision = self._connection.execute(
                "SELECT 1 FROM rename_plan WHERE source_key = ? AND id != ?",
                (candidate_key, entry_id),
            ).fetchone()
            if source_collision is not None:
                return True
            destination_collision = self._connection.execute(
                "SELECT 1 FROM rename_plan WHERE destination_key = ? AND id != ?",
                (candidate_key, entry_id),
            ).fetchone()
            return destination_collision is not None

        replacement = resolve_unique_destination(
            self._rename_format,
            source_path,
            destination_directory,
            {"n": counter_start},
            is_collision,
        )
        self._connection.execute(
            """
            UPDATE rename_plan
            SET destination_path = ?, destination_key = ?
            WHERE id = ?
            """,
            (str(replacement), _path_key(replacement), entry_id),
        )
        self._connection.commit()
        return replacement

    def close(self) -> None:
        """Remove all temporary SQLite files owned by this plan.

        Returns:
            None.
        """
        if self._closed:
            return
        self._closed = True
        self._connection.close()
        for suffix in ("", "-journal", "-shm", "-wal"):
            Path(f"{self._database_path}{suffix}").unlink(missing_ok=True)


def _path_key(path: Path) -> str:
    """Return a normalized absolute key for filesystem comparisons.

    Args:
        path: Filesystem path to normalize.

    Returns:
        Normalized absolute path string.
    """
    return os.path.normcase(str(path.absolute()))


def _path_exists(path: Path) -> bool:
    """Check for any existing directory entry, including broken symlinks.

    Args:
        path: Candidate destination path.

    Returns:
        True when a filesystem entry already occupies the path.
    """
    return os.path.lexists(path)


def _can_reroll(rename_format: str, stem: str) -> bool:
    """Determine whether another expansion can produce a new random name.

    Args:
        rename_format: Validated rename format.
        stem: Most recently expanded filename stem.

    Returns:
        True for explicit random tokens or UUID fallback stems.
    """
    return bool(_REROLL_TOKEN_PATTERN.search(rename_format) or _UUID_PATTERN.fullmatch(stem))


def resolve_unique_destination(
    rename_format: str,
    source_path: Path,
    destination_directory: Path,
    counter: dict[str, int],
    is_collision: Callable[[Path], bool],
    max_rerolls: int = MAX_COLLISION_REROLLS,
) -> Path:
    """Resolve one collision-free destination with bounded random retries.

    Args:
        rename_format: Validated rename format.
        source_path: Existing source JPEG.
        destination_directory: Directory that will contain the result.
        counter: Per-invocation sequential-token counter.
        is_collision: Callback returning True for an unavailable destination.
        max_rerolls: Maximum additional random expansions after the first.

    Returns:
        Available destination path.

    Raises:
        ValueError: If an input has the wrong type or value.
        RenamePlanningError: If a collision cannot be resolved.
    """
    if not isinstance(rename_format, str) or not rename_format:
        raise ValueError("rename_format must be a non-empty string")
    if not isinstance(source_path, Path) or not source_path.name:
        raise ValueError("source_path must be a pathlib.Path with a filename")
    if not isinstance(destination_directory, Path):
        raise ValueError("destination_directory must be a pathlib.Path")
    if not isinstance(counter, dict):
        raise ValueError("counter must be a dictionary")
    counter_value = counter.get("n", 0)
    if not isinstance(counter_value, int) or isinstance(counter_value, bool) or counter_value < 0:
        raise ValueError("counter['n'] must be a non-negative integer")
    if not callable(is_collision):
        raise ValueError("is_collision must be callable")
    if not isinstance(max_rerolls, int) or isinstance(max_rerolls, bool) or max_rerolls < 0:
        raise ValueError("max_rerolls must be a non-negative integer")

    initial_counter = counter.get("n", 0)
    for attempt in range(max_rerolls + 1):
        counter["n"] = initial_counter
        stem = resolve_rename(rename_format, source_path, counter)
        candidate = destination_directory / f"{stem}{source_path.suffix}"
        if not is_collision(candidate):
            return candidate
        if not _can_reroll(rename_format, stem):
            counter["n"] = initial_counter
            raise RenamePlanningError(
                f"Rename destination already exists and format cannot be re-rolled: {candidate}"
            )
        if attempt == max_rerolls:
            counter["n"] = initial_counter
            raise RenamePlanningError(
                f"Unable to generate a collision-free name after {max_rerolls} re-rolls: {candidate}"
            )

    raise AssertionError("collision retry loop terminated unexpectedly")


def _database_size(database_path: Path) -> int:
    """Return the total bytes used by SQLite database files.

    Args:
        database_path: Main SQLite database path.

    Returns:
        Combined bytes for database, journal, shared-memory, and WAL files.
    """
    total = 0
    for suffix in ("", "-journal", "-shm", "-wal"):
        candidate = Path(f"{database_path}{suffix}")
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _check_resources(
    database_path: Path,
    count: int,
    started_at: float,
    last_progress_at: float,
    limits: RenamePlanLimits,
    phase: str,
) -> float:
    """Enforce resource limits and optionally emit bounded progress output.

    Args:
        database_path: SQLite database path.
        count: Files processed in the current phase.
        started_at: Monotonic planning start time.
        last_progress_at: Time of the previous progress message.
        limits: Planning resource limits.
        phase: Human-readable planning phase.

    Returns:
        Updated last-progress timestamp.

    Raises:
        RenamePlanningError: If a time or disk limit is exceeded.
    """
    now = time.monotonic()
    elapsed = now - started_at
    database_bytes = _database_size(database_path)
    if elapsed > limits.max_seconds:
        raise RenamePlanningError(
            f"Rename planning exceeded {limits.max_seconds:g} seconds during {phase}. No files were modified."
        )
    if database_bytes > limits.max_database_bytes:
        raise RenamePlanningError(
            "Rename planning temporary database exceeded "
            f"{limits.max_database_bytes // (1024 * 1024)} MiB during {phase}. No files were modified."
        )
    if now - last_progress_at >= limits.progress_seconds:
        print(
            f"ℹ️ Rename planning ({phase}): {count:,} files checked, "
            f"{elapsed:.1f}s elapsed, {database_bytes / (1024 * 1024):.1f} MiB temporary index",
            flush=True,
        )
        return now
    return last_progress_at


def _remove_database(database_path: Path) -> None:
    """Remove a temporary SQLite database and companion files.

    Args:
        database_path: Main SQLite database path.

    Returns:
        None.
    """
    for suffix in ("", "-journal", "-shm", "-wal"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def build_rename_plan(
    source_paths: Iterable[Path],
    rename_format: str,
    output_directory: Path | None,
    counter: dict[str, int],
    limits: RenamePlanLimits,
) -> RenamePlan:
    """Build a complete two-pass rename plan with bounded memory use.

    Args:
        source_paths: Stream of source JPEG paths.
        rename_format: Validated rename format.
        output_directory: Shared output directory, or None for clean-inline.
        counter: Per-invocation sequential-token counter.
        limits: Planning resource limits.

    Returns:
        Owned disk-backed rename plan. Callers must close it.

    Raises:
        ValueError: If an entry-point argument is invalid.
        RenamePlanningError: If planning encounters a collision, resource limit,
            or temporary-storage failure.
    """
    if not isinstance(rename_format, str) or not rename_format:
        raise ValueError("rename_format must be a non-empty string")
    if output_directory is not None and not isinstance(output_directory, Path):
        raise ValueError("output_directory must be None or a pathlib.Path")
    if not isinstance(counter, dict):
        raise ValueError("counter must be a dictionary")
    counter_value = counter.get("n", 0)
    if not isinstance(counter_value, int) or isinstance(counter_value, bool) or counter_value < 0:
        raise ValueError("counter['n'] must be a non-negative integer")
    if not isinstance(limits, RenamePlanLimits):
        raise ValueError("limits must be a RenamePlanLimits instance")

    descriptor, raw_database_path = tempfile.mkstemp(
        prefix="scrubexif-rename-plan-",
        suffix=".sqlite3",
    )
    os.close(descriptor)
    database_path = Path(raw_database_path)
    connection: sqlite3.Connection | None = None
    started_at = time.monotonic()
    last_progress_at = started_at
    count = 0
    print(
        "ℹ️ Planning collision-safe renames before modifying files. "
        "Large directory hierarchies may take some time.",
        flush=True,
    )

    try:
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(f"PRAGMA cache_size=-{limits.sqlite_cache_kib}")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE rename_plan (
                id INTEGER PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_key TEXT NOT NULL UNIQUE,
                destination_path TEXT,
                destination_key TEXT UNIQUE,
                counter_start INTEGER
            )
            """
        )

        for raw_source_path in source_paths:
            if time.monotonic() - started_at > limits.max_seconds:
                raise RenamePlanningError(
                    f"Rename planning exceeded {limits.max_seconds:g} seconds during inventory. "
                    "No files were modified."
                )
            if not isinstance(raw_source_path, Path):
                raise RenamePlanningError("Rename source entries must be pathlib.Path values")
            if raw_source_path.is_symlink() or not raw_source_path.is_file():
                continue
            source_path = raw_source_path.absolute()
            cursor = connection.execute(
                "INSERT OR IGNORE INTO rename_plan(source_path, source_key) VALUES (?, ?)",
                (str(source_path), _path_key(source_path)),
            )
            if cursor.rowcount == 0:
                continue
            count += 1
            if count > limits.max_files:
                raise RenamePlanningError(
                    f"Rename planning exceeded the {limits.max_files:,}-file limit. No files were modified."
                )
            if count % _COMMIT_INTERVAL == 0:
                connection.commit()
                last_progress_at = _check_resources(
                    database_path,
                    count,
                    started_at,
                    last_progress_at,
                    limits,
                    "inventory",
                )

        connection.commit()
        last_progress_at = _check_resources(
            database_path,
            count,
            started_at,
            last_progress_at,
            limits,
            "inventory",
        )

        planned_count = 0
        rows = connection.execute(
            "SELECT id, source_path, source_key FROM rename_plan ORDER BY id"
        )
        for row_id, raw_source_path, source_key in rows:
            if time.monotonic() - started_at > limits.max_seconds:
                raise RenamePlanningError(
                    f"Rename planning exceeded {limits.max_seconds:g} seconds during destinations. "
                    "No files were modified."
                )
            source_path = Path(raw_source_path)
            destination_directory = output_directory or source_path.parent
            counter_start = counter.get("n", 0)

            def is_collision(candidate: Path) -> bool:
                """Check the filesystem and both planning indexes.

                Args:
                    candidate: Proposed destination.

                Returns:
                    True when the destination is unavailable.
                """
                candidate_key = _path_key(candidate)
                if candidate_key == source_key:
                    reserved = connection.execute(
                        "SELECT 1 FROM rename_plan WHERE destination_key = ? AND id != ?",
                        (candidate_key, row_id),
                    ).fetchone()
                    return reserved is not None
                if _path_exists(candidate):
                    return True
                source_collision = connection.execute(
                    "SELECT 1 FROM rename_plan WHERE source_key = ? AND id != ?",
                    (candidate_key, row_id),
                ).fetchone()
                if source_collision is not None:
                    return True
                destination_collision = connection.execute(
                    "SELECT 1 FROM rename_plan WHERE destination_key = ?",
                    (candidate_key,),
                ).fetchone()
                return destination_collision is not None

            destination_path = resolve_unique_destination(
                rename_format,
                source_path,
                destination_directory,
                counter,
                is_collision,
            )
            connection.execute(
                """
                UPDATE rename_plan
                SET destination_path = ?, destination_key = ?, counter_start = ?
                WHERE id = ?
                """,
                (
                    str(destination_path),
                    _path_key(destination_path),
                    counter_start,
                    row_id,
                ),
            )
            planned_count += 1
            if planned_count % _COMMIT_INTERVAL == 0:
                connection.commit()
                last_progress_at = _check_resources(
                    database_path,
                    planned_count,
                    started_at,
                    last_progress_at,
                    limits,
                    "destinations",
                )

        connection.commit()
        _check_resources(
            database_path,
            planned_count,
            started_at,
            last_progress_at,
            limits,
            "destinations",
        )
        connection.close()
        connection = None
        elapsed = time.monotonic() - started_at
        print(
            f"✅ Rename plan ready: {count:,} files in {elapsed:.1f}s "
            f"({_database_size(database_path) / (1024 * 1024):.1f} MiB temporary index)",
            flush=True,
        )
        return RenamePlan(
            database_path,
            count,
            rename_format,
            output_directory,
        )
    except (RenamePlanningError, ValueError):
        if connection is not None:
            connection.close()
        _remove_database(database_path)
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        _remove_database(database_path)
        raise RenamePlanningError(
            f"Rename planning storage failed: {exc}. No files were modified."
        ) from exc
    except Exception:
        if connection is not None:
            connection.close()
        _remove_database(database_path)
        raise
