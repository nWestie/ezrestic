print(f"Importing {__name__}")
from . import ez_restic as ez


def main() -> None:
    import sys
    print("hiii")
    sys.exit(ez.main())
