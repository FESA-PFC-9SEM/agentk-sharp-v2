"""
Semantic review pipeline — LLM-based detection of issues that static analysis misses.

Two focused passes over the manifest:
  1. Security  — credentials, exposure, sensitive data
  2. Quality   — coherence, cross-resource consistency, sizing, typos, encoding
"""

from typing import Literal

import ollama
import yaml
from pydantic import BaseModel, Field

from config import SEMANTIC_MODEL, OLLAMA_HOST
from logger import log, ollama_chat


def _resource_list(yaml_content: str) -> str:
    """Return a bullet list of 'Kind/name' for every document in the manifest."""
    lines = []
    try:
        for doc in yaml.safe_load_all(yaml_content):
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind", "Unknown")
            name = (doc.get("metadata") or {}).get("name", "unknown")
            lines.append(f"- {kind}/{name}")
    except Exception:
        pass
    return "\n".join(lines) if lines else "- (could not parse resources)"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class SemanticFinding(BaseModel):
    category: Literal[
        "typo",
        "hardcoded-secret",
        "port-mismatch",
        "label-mismatch",
        "resource-sizing",
        "coherence",
        "other",
    ] = Field(description="Category that best describes the problem")
    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="How critical this finding is"
    )
    resource: str = Field(
        description="Affected resource in 'Kind/name' format, e.g. 'Deployment/fiware-orionld'"
    )
    field: str = Field(
        description="Dot-notation field path or location, e.g. 'spec.template.spec.containers[0].env[1].value'"
    )
    description: str = Field(description="Clear explanation of what is wrong")
    suggestion: str = Field(description="Concrete suggestion to address it")


class SemanticReview(BaseModel):
    findings: list[SemanticFinding] = Field(
        description="All detected issues. Empty list if nothing was found."
    )


# ---------------------------------------------------------------------------
# Prompts — intentionally open-ended so the model reasons freely
# ---------------------------------------------------------------------------

_SECURITY_PROMPT = """\
You are a security-focused Kubernetes engineer reviewing a manifest before it goes to production.

Read the manifest carefully and identify any security concerns you notice — \
credentials, secrets, sensitive data, unnecessary exposure, or anything that \
a security audit would flag.

Be thorough but only report issues you are genuinely confident about. \
Do not invent problems.

Resources in this manifest (use exactly these identifiers in the "resource" field):
{resource_list}

Manifest:
```yaml
{yaml_content}
```

Return a JSON object with a "findings" array. \
If you find nothing, return {{"findings": []}}.
"""

_QUALITY_PROMPT = """\
You are a senior platform engineer reviewing a Kubernetes manifest for a colleague.

Read it carefully as you would in a real code review. Look for anything that seems \
wrong, inconsistent, fragile, or that would silently cause the workload to misbehave \
— across all resources in the file. Consider how the resources relate to each other.

Be thorough but only report issues you are genuinely confident about. \
Do not invent problems.

Resources in this manifest (use exactly these identifiers in the "resource" field):
{resource_list}

Manifest:
```yaml
{yaml_content}
```

Return a JSON object with a "findings" array. \
If you find nothing, return {{"findings": []}}.
"""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_semantic_review(yaml_content: str) -> SemanticReview:
    schema = SemanticReview.model_json_schema()
    cl = ollama.Client(host=OLLAMA_HOST)
    all_findings: list[SemanticFinding] = []
    resource_list = _resource_list(yaml_content)

    for pass_label, prompt_template in [
        # ("security", _SECURITY_PROMPT),
        ("quality",  _QUALITY_PROMPT),
    ]:
        try:
            resp = ollama_chat(
                cl,
                label=f"SEMANTIC {pass_label}",
                model=SEMANTIC_MODEL,
                options={"temperature": 0.1},
                messages=[{"role": "user", "content": prompt_template.format(yaml_content=yaml_content, resource_list=resource_list)}],
                format=schema,
                stream=False,
            )
            result = SemanticReview.model_validate_json(resp.message.content or "{}")
            log("SEMANTIC", f"{pass_label}: {len(result.findings)} finding(s)")
            all_findings.extend(result.findings)
        except Exception as e:
            log("SEMANTIC", f"{pass_label} pass failed: {e}")

    return SemanticReview(findings=all_findings)
