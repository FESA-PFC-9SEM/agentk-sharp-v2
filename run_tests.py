#!/usr/bin/env python3
"""
Batch test runner.

Runs analyze_manifest() on every manifest under scenarios/real/ N times,
saves patched YAMLs to scenarios/real/output/, and writes a CSV with one
row per LLM call containing timing and token metrics.

Usage:
    python run_tests.py [--runs N]   (default: 5)
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

import ollama

import logger
from config import GPU_NAME, CUDA_VERSION, DRIVER_VERSION, OLLAMA_HOST, OLLAMA_API_KEY
from workflow.analysis import analyze_manifest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INPUT_DIR  = Path(__file__).parent / "scenarios" / "real"
OUTPUT_DIR = INPUT_DIR / "output" / str(os.getpid())
CSV_PATH   = Path(__file__).parent / f"test_results_{os.getpid()}.csv"
CSV_FIELDS = [
    "timestamp", "manifest_name", "issue", "test_run",
    "language_model", "gpu_name", "cuda_version", "driver_version", "ollama_version",
    "result",
    "input_tokens", "output_tokens",
    "answer_latency_ms", "load_latency_ms",
    "patch",
]


def _ollama_version() -> str:
    try:
        headers = {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}
        cl = ollama.Client(host=OLLAMA_HOST, headers=headers)
        return cl.version() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5, help="Number of runs per manifest")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ollama_ver = _ollama_version()
    manifests = sorted(INPUT_DIR.glob("*.yaml"))

    if not manifests:
        print(f"No YAML files found in {INPUT_DIR}", file=sys.stderr)
        sys.exit(1)

    total = len(manifests) * args.runs
    print(f"Running {len(manifests)} manifest(s) × {args.runs} run(s) = {total} analyses")
    print(f"GPU: {GPU_NAME}  |  CUDA: {CUDA_VERSION}  |  Driver: {DRIVER_VERSION}  |  Ollama: {ollama_ver}")
    print(f"CSV: {CSV_PATH}\n")

    csv_file = CSV_PATH.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
    writer.writeheader()

    pending_rows: list[dict] = []

    def on_metrics(label: str, model: str, in_tok, out_tok, load_ms, gen_ms) -> None:
        pending_rows.append({
            "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "manifest_name":    _ctx["manifest"],
            "issue":            label,
            "test_run":         _ctx["run"],
            "language_model":   model,
            "gpu_name":         GPU_NAME,
            "cuda_version":     CUDA_VERSION,
            "driver_version":   DRIVER_VERSION,
            "ollama_version":   ollama_ver,
            "result":           "",
            "input_tokens":     in_tok  if in_tok  is not None else "",
            "output_tokens":    out_tok if out_tok is not None else "",
            "answer_latency_ms": gen_ms  if gen_ms  is not None else "",
            "load_latency_ms":  load_ms if load_ms is not None else "",
            "patch":            "",
        })

    logger.set_metrics_hook(on_metrics)
    _ctx: dict = {}

    done = 0
    for manifest in manifests:
        for run in range(1, args.runs + 1):
            done += 1
            _ctx["manifest"] = manifest.name
            _ctx["run"] = run
            pending_rows.clear()

            print(f"[{done}/{total}]  {manifest.name}  run={run} ...", end=" ", flush=True)
            try:
                report = analyze_manifest(manifest.read_text(encoding="utf-8"), manifest.name)

                # Save patched YAML
                out_path = OUTPUT_DIR / f"run{run}_patched_{manifest.name}"
                from workflow.analysis import apply_selected
                all_idx     = {i for i, fr  in enumerate(report.finding_reports)          if fr.patch  is not None}
                all_sem_idx = {i for i, sfr in enumerate(report.semantic_finding_reports)  if sfr.patch is not None}
                patched_yaml, _ = apply_selected(report, all_idx, all_sem_idx)
                out_path.write_text(patched_yaml, encoding="utf-8")

                # Correlate patches with their LLM call rows (same order as findings)
                checkov_patches = [
                    fr.patch for fr in report.finding_reports if fr.patch is not None
                ]
                sem_patches = [
                    sfr.patch for sfr in report.semantic_finding_reports if sfr.patch is not None
                ]
                ci, si = 0, 0
                for row in pending_rows:
                    label = row["issue"]
                    if label.startswith("PATCH ") and ci < len(checkov_patches):
                        p = checkov_patches[ci]
                        row["patch"] = f"{p.action} {p.path} = {p.value!r}"
                        ci += 1
                    elif label.startswith("SEMANTIC PATCH ") and si < len(sem_patches):
                        p = sem_patches[si]
                        row["patch"] = f"{p.action} {p.path} = {p.value!r}"
                        si += 1

                print(f"OK  → {out_path.name}")
            except Exception as e:
                print(f"FAILED: {e}", file=sys.stderr)

            for row in pending_rows:
                writer.writerow(row)
            csv_file.flush()

    csv_file.close()
    logger.set_metrics_hook(None)
    print(f"\nDone. Results saved to {CSV_PATH}")


if __name__ == "__main__":
    main()
