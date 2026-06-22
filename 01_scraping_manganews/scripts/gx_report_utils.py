import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def try_git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def extract_failed_expectations(result: Dict[str, Any], limit: int = 200) -> List[Dict[str, Any]]:
    failed: List[Dict[str, Any]] = []
    for r in result.get("results", []):
        if not r.get("success", True):
            cfg = r.get("expectation_config", {})
            failed.append(
                {
                    "expectation_type": cfg.get("expectation_type"),
                    "kwargs": cfg.get("kwargs"),
                }
            )
        if len(failed) >= limit:
            break
    return failed


def write_json_report(report_dir: str, filename: str, payload: Dict[str, Any]) -> str:
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(report_dir) / filename
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    return str(out_path)
