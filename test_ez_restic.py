from contextlib import nullcontext as doesnt_raise
from pathlib import Path

import pytest

from ezrestic import ez

default_conf = Path("test_json/backups.toml")
demo_repo = "./.demo_repo"


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


# def test_check_repo_good(get_restic: ez.EZRestic):
#     res = get_restic._check_repo_exists(demo_repo)
#     assert res == True


# def test_check_repo_bad(get_restic: ez.EZRestic):
#     res = get_restic._check_repo_exists("non_repo")
#     assert res == False


# def test_repo_create(get_restic: ez.EZRestic):
#     restic = get_restic
#     restic.init_repo("./test_repo")
