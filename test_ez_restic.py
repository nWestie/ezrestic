from collections.abc import Callable
from contextlib import nullcontext as doesnt_raise
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from typer.testing import CliRunner

from ezrestic import ez
from ezrestic import restic_types as rt

default_conf = Path("test_json/backups.toml")
demo_repo = "./.demo_repo"
runner = CliRunner()


@pytest.mark.parametrize(
    "file, expected",
    [
        (default_conf, doesnt_raise()),
        (Path("dne.dne"), pytest.raises(ez.BackupList.LoadingError)),
        (Path(".gitignore"), pytest.raises(ez.BackupList.LoadingError)),
        (Path("uv.lock"), pytest.raises(ez.BackupList.LoadingError)),
    ],
)
def test_config_load(file, expected):
    with expected:
        _ = ez.BackupList.from_config_file(file)


@pytest.fixture
def get_backup_obj():
    return ez.BackupList.from_config_file(default_conf)


@pytest.mark.parametrize(
    "locator, expected",
    [
        ("./local-repo", "relative"),
        (r"C:\backup\repo", "unchanged"),
        ("rclone:opcbackup:immich", "unchanged"),
        ("sftp:user@example.com:/repo", "unchanged"),
    ],
)
def test_resolve_repo_locator(locator, expected, tmp_path):
    config_dir = tmp_path / "config-directory"

    resolved = ez.BackupList._resolve_repo_locator(locator, config_dir)

    if expected == "relative":
        assert resolved == str((config_dir / "local-repo").resolve())
    else:
        assert resolved == locator


def test_snapshot_stats_parses_cli_output():
    backup_output = Path("test_json/backup_res.jsonl").read_bytes()
    stats = rt.SnapshotStats.from_backup_output(backup_output)
    assert stats.total_bytes_processed == 708


def test_check_result_parses_cli_output():
    check = rt.CheckResult.from_output(Path("test_json/check.json").read_bytes())
    assert check.broken_packs == []


def test_repo_stats_parses_cli_output():
    repo_stats = rt.RepoStatsResult.from_stats_output(
        Path("test_json/stats.json").read_bytes(),
        Path("test_json/stats_sz.json").read_bytes(),
    )
    assert repo_stats.stored_size == 6345


@pytest.mark.parametrize(
    "called, inputs",
    [
        (rt.SnapshotStats.from_backup_output, (b"not-json",)),
        (rt.Snapshot.list_from_output, (b"not-json",)),
        (rt.RepoStatsResult.from_stats_output, (b"not-json", b"also-not-json")),
        (rt.CheckResult.from_output, (b"not-json",)),
        (rt.ForgetResult.from_output, (b"not-json",)),
    ],
)
def test_results_reject_bad_json(called: Callable, inputs: tuple):
    with pytest.raises(rt.ResticParseError):
        called(*inputs)


@pytest.mark.parametrize("stderr", [b"", b"restic failed\n"])
def test_restic_error_handles_non_json_stderr(stderr):
    error = rt.ResticError.from_process(CompletedProcess(["restic"], 1, b"", stderr))
    assert isinstance(error, rt.ResticParseError)
    assert error.code == -1


def test_restic_call_with_bad_arguments_returns_restic_error(monkeypatch, tmp_path):
    process = CompletedProcess(
        ["restic", "invalid-command"],
        1,
        b"",
        b'{"message_type":"error","code":1,"message":"unknown command"}\n',
    )
    monkeypatch.setattr(ez.subp, "run", lambda *args, **kwargs: process)
    restic = ez.EZRestic("restic")
    repo = ez.Repository("repo", tmp_path / "password", restic)

    with pytest.raises(rt.ResticError) as raised:
        restic._call_on_repo(repo, ["invalid-command"])

    assert type(raised.value) is rt.ResticError
    assert raised.value.code == 1


def test_restic_error_handles_unknown_code():
    error = rt.ResticError.from_process(
        CompletedProcess(
            ["restic"],
            42,
            b"",
            b'{"message_type":"error","code":42,"message":"failed"}\n',
        )
    )
    assert error.sys_message == "Unknown Error"


def test_restic_call_start_failure_is_restic_error(tmp_path):
    restic = ez.EZRestic("restic-command-that-does-not-exist")
    repo = ez.Repository("repo", tmp_path / "password", restic)

    with pytest.raises(rt.ResticError) as raised:
        restic._call_on_repo(repo, ["snapshots"])

    assert isinstance(raised.value, rt.ResticCommandError)


def test_status_dest_requires_pass_file(tmp_path):
    result = runner.invoke(
        ez.app,
        [
            "--config-file",
            str(tmp_path / "missing.toml"),
            "status",
            "--dest",
            "some-repository",
        ],
    )

    assert result.exit_code != 0
    assert "--pass-file" in result.output


def test_status_source_rejects_unknown_source(tmp_path):
    config = tmp_path / "backups.toml"
    config.write_text(
        'restic_pass_file = "./password"\n'
        '[src.known]\n'
        'source_dir = "."\n'
        'dests = ["./repo"]\n'
    )

    result = runner.invoke(
        ez.app,
        ["--config-file", str(config), "status", "--source", "unknown"],
    )

    assert result.exit_code != 0
    assert "unknown source: unknown" in result.output


def test_status_rejects_source_and_dest_together(tmp_path):
    result = runner.invoke(
        ez.app,
        [
            "--config-file",
            str(tmp_path / "missing.toml"),
            "status",
            "--source",
            "demo",
            "--dest",
            "some-repository",
            "--pass-file",
            str(tmp_path / "password"),
        ],
        terminal_width=200,
    )

    assert result.exit_code != 0
    assert "No config file found" not in result.output


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--source", "demo", "--all"],
    ],
)
def test_backup_requires_source_selection_or_all(options):
    result = runner.invoke(
        ez.app,
        ["--config-file", "missing.toml", "backup", *options],
    )

    assert result.exit_code != 0
    assert "source" in result.output.lower() or "all" in result.output.lower()


def test_backup_all_allows_no_prune():
    result = runner.invoke(
        ez.app,
        [
            "--config-file",
            "missing.toml",
            "backup",
            "--all",
            "--no-prune",
        ],
    )

    assert "No config file found" in result.output
