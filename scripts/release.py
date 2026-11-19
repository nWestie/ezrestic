import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, capture_output: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=capture_output,
        text=True,
    )
    return result.stdout.strip() if capture_output else ""


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    uv_args = sys.argv[1:]
    if not uv_args:
        fail("pass arguments to uv version, for example: --bump patch or 0.8.0")

    # do checks
    for executable in ("git", "uv"):
        if shutil.which(executable) is None:
            fail(f"{executable} is required")

    if run(
        "git", "status", "--porcelain", "--untracked-files=all", capture_output=True
    ):
        fail("git working tree is not clean")

    # bump version
    run("uv", "version", *uv_args)

    version = run("uv", "version", "--short", capture_output=True)
    if not version:
        fail("uv did not return a short version")

    # tag it
    tag = f"v{version}"
    existing_tag = run(
        "git",
        "tag",
        "--list",
        tag,
        capture_output=True,
    )
    if existing_tag:
        fail(f"tag {tag} already exists")

    run("git", "add", "-u")
    staged = run("git", "diff", "--cached", "--name-only", capture_output=True)
    if not staged:
        fail("failed to stage the version changes")

    run("git", "commit", "-m", f"chore: bump version to {tag}")
    run("git", "tag", tag)

    branch = run("git", "branch", "--show-current", capture_output=True)
    if not branch:
        fail("cannot push a release from a detached HEAD")

    run("git", "push", "origin", branch)
    run("git", "push", "origin", tag)

    print(f"Released and pushed {tag}")


if __name__ == "__main__":
    main()
