import argparse
import json
import subprocess as subp
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
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
    def from_json(cls, data) -> "SnapshotStats":
        t_format = lambda str: datetime.fromisoformat(str)
        data = {
            **data,
            "backup_start": t_format(data["backup_start"]),
            "backup_end": t_format(data["backup_end"]),
        }
        return cls(**data)

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
        del data["short_id"]
        data = {
            **data,
            "time": datetime.fromisoformat(data["time"]),
            "summary": SnapshotStats.from_json(data["summary"]),
            "id": ID(data["id"]),
        }
        return cls(**data)

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
        res += f"- Restore Size: {restore_sz_fmt}"
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
        del data["message_type"]
        return cls(**data)

    def is_good(self) -> bool:
        good = (self.num_errors == 0) and len(self.broken_packs) == 0
        good = good and not (self.suggest_prune)
        return good


@dataclass(frozen=True)
class ResticError(ValueError):
    restic_errs: ClassVar[dict[int, str]] = {
        -1: "Unknown Error",
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
        return self.restic_errs[self.code]

    @classmethod
    def from_cli(cls, res: subp.CompletedProcess[bytes]):
        """Build error from restic return"""
        text = res.stderr.decode()
        lines = text.splitlines()
        try:
            obj = json.loads(lines[-1])
            del obj["message_type"]
            obj = {**obj, "ext_message": "\n".join(lines[:-1])}
            if obj["code"] == 10:
                # This is a code smell, it should probably
                # be in some 'restic_error_factory' or some bs like
                # that, but I'm not about that life, it's fine
                return ResticRepoNotFoundError(**obj)
            return cls(**obj)
        except ValueError:
            return cls(-1, text, "")

    def __str__(self):
        return (
            f"Code: {self.code} - {self.sys_message}\n"
            + f"{self.message}\n"
            + (f"Other Codes:\n{self.ext_message}" if self.ext_message else "")
        )


class ResticRepoNotFoundError(ResticError):
    def __str__(self):
        return f"{self.message}\n" + (
            f"Other Codes:\n{self.ext_message}" if self.ext_message else ""
        )


@dataclass(frozen=True)
class EZRestic:
    """Wraps Restic CLI, parsing the CLI's JSON output into clean objects"""

    pass_file: str
    restic_path: str

    def init_repo(self, dest_repo) -> bool:
        """Returns True if successful, or errors if it fails"""
        _ = self._call_restic(dest_repo, ["init"])
        # Result:
        # {'message_type': 'initialized',
        # 'id': 'ce25a3a09a...',
        # 'repository': './example'}
        print(f"Successfully initiated repo at {dest_repo}")
        return True

    def get_snapshots(self, repo, filters=None) -> list[Snapshot]:
        """Fetches Snapshots stored on the specified repo"""
        if not filters:
            filters = []
        res = self._call_restic(repo, ["snapshots", *filters])
        obj = json.loads(res.stdout.decode())
        return [Snapshot.from_json(snap) for snap in obj]

    def get_stats(self, repo) -> RepoStatsResult:
        res = self._call_restic(repo, ["stats"])
        res_size = self._call_restic(repo, ["stats", "--mode", "raw-data"])
        obj: dict = json.loads(res.stdout.decode())
        sz_obj: dict = json.loads(res_size.stdout.decode())
        sz_obj["stored_size"] = sz_obj.pop("total_size")
        obj |= sz_obj
        return RepoStatsResult.from_json(obj)

    def do_backup(self, dest_repo: str, source_dir: Path) -> SnapshotStats:
        """Calls restic to backup a source to a single destination"""
        self._check_repo_exists(dest_repo)

        res = self._call_restic(dest_repo, ["backup", str(source_dir.absolute())])
        line = res.stdout.decode().splitlines()[-1]
        data = json.loads(line)
        for key in ["total_duration", "message_type", "snapshot_id"]:
            del data[key]
        return SnapshotStats.from_json(data)

    def do_check(self, repo) -> CheckResult:
        """Check repo status, returns suggusted prune/repair. May throw ResticError"""
        res = self._call_restic(repo, ["check"])
        obj = json.loads(res.stdout.decode("utf-8"))
        return CheckResult.from_json(obj)

    def do_forget(self, repo: str, weekly=4, monthly=6) -> ForgetResult:
        """Delete old backups according to a keep-scheme"""
        # NOTE: grouping by host removes path grouping.
        # Restic seems to expect backups from multiple sources to be dumped in 1 repo,
        # I have seperate repos for each and don't want them seperated by path
        # (mostly to make immich prune properly)
        res = self._call_restic(
            repo,
            [
                "forget",
                "--keep-weekly",
                str(weekly),
                "--keep-monthly",
                str(monthly),
                "--group-by",
                "host",
            ],
        )
        obj = json.loads(res.stdout.decode("utf-8"))
        return ForgetResult.from_json(obj)

    def do_prune(self, repo: str) -> str:
        """Runs prune - returns raw output. TODO: No good way to parse it currently."""
        res = self._call_restic(repo, ["prune"])
        return res.stdout.decode("utf-8")

    def repo_stats_printout(
        self, repo: str, include_recent_snaps: bool | int = True
    ) -> str:
        stats = self.get_stats(repo)
        if include_recent_snaps:
            snaps = self.get_snapshots(repo)
        res = f"--- Repo: {repo} ---\n"
        res += str(stats)
        if include_recent_snaps:
            res += "\n"
            # cannot check isinstance(_, int) as bool subclasses int
            if not isinstance(include_recent_snaps, bool):
                snaps = snaps[-include_recent_snaps:]
            res += "\n".join(str(s) for s in snaps)
        return res

    def _check_repo_exists(self, dest_repo: str) -> bool:
        """Raises exception if repo does not exist"""
        try:
            _ = self._call_restic(dest_repo, ["cat", "config"])
        except ResticError as e:
            if e.code == 10:
                return False
            raise
        return True

    def _call_restic(self, repo: str, cmd: list[str] | str):
        if isinstance(cmd, str):
            cmd = [cmd]
        full_cmd = [
            "restic",
            *cmd,
            "--password-file",
            self.pass_file,
            "--repo",
            repo,
            "--json",
        ]
        res = subp.run(full_cmd, capture_output=True, check=False)
        if res.returncode != 0:
            raise ResticError.from_cli(res)
        return res


@dataclass(frozen=True)
class Source:
    """Represents a backup to be done, as stored in config.
    One source, multiple desitnations, with pre/post actions"""

    name: str
    source_dir: Path
    dests: list[str]
    preaction: list[str] | None = None
    postaction: list[str] | None = None
    ignore_files: list[Path] = field(default_factory=list)

    @classmethod
    def from_toml(cls, name, data: dict[str, Any]) -> "Source":
        """constructs a source from it's Toml representation in the config file"""
        data = {
            **data,
            "source_dir": Path(data["source_dir"]).absolute(),
        }
        ignore = data.pop("ignore_file", None)
        if ignore:
            ignore = data["source_dir"].joinpath(ignore).resolve()
            data["ignore_files"] = [str(ignore)]
        return cls(name, **data)

    def run_backups(self, restic_instance: EZRestic, prune=False):
        """Runs preaction, backups, and postaction for this source.
        Pre/post actions are run with source dir as the working dir."""
        try:
            if self.preaction:
                self._call_shell_action(self.preaction, self.source_dir)
            for dest in self.dests:
                try:
                    snap_stats = restic_instance.do_backup(dest, self.source_dir)
                    if prune:
                        prune_res = restic_instance.do_forget(dest)
                        restic_instance.do_prune(dest)
                    stats = restic_instance.get_stats(dest)
                    print("Success:", dest)
                    print(snap_stats.short_print())
                    if prune:
                        print(f"- pruned {len(prune_res.removed)} snapshots")
                    print("- Snapshots Stored:", stats.snapshots_count)
                    print("- Repo Size:", size_fmt(stats.stored_size))
                except ResticError as e:
                    print("FAILED to backup:", dest)
                    print(e)
                print()
            if self.postaction:
                self._call_shell_action(self.postaction, self.source_dir)
        except subp.CalledProcessError as e:
            print(f"Error {e.returncode}: {e.output}")

    def get_repo_statuses(self, restic: EZRestic, **kwargs) -> str:
        """returns a string printout of the status of each configured repo."""
        return "\n".join(restic.repo_stats_printout(d, **kwargs) for d in self.dests)

    def _call_shell_action(self, action: list[str], working_dir=None) -> bool:
        """runs pre/post action (space delimited )"""
        subp.run(action, check=True, cwd=working_dir)
        return True


@dataclass(frozen=True)
class BackupList:
    restic_inst: EZRestic
    sources: MappingProxyType[str, Source]

    @classmethod
    def from_config(cls, config_text: str) -> "BackupList":
        config = tomllib.loads(config_text)
        pass_file = config.pop("restic_pass_file")
        restic_path = config.pop("restic_path", "restic")
        pass_file = str(Path(pass_file).absolute())

        restic = EZRestic(pass_file, restic_path)

        src: dict[str, dict] = config.pop("src")
        sources: dict[str, Source] = {}
        for name, data in src.items():
            sources[name] = Source.from_toml(name, data)
        return cls(restic, MappingProxyType(sources))

    def run_all_backups(self):
        """called by 'run' command"""
        for source in self.sources.values():
            source.run_backups(self.restic_inst)


def get_status(args, backups: BackupList):
    """called by 'status' command. gets status(es) of repo(s)"""
    snapshot_ct = True if args.verbose else 3
    if args.source and (s := backups.sources.get(args.source)):
        s.get_repo_statuses(backups.restic_inst, include_recent_snapshots=snapshot_ct)
    if args.dest:
        snaps = backups.restic_inst.get_snapshots(args.dest)
        _ = [print(s) for s in snaps[snapshot_ct:]]


def parse_args():
    """Build arg-parser and parse args"""
    parser = argparse.ArgumentParser(prog="EZ-Restic")
    parser.add_argument("--config-file", type=Path)
    subs = parser.add_subparsers(title="cmd")

    # Run subcommand
    run = subs.add_parser("backup", description="Run a backup")
    run_group = run.add_mutually_exclusive_group(required=True)
    run_group.add_argument(
        "--source",
        "-s",
        type=str,
        help="backs up only a single repo from config",
    )
    run_group.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="backs up all repos listed in the config",
    )
    run.add_argument(
        "--no-prune",
        action="store_true",
        help="skip the pruning stage after backup",
    )
    run.set_defaults(id="backup")

    # status subcommand
    status = subs.add_parser(
        "status",
        description="Prints to status of restic repo(s)",
    )
    status.add_argument(
        "--source",
        "-s",
        type=str,
        help="Status of all destinations for a source",
    )
    status.add_argument(
        "--dest",
        "-d",
        type=str,
        help="Status of a single repo",
    )
    status.add_argument(
        "--verbose",
        "-v",
        action="store_true",
    )
    status.set_defaults(id="status")
    args = parser.parse_args()
    return args


def main():
    """Main Entrypoint"""
    args = parse_args()

    if args.config_file:
        config_file = args.config_file
    else:
        config_file = Path("backups.toml")
    backups = BackupList.from_config(config_file.read_text())

    # run the specified function, providing the restic instance and sources
    match args.id:
        case "backup":
            if args.all:
                backups.run_all_backups()
            else:
                backups.sources[args.source].run_backups(
                    backups.restic_inst,
                    prune=(not args.no_prune),
                )
        case "status":
            get_status(args, backups)
        case _:
            backups.run_all_backups()


if __name__ == "__main__":
    sys.exit(main())
