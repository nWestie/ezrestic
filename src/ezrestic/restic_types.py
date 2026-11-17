import json
import subprocess as subp
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar


def size_fmt(num, suffix="B", binary=True):
    """Metric post-fix notation for orders of magnitude"""
    div = 1024.0 if binary else 1000.0
    if binary and num > 1024:
        suffix = "i" + suffix

    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < div:
            return f"{num:.3g}{unit}{suffix}"
        num /= div
    return f"{num:.3g}Y{suffix}"


def _load_json(text: str | bytes) -> dict[str, Any]:
    """Parse the final JSON object emitted by a restic command."""
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ResticParseError("Restic Response is Invalid JSON", str(e)) from e


def _load_json_lines(text: str | bytes) -> list[dict]:
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return [json.loads(line) for line in text.splitlines()]
    except json.JSONDecodeError as e:
        raise ResticParseError("Restic Response is Invalid JSON", str(e)) from e


@dataclass(frozen=True)
class ID:
    "An ID string that displays a shortened"

    id: str

    def __str__(self) -> str:
        return self.id[:8]

    def __repr__(self) -> str:
        return self.id


@dataclass(frozen=True)
class SnapshotStats:
    """Contains extended detail about a single snapshot. See fields at https://restic.readthedocs.io/en/stable/075_scripting.html#backup"""

    backup_start: datetime  # Time at which the backup was started
    backup_end: datetime  # Time at which the backup was completed
    files_new: int  # Number of new files
    files_changed: int  # Number of files that changed
    files_unmodified: int  # Number of files that did not change
    dirs_new: int  # Number of new directories
    dirs_changed: int  # Number of directories that changed
    dirs_unmodified: int  # Number of directories that did not change
    data_blobs: int  # Number of data blobs added
    tree_blobs: int  # Number of tree blobs added
    data_added: int  # Amount of (uncompressed) data added, in bytes
    data_added_packed: int  # Amount of data added (after compression), in bytes
    total_files_processed: int  # Total number of files processed
    total_bytes_processed: int  # Total number of bytes processed

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SnapshotStats":
        data = {
            **data,
            "backup_start": datetime.fromisoformat(data["backup_start"]),
            "backup_end": datetime.fromisoformat(data["backup_end"]),
        }
        return cls(**data)

    @classmethod
    def from_backup_output(cls, output: bytes | str) -> "SnapshotStats":
        try:
            data = _load_json_lines(output.strip())
            summary = next(d for d in data if d.get("message_type") == "summary")
            for key in ("total_duration", "message_type", "snapshot_id"):
                summary.pop(key, None)
            return cls.from_json(summary)
        except (KeyError, TypeError, ValueError, StopIteration) as e:
            raise ResticParseError("Failed to parse backup result", str(e)) from e

    def short_print(self) -> str:
        tot_size = size_fmt(self.total_bytes_processed)
        added_size = size_fmt(self.data_added_packed)
        res = f"- Snapshot Size: {tot_size} (unpacked)\n"
        res += f"- Bytes Added: {added_size}\n"
        res += f"- Files Added: {self.files_new} - Changed: {self.files_changed}"
        return res


@dataclass(frozen=True)
class Snapshot:
    """Represents a specific "snapshot" - a single instance of backup history"""

    id: ID  # Snapshot ID
    summary: SnapshotStats  # Snapshot statistics
    time: datetime  # Timestamp of when the backup was started
    tree: str  # ID of the root tree blob
    paths: list[str]  # List of paths included in the backup
    hostname: str  # Hostname of the backed up machine
    username: str  # Username the backup command was run as
    program_version: str  # restic version used to create snapshot
    parent: str | None = None  # ID of the parent snapshot
    uid: int | None = None  # ID of owner
    gid: int | None = None  # ID of group
    # List of paths and globs excluded from the backup
    excludes: list[str] = field(default_factory=list)
    # List of tags for the snapshot in question
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data) -> "Snapshot":
        data = {
            **data,
            "time": datetime.fromisoformat(data["time"]),
            "summary": SnapshotStats.from_json(data["summary"]),
            "id": ID(data["id"]),
        }
        data.pop("short_id", None)
        return cls(**data)

    @classmethod
    def list_from_output(cls, output: bytes | str) -> list["Snapshot"]:
        try:
            value = _load_json(output)
            if not isinstance(value, list):
                raise TypeError("Expected a JSON array")
            return [cls.from_json(snapshot) for snapshot in value]
        except (KeyError, TypeError, ValueError) as e:
            raise ResticParseError("Failed to parse snapshots result", str(e)) from e

    def __repr__(self) -> str:
        ret = f"Snapshot {self.id}: {self.time.strftime('%Y-%m-%d %H:%M')}\n"
        ret += f"- {self.program_version}\n"
        ret += f"- paths: {', '.join(self.paths)}\n"
        if self.tags:
            ret += f"- tags: {', '.join(self.tags)}\n"
        ret += self.summary.short_print()
        return ret


