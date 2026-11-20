import subprocess as subp
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated

import typer

import ezrestic.restic_types as rt


def eprint(*args, **kwargs):
    """Prints to std_error"""
    print(*args, file=sys.stderr, **kwargs)


@dataclass(frozen=True)
class EZRestic:
    """Wraps Restic CLI, parsing the CLI's JSON output into clean objects"""

    restic_path: str

    def _call_on_repo(self, repo: "Repository", cmd: list[str] | str):
        if isinstance(cmd, str):
            cmd = [cmd]
        full_cmd = [
            self.restic_path,
            *cmd,
            "--password-file",
            str(repo.password_file),
            "--repo",
            repo.locator,
            "--json",
        ]
        try:
            res = subp.run(full_cmd, capture_output=True, check=False)
        except OSError as e:
            raise rt.ResticCommandError(
                f"Failed to invoke Restic executable: {self.restic_path}", str(e)
            ) from e
        if res.returncode != 0:
            raise rt.ResticError.from_process(res)
        return res


@dataclass(frozen=True)
class Repository:
    locator: str
    password_file: Path
    restic: EZRestic

    def init_repo(self) -> bool:
        """Builds the remote repo. Returns True if successful, errors if it fails"""
        _ = self.restic._call_on_repo(self, ["init"])
        # Result:
        # {'message_type': 'initialized',
        # 'id': 'ce25a3a09a...',
        # 'repository': './example'}
        print(f"Successfully initiated repo at {self.locator}")
        return True

    def get_snapshots(self, filters=None) -> list[rt.Snapshot]:
        """Fetches Snapshots stored on the specified repo"""
        if not filters:
            filters = []
        res = self.restic._call_on_repo(self, ["snapshots", *filters])
        return rt.Snapshot.list_from_output(res.stdout)

    def get_stats(self) -> rt.RepoStatsResult:
        res = self.restic._call_on_repo(self, ["stats"])
        res_size = self.restic._call_on_repo(self, ["stats", "--mode", "raw-data"])
        return rt.RepoStatsResult.from_stats_output(res.stdout, res_size.stdout)

    def backup(
        self, source_dir: Path, ignore_file: Path | None = None
    ) -> rt.SnapshotStats:
        """Calls restic to backup a source to a single destination"""
        self._check_repo_exists()
        cmd = ["backup", str(source_dir.absolute())]
        if ignore_file:
            cmd.extend(["--exclude-file", str(ignore_file)])
        res = self.restic._call_on_repo(self, cmd)
        return rt.SnapshotStats.from_backup_output(res.stdout)

    def check(self) -> rt.CheckResult:
        """Check repo status, returns suggusted prune/repair. May throw ResticError"""
        res = self.restic._call_on_repo(self, ["check"])
        return rt.CheckResult.from_output(res.stdout)

    def forget(self, weekly=4, monthly=6) -> rt.ForgetResult:
        """Delete old backups according to a keep-scheme"""
        # NOTE: grouping by host removes path grouping.
        # Restic seems to expect backups from multiple sources to be dumped in 1 repo,
        # I have seperate repos for each and don't want them seperated by path
        # (mostly to make immich prune properly)
        res = self.restic._call_on_repo(
            self,
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
        return rt.ForgetResult.from_output(res.stdout)

    def prune(self) -> str:
        """Runs prune - returns raw output. TODO: No good way to parse it currently."""
        res = self.restic._call_on_repo(self, ["prune"])
        return res.stdout.decode("utf-8")

    def repo_stats_printout(self, include_recent_snaps: bool | int = True) -> str:
        res = f"--- Repo: {self.locator} ---\n"
        try:
            stats = self.get_stats()
            # can be true or non-zero int
            if include_recent_snaps:
                snaps = self.get_snapshots()
            else:
                snaps = []
        except rt.ResticError as e:
            return res + str(e)

        res += str(stats)
        if type(include_recent_snaps) == int:
            snaps = snaps[-include_recent_snaps:]
            snaps.reverse()
        res += "\n".join(str(s) for s in snaps)
        return res

    def _check_repo_exists(self) -> bool:
        """Raises exception if repo does not exist"""
        try:
            _ = self.restic._call_on_repo(self, ["cat", "config"])
        except rt.ResticError as e:
            if e.code == 10:
                return False
            raise
        return True


@dataclass(frozen=True)
class Source:
    """Represents a backup to be done, as stored in config.
    One source, multiple desitnations, with pre/post actions"""

    name: str
    source_dir: Path
    dests: tuple[Repository, ...]
    preaction: list[str] | None = None
    postaction: list[str] | None = None
    ignore_file: Path | None = None

    def run_backups(self, prune=False):
        """Runs preaction, backups, and postaction for this source.
        Pre/post actions are run with source dir as the working dir."""
        try:
            if self.preaction:
                self._call_shell_action(self.preaction, self.source_dir)
            for dest in self.dests:
                try:
                    backup_snap_stats = dest.backup(self.source_dir, self.ignore_file)
                    print("Successful Backup:", dest.locator)
                    print(backup_snap_stats.short_print())
                    if prune:
                        prune_res = dest.forget()
                        dest.prune()
                    if prune:
                        print(f"- pruned {len(prune_res.removed)} snapshots")
                    stats = dest.get_stats()
                    print("- Snapshots Stored:", stats.snapshots_count)
                    print("- Repo Size:", rt.size_fmt(stats.stored_size))
                except rt.ResticError as e:
                    print("FAILED to backup:", self.name)
                    print(e)
                print()
            if self.postaction:
                self._call_shell_action(self.postaction, self.source_dir)
        except subp.CalledProcessError as e:
            print(f"Error calling action: {e.args[1]}\n{e.returncode}: {e.output}")

    def all_repo_printouts(self, **kwargs) -> str:
        """returns a string printout of the status of each configured repo."""
        return "\n".join(d.repo_stats_printout(**kwargs) for d in self.dests)

    def _call_shell_action(
        self, action: list[str], working_dir: Path | None = None
    ) -> bool:
        """runs pre/post action (space delimited )"""
        subp.run(action, check=True, cwd=str(working_dir))
        return True


@dataclass(frozen=True)
class BackupList:
    # stored for late-init repos (ex. status)
    restic_inst: EZRestic
    sources: Mapping[str, Source]

    def __post_init__(self):
        object.__setattr__(
            self,
            "sources",
            MappingProxyType(dict(self.sources)),
        )

    @dataclass(frozen=True)
    class LoadingError(Exception):
        message: str

    @classmethod
    def from_config_file(cls, config_file: Path) -> "BackupList":
        try:
            # read config from file
            config_file = config_file.resolve()
            config = tomllib.loads(config_file.read_text())
            config_dir = config_file.parent

            # get global configs
            pass_file = cls._resolve_relative_to(
                config_dir, config.pop("restic_pass_file")
            )
            restic_path = config.pop("restic_path", "restic")
            if restic_path != "restic":
                restic_path = str(cls._resolve_relative_to(config_dir, restic_path))
            restic = EZRestic(str(restic_path))

            # load sources
            src: dict[str, dict] = config.pop("src")
            sources: dict[str, Source] = {}
            for name, data in src.items():
                src_dir = data.pop("source_dir")
                src_dir = cls._resolve_relative_to(config_dir, src_dir)
                ignore_file = data.pop("ignore_file", None)
                if ignore_file:
                    ignore_file = cls._resolve_relative_to(src_dir, ignore_file)
                repositories = [
                    Repository(
                        cls._resolve_repo_locator(locator, config_dir),
                        pass_file,
                        restic,
                    )
                    for locator in data.pop("dests")
                ]
                sources[name] = Source(
                    name=name,
                    source_dir=src_dir,
                    ignore_file=ignore_file,
                    dests=tuple(repositories),
                    **data,
                )
            return cls(restic, MappingProxyType(sources))
        except FileNotFoundError:
            raise BackupList.LoadingError(
                f"No config file found at {config_file.resolve()}"
            )
        except tomllib.TOMLDecodeError as e:
            raise BackupList.LoadingError(f"Failed to load config file:\n{e}")
        except KeyError as e:
            raise BackupList.LoadingError(
                f"Failed to load config file:\nMissing field: {e}"
            )

    @staticmethod
    def _resolve_relative_to(relative_to: Path, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (relative_to / path).resolve()

    @staticmethod
    def _resolve_repo_locator(locator: str, config_dir: Path) -> str:
        path = Path(locator)
        if path.is_absolute() or ":" in locator:
            return locator
        return str((config_dir / path).resolve())

    def run_all_backups(self):
        """called by 'run' command"""
        for source in self.sources.values():
            source.run_backups()


def get_source_status(backups: BackupList, source: str, verbose: bool) -> None:
    """Print the status of every repository configured for a source."""
    configured_source = backups.sources.get(source)
    if configured_source is None:
        raise typer.BadParameter(f"unknown source: {source}", param_hint="--source")
    snapshot_ct = True if verbose else 3
    print(configured_source.all_repo_printouts(include_recent_snaps=snapshot_ct))


def get_dest_status(
    backups: BackupList, dest: str, pass_file: Path, verbose: bool
) -> None:
    """Print the status of one repository using the supplied password file."""
    snapshot_ct = True if verbose else 3
    repository = Repository(dest, pass_file, backups.restic_inst)
    print(repository.repo_stats_printout(include_recent_snaps=snapshot_ct))


app = typer.Typer(name="EZ-Restic", no_args_is_help=True)


@app.callback()
def cli(
    ctx: typer.Context,
    config_file: Annotated[
        Path | None,
        typer.Option("-c", "--config-file", help="Path to the backup configuration."),
    ] = None,
) -> None:
    """Configure and run backups with Restic."""
    ctx.ensure_object(dict)["config_file"] = config_file


def _load_backups(ctx: typer.Context) -> BackupList:
    config_file: Path | None = ctx.obj["config_file"]
    if config_file is None:
        config_file = Path("backups.toml")
        print("Warn: falling back to default config file:")
        print(" ", config_file.absolute())
    try:
        backups = BackupList.from_config_file(config_file)
    except BackupList.LoadingError as e:
        eprint(e.message)
        raise typer.Exit(1) from e
    return backups


@app.command("backup")
def backup_command(
    ctx: typer.Context,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            "-s",
            help="Back up only a single source from the configuration.",
        ),
    ] = None,
    all_sources: Annotated[
        bool,
        typer.Option(
            "--all",
            "-a",
            help="Back up all sources listed in the configuration.",
        ),
    ] = False,
    no_prune: Annotated[
        bool,
        typer.Option("--no-prune", help="Skip the pruning stage after backup."),
    ] = False,
) -> None:
    """Run a backup."""
    if source is not None and all_sources:
        raise typer.BadParameter(
            "--source and --all cannot be used together",
            param_hint="--source/--all",
        )
    if source is None and not all_sources:
        raise typer.BadParameter(
            "exactly one of --source or --all is required",
            param_hint="--source/--all",
        )

    backups = _load_backups(ctx)
    if all_sources:
        backups.run_all_backups()
    elif source not in backups.sources:
        raise typer.BadParameter(f"unknown source: {source}", param_hint="--source")
    else:
        backups.sources[source].run_backups(prune=not no_prune)


@app.command("status")
def status_command(
    ctx: typer.Context,
    source: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Status of all destinations for a source."),
    ] = None,
    dest: Annotated[
        str | None,
        typer.Option("--dest", "-d", help="Status of a single repository."),
    ] = None,
    pass_file: Annotated[
        Path | None,
        typer.Option(
            "--pass-file",
            "--pass_file",
            "-p",
            help="Path to the repository password file.",
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Print status for all repositories or filter by source or destination."""
    if source is not None and dest is not None:
        raise typer.BadParameter(
            "--source and --dest cannot be used together",
            param_hint="--source/--dest",
        )
    if dest is not None and pass_file is None:
        raise typer.BadParameter(
            "--pass-file is required with --dest",
            param_hint="--pass-file",
        )

    backups = _load_backups(ctx)
    if source is not None:
        get_source_status(backups, source, verbose)
    elif dest is not None:
        assert pass_file is not None
        get_dest_status(backups, dest, pass_file, verbose)
    else:
        snapshot_ct = True if verbose else 3
        for configured_source in backups.sources.values():
            print(configured_source.all_repo_printouts(include_recent_snaps=snapshot_ct))


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
