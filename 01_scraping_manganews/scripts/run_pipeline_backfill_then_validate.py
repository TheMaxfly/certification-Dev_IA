import subprocess
import sys


def main() -> int:
    cmd = [
        sys.executable,
        "scripts/run_all_validations_gx110.py",
        "--do-backfill",
    ]
    p = subprocess.run(cmd)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
