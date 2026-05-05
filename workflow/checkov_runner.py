import json
import subprocess
import tempfile
import os
from dataclasses import dataclass
from pathlib import Path

_SKIP_FILE = Path(__file__).parent.parent / "skip_checks.json"


def _load_skip_checks() -> list[str]:
    try:
        return json.loads(_SKIP_FILE.read_text()).get("skip", [])
    except Exception:
        return []


@dataclass
class CheckovFinding:
    check_id: str
    check_name: str
    resource: str          # e.g. "Deployment.default.fiware-orionld"
    file_line_range: list[int]
    code_block: str        # raw YAML lines from checkov
    guideline: str


def _parse_results(raw: dict) -> list[CheckovFinding]:
    findings = []

    # checkov may wrap results under a list (multiple check types) or a single dict
    results_list = raw if isinstance(raw, list) else [raw]

    for result_block in results_list:
        failed = (result_block.get("results") or {}).get("failed_checks", [])
        for check in failed:
            code_block_lines = check.get("code_block") or []
            code_text = "".join(line for _, line in code_block_lines)

            findings.append(CheckovFinding(
                check_id=check.get("check_id", ""),
                check_name=check.get("check_name", ""),
                resource=check.get("resource", ""),
                file_line_range=check.get("file_line_range", [0, 0]),
                code_block=code_text,
                guideline=check.get("guideline", ""),
            ))

    return findings


def run_checkov(yaml_content: str) -> list[CheckovFinding]:
    """Write yaml_content to a temp file, run checkov, return failed checks."""
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        tmp_path = f.name

    try:
        skip = _load_skip_checks()
        cmd = ["checkov", "-f", tmp_path, "-o", "json", "--quiet", "--compact"]
        if skip:
            cmd += ["--skip-check", ",".join(skip)]
            print(f"[CHECKOV] skipping: {', '.join(skip)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = result.stdout.strip()
        if not stdout:
            return []

        raw = json.loads(stdout)
        return _parse_results(raw)

    except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        print(f"[CHECKOV ERROR] {e}")
        return []
    finally:
        os.unlink(tmp_path)
