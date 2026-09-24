#! /usr/bin/python3
import argparse
import json
import subprocess as subp
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any


def size_fmt(num, suffix="B", binary=True):
    """Metric post-fix notation for orders of magnitude"""
    div = 1024.0 if binary else 1000.0
    if binary and num > 1024:
        suffix = "i" + suffix

    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < div:
            return f"{num:3.2f}{unit}{suffix}"
        num /= div
    return f"{num:.2f}Y{suffix}"


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

    def summary(self) -> str:
        tot_size = size_fmt(self.total_bytes_processed)
        added_size = size_fmt(self.data_added_packed)
        res = f"- Snapshot Size: {tot_size} (unpacked)\n"
        res += f"- Bytes Added: {added_size}\n"
        res += f"- Files Added: {self.files_new} - Changed: {self.files_changed}\n"
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
        ret += self.summary.summary() + "\n"
        return ret


@dataclass(frozen=True)
class ForgetResult:
    """Details of a forget action, including kept/removed snapshots"""

    removed: list[Snapshot]
    kept: list[tuple[Snapshot, list[str]]]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ForgetResult":
        kept_snaps = [Snapshot.from_json(s) for s in data.get("keep", [])]
        kept_reasons: list[list[str]] = [s for s in data.get("reasons", [])]
        out_data = {
            "removed": [Snapshot.from_json(s) for s in data.get("remove", [])],
            "kept": list(zip(kept_snaps, kept_reasons)),
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
                repr_str += f"- {snap} - kept as: {', '.join(reasons)}"
        return repr_str


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


class ResticError(ValueError):
    restic_errs = MappingProxyType(
        {
            0: "Command was successful",
            1: "Command failed, see command help for more details",
            2: "Go runtime error",
            3: "backup could not read some source data, or forget could not remove one or more snapshots",
            10: "Repository does not exist (since restic 0.17.0)",
            11: "Failed to lock repository (since restic 0.17.0)",
            12: "Wrong password (since restic 0.17.1)",
            130: "Command was cancelled (e.g. SIGINT or SIGTERM)",
        }
    )

    def __init__(self, restic_err_json: str):
        """Build error from restic return"""
        try:
            lines = restic_err_json.splitlines()
            if len(lines) > 1:
                self.other_msgs = "\n".join(lines[:-1])

            obj = json.loads(lines[-1])
            self.type = obj["message_type"]
            self.code = obj["code"]
            self.message = obj["message"]
            self.sys_message = self.restic_errs[self.code]
        except ValueError:
            self.message = restic_err_json
            self.type = "Unknown Error Format"
            self.code = -1

    def __str__(self):
        return f"ERROR Code: {self.code} - {self.message}"

    def full_message(self):
        return (
            f"Code: {self.code} - {self.type}\n{self.message}\n"
            + f"Other Codes:\n{self.other_msgs}"
        )


class EZRestic:
    """Wraps Restic CLI, uses JSON output"""

    def __init__(self, pass_file: str | Path):
        self.pass_file = str(pass_file)

    def init_repo(self, dest_repo) -> bool:
        """Returns none if successful, or error"""
        res = self._call_restic(dest_repo, ["init"])
        # Result:
        # {'message_type': 'initialized',
        # 'id': 'ce25a3a09a...',
        # 'repository': './example'}
        if isinstance(res, ResticError):
            print(res)
            return False
        print(f"Successfully initiated repo at {dest_repo}")
        return True

    def backup_to(self, dest_repo: str, source_dir: str) -> None:
        """Calls restic to backup a source to a single destination"""
        self._check_repo_exists(dest_repo)

        res = self._call_restic(dest_repo, ["backup", source_dir])
        if isinstance(res, ResticError):
            raise res

        line = res.stdout.decode().splitlines()[-1]
        summary = SnapshotStats(**json.loads(line))
        print("Success -", summary.summary())

    def get_snapshots(self, repo, filters=None) -> list[Snapshot]:
        """Fetches Snapshots stored on the specified repo"""
        if not filters:
            filters = []
        res = self._call_restic(repo, ["snapshots", *filters])
        if isinstance(res, ResticError):
            raise res

        obj = json.loads(res.stdout.decode())
        return [Snapshot.from_json(snap) for snap in obj]

    def do_check(self, repo) -> CheckResult:
        res = self._call_restic(repo, ["check"])
        if isinstance(res, ResticError):
            raise res
        obj = json.loads(res.stdout.decode("utf-8"))
        return CheckResult.from_json(obj)

    def do_forget(self, repo: str, weekly=4, monthly=6) -> ForgetResult:
        """Delete old backups according to a keep-scheme"""
        # NOTE: grouping by host removes path grouping.
        # Restic seems to expect backups from multiple sources to be dumped in 1 repo,
        # I have seperate repos for each and don't want them seperated by path
        # (mostly to make immich prune properly)
        res = self._call_restic(repo, [f"--keep-weekly {weekly}", "--keep-monthly {monthly}", "--group-by host"])
        if isinstance(res, ResticError):
            raise res
        obj = json.loads(res.stdout.decode("utf-8"))
        return ForgetResult.from_json(obj)

    def do_prune(self, repo:str)->str:
        """Runs prune - returns raw output. TODO: No good way to parse it currently."""
        res = self._call_restic(repo, ["prune"])
        if isinstance(res, ResticError):
            raise res
        return res.stdout.decode("utf-8")
        
            

    def _check_repo_exists(self, dest_repo: str):
        """Raises exception if repo does not exist"""
        res = self._call_restic(dest_repo, ["cat", "config"])
        if isinstance(res, ResticError):
            raise res

    def _call_restic(self, repo: str, cmd: list[str] | str):
        if isinstance(cmd, str):
            cmd = [cmd]
        cmd_list = [
            "restic",
            *cmd,
            "--password-file",
            self.pass_file,
            "--repo",
            repo,
            "--json",
        ]
        res = subp.run(cmd_list, capture_output=True, check=False)
        if res.returncode != 0:
            return ResticError(res.stderr.decode())
        return res


@dataclass
class Source:
    """Represents a backup to be done, as stored in config.
    One source, multiple desitnations, with pre/post actions"""

    name: str
    source_dir: str
    dests: list[str]
    preaction: str | None = None
    postaction: str | None = None
    ignore_files: list[str] = field(default_factory=list)

    @classmethod
    def from_toml(cls, name, data) -> "Source":
        """constructs a source from it's Toml representation in the config file"""
        ignore = data.pop("ignore_file", None)
        if ignore:
            ignore = Path(data["source_dir"]).joinpath(ignore).absolute()
            data["ignore_files"] = [str(ignore)]
        return cls(name, **data)

    def _call_action(self, action: str) -> bool:
        """runs pre/post action"""
        subp.run(action.split(), check=True)
        return True

    def run_backups(self, restic_instance: EZRestic):
        """Runs preaction, backups, and postaction"""
        try:
            if self.preaction:
                self._call_action(self.preaction)
            for dest in self.dests:
                restic_instance.backup_to(dest, self.source_dir)
            if self.postaction:
                self._call_action(self.postaction)
        except subp.CalledProcessError as e:
            print(f"Error {e.returncode}: {e.output}")
        except ResticError as e:
            print(e)

    @classmethod
    def get_by_name(cls, name: str, source_list: Iterable["Source"]) -> "Source":
        "finds source object with matching name in list"
        source = next(filter(lambda s: s.name == name, source_list), None)
        if source is None:
            raise ValueError(f"Source not found: {name}")
        return source


def load_config(config_file: Path) -> tuple[list[Source], Path]:
    """Loads toml comfig file from the specified path,
    returning source objects and the restic password path"""

    with open(config_file, "rb") as tml:
        config = tomllib.load(tml)
    pass_file = config.pop("restic_pass_file")
    pass_file = config_file.parent.joinpath(pass_file).absolute()

    src: dict[str, dict] = config.pop("src")
    sources: list[Source] = []
    for name, data in src.items():
        sources.append(Source.from_toml(name, data))

    return sources, pass_file


def run_all_backups(args, sources: Iterable[Source], restic: EZRestic):
    """called by 'run' command"""
    if args.source:
        source = Source.get_by_name(args.source, sources)
        source.run_backups(restic)
        return

    for source in sources:
        source.run_backups(restic)


def get_status(args, sources: Iterable[Source], restic: EZRestic):
    """called by 'status' command. gets status(es) of repo(s)"""
    start_index = 0 if args.verbose else -3
    if args.source:
        source = Source.get_by_name(args.source, sources)
        for dest in source.dests:
            print(f"---Dest: {dest}---")
            snaps = restic.get_snapshots(dest)
            _ = [print(s) for s in snaps[start_index:]]
    if args.dest:
        restic.get_snapshots(args.dest)
        _ = [print(s) for s in snaps[start_index:]]


def parse_args():
    """Build arg-parser and parse args"""
    parser = argparse.ArgumentParser(prog="EZ-Restic")
    subs = parser.add_subparsers(title="cmd")
    run = subs.add_parser("run", description="Runs the backup")
    run.add_argument(
        "--source", "-s", type=str, help="Status of all destinations for a source"
    )
    run.set_defaults(func=run_all_backups)
    status = subs.add_parser("status", description="Prints to status of restic repo(s)")
    status.add_argument(
        "--source", "-s", type=str, help="Status of all destinations for a source"
    )
    status.add_argument("--dest", "-d", type=str, help="Status of a single repo")
    status.add_argument(
        "--verbose", "-v", action="store_true", help="Status of a single repo"
    )
    status.set_defaults(func=get_status)

    args = parser.parse_args()
    return args


def main():
    """Main Entrypoint"""
    config_file = Path("backups.toml")
    sources, pass_file = load_config(config_file)

    restic = EZRestic(pass_file)

    args = parse_args()
    # run the specified function, providing the restic instance and sources
    args.func(args, sources, restic)


if __name__ == "__main__":
    sys.exit(main())