@dataclass(frozen=True)
class ForgetResult:
    """Details of a forget action, including kept/removed snapshots"""

    removed: list[Snapshot]
    kept: list[tuple[Snapshot, list[str]]]

    @classmethod
    def from_json(cls, data: list[dict[str, Any]]) -> "ForgetResult":
        kept_snaps = []
        kept_reasons: list[list[str]] = []
        removed: list[Snapshot] = []
        for block in data:
            kept_snaps.extend(
                [Snapshot.from_json(s) for s in (block.get("keep") or [])]
            )
            kept_reasons.extend([s["matches"] for s in (block.get("reasons") or [])])
            removed.extend([Snapshot.from_json(s) for s in (block.get("remove") or [])])
        out_data = {
            "kept": list(zip(kept_snaps, kept_reasons)),
            "removed": removed,
        }
        return cls(**out_data)

    @classmethod
    def from_output(cls, output: bytes | str) -> "ForgetResult":
        try:
            value = _load_json(output)

            if not isinstance(value, list):
                raise TypeError("Expected a JSON array")
            return cls.from_json(value)
        except (KeyError, TypeError, ValueError) as e:
            raise ResticParseError("Failed to parse forget result", str(e)) from e

    def __repr__(self) -> str:
        repr_str = ""
        if len(self.removed):
            repr_str += "removed:\n"
            for snap in self.removed:
                repr_str += "-" + str(snap) + "\n"

        if len(self.kept):
            repr_str += "Keeping:\n"
            for snap, reasons in self.kept:
                repr_str += f"- Snapshot {snap.id}\n   kept as: {', '.join(reasons)}\n"
        return repr_str


@dataclass(frozen=True)
class RepoStatsResult:
    total_size: int  # Repository size in bytes
    total_file_count: int  # Number of blobs in the repository
    snapshots_count: int  # Number of processed snapshots
    stored_size: float
    total_uncompressed_size: int
    compression_ratio: float
    compression_progress: float
    compression_space_saving: float
    total_blob_count: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RepoStatsResult":
        return cls(**data)

    @classmethod
    def from_stats_output(
        cls, stats_output: bytes | str, raw_data_output: bytes | str
    ) -> "RepoStatsResult":
        try:
            stats = _load_json(stats_output)
            if isinstance(raw_data_output, bytes):
                raw_data_output = raw_data_output.decode("utf-8")
            raw_data = json.loads(raw_data_output)
            raw_data["stored_size"] = raw_data.pop("total_size")
            return cls.from_json(stats | raw_data)
        except (KeyError, TypeError, ValueError) as e:
            raise ResticParseError("Failed to parse stats result", str(e)) from e

    def __repr__(self) -> str:
        restore_sz_fmt = size_fmt(self.total_size)
        stored_sz_fmt = size_fmt(self.stored_size)
        files_format = size_fmt(
            self.total_file_count,
            suffix="",
            binary=False,
        )
        res = f"- Repository Size: {stored_sz_fmt}\n"
        res += f"- Compression: {self.compression_ratio:.2g}x\n"
        res += f"- Snapshots: {self.snapshots_count}\n"
        res += f"- Files: {files_format}\n"
        res += f"- Restore Size: {restore_sz_fmt}\n"
        return res


@dataclass(frozen=True)
class CheckResult:
    """Output of the check command, includes recommended actions"""

    num_errors: int  # Number of errors
    suggest_repair_index: bool  # Run "restic repair index"
    suggest_prune: bool  # Run "restic prune"
    # Run "restic repair packs ID…" and "restic repair snapshots –forget" to remove damaged files
    broken_packs: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CheckResult":
        data = {**data}
        data.pop("message_type", None)
        if data.get("broken_packs") is None:
            data["broken_packs"] = []
        return cls(**data)

    @classmethod
    def from_output(cls, output: bytes | str) -> "CheckResult":
        try:
            check = _load_json(output)
            return cls.from_json(check)
        except (KeyError, TypeError, ValueError) as e:
            raise ResticParseError("Failed to parse check result", str(e)) from e

    def is_good(self) -> bool:
        good = (self.num_errors == 0) and len(self.broken_packs) == 0
        good = good and not (self.suggest_prune)
        return good


@dataclass(frozen=True)
class ResticError(ValueError):
    restic_errs: ClassVar[dict[int, str]] = {
        0: "Command was successful",
        1: "Command failed, see command help for more details",
        2: "Go runtime error",
        3: "backup could not read some source data, or forget could not remove one or more snapshots",
        10: "Repository does not exist",
        11: "Failed to lock repository",
        12: "Wrong password",
        130: "Command was cancelled (e.g. SIGINT or SIGTERM)",
    }
    code: int
    message: str
    ext_message: str

    @property
    def sys_message(self):
        return self.restic_errs.get(self.code, "Unknown Error")

    @classmethod
    def from_process(cls, res: subp.CompletedProcess[bytes]):
        """Build error from restic return"""
        text = res.stderr.decode("utf-8", errors="replace")
        try:
            lines = text.strip().splitlines()
            obj = json.loads(lines[-1])
            if not isinstance(obj, dict):
                raise TypeError("Expected a JSON object")
            obj.pop("message_type", None)
            obj = {**obj, "ext_message": "\n".join(lines[:-1])}
            if obj["code"] == 10:
                # This is a code smell, it should probably
                # be in some 'restic_error_factory' or some bs like
                # that, but I'm not about that life, it's fine
                return ResticRepoNotFoundError(**obj)
            return cls(**obj)
        except (KeyError, TypeError, ValueError, IndexError):
            return ResticParseError("Failed to Build Restic Error", text)

    def __str__(self):
        return (
            f"Code: {self.code} - {self.sys_message}\n"
            + f"{self.message}\n"
            + (f"Other Codes:\n{self.ext_message}" if self.ext_message else "")
        )


class ResticParseError(ResticError):
    """Restic output was successful but did not match the expected schema."""

    def __init__(self, message: str, ext_message: str = ""):
        super().__init__(-1, message, ext_message)


class ResticCommandError(ResticError):
    """The Restic executable could not be started or invoked."""

    def __init__(self, message: str, ext_message: str = ""):
        super().__init__(-1, message, ext_message)


class ResticRepoNotFoundError(ResticError):
    def __str__(self):
        return f"{self.message}\n" + (
            f"Other Codes:\n{self.ext_message}" if self.ext_message else ""
        )
