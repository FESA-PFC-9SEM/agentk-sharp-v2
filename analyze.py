#!/usr/bin/env python3
import sys
from pathlib import Path

from workflow.analysis import analyze_manifest, apply_selected
from workflow.classification import category_meta, group_findings

_SEP = "─" * 60
_SEV = {"critical": "CRIT", "high": "HIGH", "medium": "MED ", "low": "LOW ", "info": "INFO"}


def _err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def main() -> None:
    if len(sys.argv) != 2:
        _err("usage: analyze.py <manifest.yaml>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        _err(f"error: file not found: {path}")
        sys.exit(1)

    yaml_content = path.read_text(encoding="utf-8")
    report = analyze_manifest(yaml_content, path.name)

    # --- Checkov findings grouped by category ---
    if report.finding_reports:
        _err(f"\n{_SEP}")
        _err(f"CHECKOV  ({report.total_findings} finding(s), {report.patched_count} patched)")
        _err(_SEP)
        grouped = group_findings(report.finding_reports)
        for cat_name, frs in grouped.items():
            _err(f"\n  [{cat_name}]")
            for fr in frs:
                f = fr.finding
                patch_mark = "✓" if fr.patch else "✗"
                _err(f"  {patch_mark} {f.check_id}  {f.check_name}")
                _err(f"    resource: {f.resource}")
                if fr.patch:
                    _err(f"    patch:    {fr.patch.action} {fr.patch.path} = {fr.patch.value!r}")

    # --- Semantic findings ---
    sfrs = report.semantic_finding_reports
    if sfrs:
        _err(f"\n{_SEP}")
        _err(f"SEMANTIC REVIEW  ({len(sfrs)} finding(s))")
        _err(_SEP)
        for sfr in sfrs:
            f = sfr.finding
            sev = _SEV.get(f.severity, f.severity.upper())
            patch_mark = "✓" if sfr.patch else "✗"
            _err(f"\n  {patch_mark} [{sev}] {f.category}  —  {f.resource}  {f.field}")
            _err(f"  {f.description}")
            _err(f"  → {f.suggestion}")
            if sfr.patch:
                _err(f"  patch: {sfr.patch.action} {sfr.patch.path} = {sfr.patch.value!r}")
            if sfr.extra_resource:
                _err(f"  + generated Secret resource appended to output")

    _err(f"\n{_SEP}\n")

    # --- Patched YAML to stdout ---
    all_indices = {i for i, fr in enumerate(report.finding_reports) if fr.patch is not None}
    all_sem_indices = {i for i, sfr in enumerate(report.semantic_finding_reports) if sfr.patch is not None}
    patched_yaml, _ = apply_selected(report, all_indices, all_sem_indices)
    print(patched_yaml, end="")


if __name__ == "__main__":
    main()
