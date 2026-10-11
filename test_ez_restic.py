import tomllib
from contextlib import nullcontext as doesnt_raise
from pathlib import Path

import pytest

from ezrestic import ez

default_conf = Path("test_json/backups.toml")


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
