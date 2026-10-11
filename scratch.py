import json
from pathlib import Path
from subprocess import CompletedProcess

import src.ezrestic.ez_restic as ez_restic

backups = ez_restic.BackupList.from_config(Path("test_json/backups.toml").read_text())
restic = backups.restic_inst
demo_repo = backups.sources["demo"].dests[0]
dne_repo = backups.sources["dne"].dests[0]


def call_and_save(cmds: list[str], out_path: Path | None = None):
    res = restic._call_restic(demo_repo, cmds)
    if type(res) is CompletedProcess:
        writer = out_path.open("w") if out_path else None
        try:
            obj = json.loads(res.stdout.decode("utf-8"))
        except json.decoder.JSONDecodeError:
            print(res.stdout.decode(), file=writer)
            return
        print(json.dumps(obj, indent=4), file=writer)
        if writer:
            writer.close()
    else:
        print(res)


# f_raw = Path("test_json/stats_sz.json")
# call_and_save(["stats", "--mode", "raw-data"], f_raw)

# stats = restic.get_stats(backups.sources["demo"].dests[0])
# print(stats)


# backups.sources["demo"].run_backups(restic, prune=True)


print(restic.repo_stats_printout(demo_repo, 0))


