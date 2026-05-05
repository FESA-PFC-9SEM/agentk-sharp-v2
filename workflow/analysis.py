"""
Top-level analysis orchestrator.

Flow per manifest:
  1. checkov  → list[CheckovFinding]  (each finding carries code_block from the manifest)
  2. RAG      → retrieve CIS benchmark context per finding
  3. LLM      → generate YAMLPatch using code_block + RAG context
  4. Apply    → patch multi-doc YAML, produce unified diff
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from logger import log
from workflow.checkov_runner import CheckovFinding, run_checkov
from workflow.classification import check_categories
from workflow.llm import generate_patch, generate_semantic_patch
from workflow.patch import YAMLPatch, apply_patches_to_yaml, format_diff
from workflow.rag import RAGIndex, RAGResult, build_index, query
from workflow.semantic_review import SemanticFinding, SemanticReview, run_semantic_review

DOCS_DIR = Path(__file__).parent.parent / "docs"
SKIP_FILE = Path(__file__).parent.parent / "skip_checks.json"
_PDF_NAME = "CIS_Kubernetes_Benchmark_V2.0.0_PDF.pdf"


# ---------------------------------------------------------------------------
# Report data structures
# ---------------------------------------------------------------------------

@dataclass
class FindingReport:
    finding: CheckovFinding          # .code_block is the manifest chunk checkov flagged
    rag_results: list[RAGResult]
    patch: YAMLPatch | None
    categories: list[str] = field(default_factory=list)  # from classification.json


@dataclass
class SemanticFindingReport:
    finding: SemanticFinding
    patch: YAMLPatch | None
    extra_resource: str | None = None  # e.g. generated Secret YAML for hardcoded-secret findings


@dataclass
class AnalysisReport:
    filename: str
    original_yaml: str
    finding_reports: list[FindingReport]
    semantic_finding_reports: list[SemanticFindingReport]
    patched_yaml: str
    diff: str
    patch_errors: list[str] = field(default_factory=list)

    @property
    def semantic_review(self) -> SemanticReview:
        return SemanticReview(findings=[r.finding for r in self.semantic_finding_reports])

    @property
    def total_findings(self) -> int:
        return len(self.finding_reports)

    @property
    def patched_count(self) -> int:
        return sum(1 for r in self.finding_reports if r.patch is not None)


# ---------------------------------------------------------------------------
# Resource key helpers (for multi-doc YAML patching)
# ---------------------------------------------------------------------------

def _checkov_resource_key(resource: str) -> str:
    """
    'Deployment.default.fiware-orionld'  → 'Deployment/fiware-orionld'
    'Service.fiware-orionld-service'     → 'Service/fiware-orionld-service'
    """
    parts = resource.split(".")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[2]}"
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return resource


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_manifest(
    yaml_content: str,
    filename: str = "manifest.yaml",
    run_static: bool = True,
    run_semantic: bool = True,
) -> AnalysisReport:
    pdf_path = str(DOCS_DIR / _PDF_NAME)

    # Step 1: static analysis
    findings: list[CheckovFinding] = []
    if run_static:
        log("ANALYSIS", f"running checkov on {filename}")
        findings = run_checkov(yaml_content)
        log("ANALYSIS", f"{len(findings)} failed check(s)")
    else:
        log("ANALYSIS", "static analysis skipped")

    # Step 2: semantic review + patch generation
    semantic_finding_reports: list[SemanticFindingReport] = []
    if run_semantic:
        log("ANALYSIS", "running semantic review")
        semantic = run_semantic_review(yaml_content)
        log("ANALYSIS", f"semantic: {len(semantic.findings)} finding(s)")
        for sf in semantic.findings:
            sp, extra = generate_semantic_patch(sf, yaml_content)
            if sp:
                log("SEMANTIC PATCH", f"{sf.resource}  {sf.field}  → {sp.action} {sp.path}")
            else:
                log("SEMANTIC PATCH", f"failed for {sf.resource}/{sf.field}")
            if extra:
                log("SEMANTIC PATCH", f"generated extra resource for {sf.resource}")
            semantic_finding_reports.append(SemanticFindingReport(finding=sf, patch=sp, extra_resource=extra))
    else:
        log("ANALYSIS", "semantic review skipped")

    if not findings:
        return AnalysisReport(
            filename=filename,
            original_yaml=yaml_content,
            finding_reports=[],
            semantic_finding_reports=semantic_finding_reports,
            patched_yaml=yaml_content,
            diff="(no issues found — nothing to patch)",
        )

    # Step 3: build RAG index (cached after first run)
    log("ANALYSIS", "loading RAG index")
    rag_index: RAGIndex = build_index(pdf_path)

    # Steps 4-5: per finding
    finding_reports: list[FindingReport] = []
    patches_by_resource: dict[str, list[YAMLPatch]] = {}

    for i, finding in enumerate(findings, 1):
        log("FINDING", f"[{i}/{len(findings)}] {finding.check_id} — {finding.check_name}  resource: {finding.resource}")

        query_text = f"{finding.check_id} {finding.check_name}\n{finding.code_block}"
        rag_results = query(rag_index, query_text, k=3)
        if rag_results:
            top = rag_results[0]
            log("RAG", f"top: [{top.chunk.section}] {top.chunk.title}  score={top.score:.3f}")
        else:
            log("RAG", "no results above confidence threshold")

        patch = generate_patch(finding, rag_results)
        if patch:
            log("PATCH", f"{patch.action} {patch.path} = {patch.value!r}")
            rkey = _checkov_resource_key(finding.resource)
            patches_by_resource.setdefault(rkey, []).append(patch)
        else:
            log("PATCH", "generation failed")

        cats = check_categories(finding.check_id)
        if cats:
            log("FINDING", f"categories: {', '.join(cats)}")

        finding_reports.append(FindingReport(
            finding=finding,
            rag_results=rag_results,
            patch=patch,
            categories=cats,
        ))

    # Step 6: apply all patches and produce diff
    patched_yaml, patch_errors = apply_patches_to_yaml(yaml_content, patches_by_resource)
    diff = format_diff(yaml_content, patched_yaml, filename)

    for err in patch_errors:
        log("PATCH ERROR", err)

    log("ANALYSIS", f"done — {sum(1 for r in finding_reports if r.patch)} patch(es) applied")

    return AnalysisReport(
        filename=filename,
        original_yaml=yaml_content,
        finding_reports=finding_reports,
        semantic_finding_reports=semantic_finding_reports,
        patched_yaml=patched_yaml,
        diff=diff,
        patch_errors=patch_errors,
    )


# ---------------------------------------------------------------------------
# Post-analysis helpers
# ---------------------------------------------------------------------------

def apply_selected(
    report: AnalysisReport,
    selected_indices: set[int],
    semantic_selected_indices: set[int] | None = None,
) -> tuple[str, str]:
    """
    Re-apply only the patches chosen by the user (checkov + semantic).
    Returns (patched_yaml, diff).
    """
    patches_by_resource: dict[str, list[YAMLPatch]] = {}

    for i, fr in enumerate(report.finding_reports):
        if i not in selected_indices or fr.patch is None:
            continue
        rkey = _checkov_resource_key(fr.finding.resource)
        patches_by_resource.setdefault(rkey, []).append(fr.patch)

    sem_indices = semantic_selected_indices if semantic_selected_indices is not None else set()
    extra_resources: list[str] = []
    for i, sfr in enumerate(report.semantic_finding_reports):
        if i not in sem_indices:
            continue
        if sfr.patch is not None:
            kind, _, name = sfr.finding.resource.partition("/")
            rkey = f"{kind}/{name}"
            patches_by_resource.setdefault(rkey, []).append(sfr.patch)
        if sfr.extra_resource:
            extra_resources.append(sfr.extra_resource)

    patched_yaml, _ = apply_patches_to_yaml(report.original_yaml, patches_by_resource)
    if extra_resources:
        merged: dict[str, dict] = {}
        for raw in extra_resources:
            try:
                doc = yaml.safe_load(raw)
                if not isinstance(doc, dict):
                    continue
                key = f"{doc.get('kind', '')}/{(doc.get('metadata') or {}).get('name', '')}"
                if key in merged:
                    merged[key].setdefault("data", {}).update(doc.get("data") or {})
                else:
                    merged[key] = doc
            except Exception:
                pass
        extras_yaml = yaml.dump_all(list(merged.values()), default_flow_style=False, allow_unicode=True)
        patched_yaml = patched_yaml.rstrip("\n") + "\n---\n" + extras_yaml
    diff = format_diff(report.original_yaml, patched_yaml, report.filename)
    return patched_yaml, diff


def add_to_skip(check_ids: list[str]) -> None:
    """Append check_ids to skip_checks.json, deduplicating."""
    try:
        data = json.loads(SKIP_FILE.read_text())
    except Exception:
        data = {"skip": []}

    existing = set(data.get("skip", []))
    existing.update(check_ids)
    data["skip"] = sorted(existing)
    SKIP_FILE.write_text(json.dumps(data, indent=2) + "\n")
