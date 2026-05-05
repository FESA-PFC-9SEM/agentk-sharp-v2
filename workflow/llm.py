"""
LLM calls used exclusively by the analysis workflow.
"""

import ollama
import yaml
from pydantic import BaseModel, Field

from config import OLLAMA_MODEL, OLLAMA_HOST, PATCH_MODEL
from logger import log, ollama_chat
from workflow.checkov_runner import CheckovFinding
from workflow.patch import YAMLPatch
from workflow.rag import RAGResult
from workflow.semantic_review import SemanticFinding


def _client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_HOST)


def _rag_context_text(rag_results: list[RAGResult]) -> str:
    parts = []
    for r in rag_results:
        refs = "\n".join(f"  - {u}" for u in r.chunk.references[:3])
        parts.append(
            f"[CIS {r.chunk.section}] {r.chunk.title}  (PDF page {r.chunk.page})\n"
            f"{r.chunk.text[:600]}\n"
            + (f"References:\n{refs}" if refs else "")
        )
    return "\n\n---\n\n".join(parts)


def generate_patch(
    finding: CheckovFinding,
    rag_results: list[RAGResult],
) -> YAMLPatch | None:
    rag_ctx = _rag_context_text(rag_results)

    prompt = (
        "You are a Kubernetes security expert. "
        "Generate a single YAMLPatch to fix the security issue below.\n\n"
        f"Check: [{finding.check_id}] {finding.check_name}\n"
        f"Resource: {finding.resource}\n\n"
        "Manifest chunk (verbatim lines from the file checkov flagged):\n"
        f"```yaml\n{finding.code_block}\n```\n\n"
        f"CIS Kubernetes Benchmark context:\n{rag_ctx}\n\n"
        "Rules:\n"
        "- Derive the path from the manifest chunk above — it shows the real structure\n"
        "- Use dot-notation with [N] for list indices\n"
        "- For Deployments, containers live at spec.template.spec.containers[N]\n"
        "- Produce exactly ONE patch (the most impactful fix)\n"
        "- Encode nested objects as inline JSON in the value field\n"
    )

    schema = YAMLPatch.model_json_schema()

    try:
        resp = ollama_chat(
            _client(),
            label=f"PATCH {finding.check_id}",
            model=PATCH_MODEL,
            options={"temperature": 0},
            messages=[{"role": "user", "content": prompt}],
            format=schema,
            stream=False,
        )
        raw = resp.message.content or ""
        return YAMLPatch.model_validate_json(raw)
    except Exception as e:
        log("PATCH", f"failed for {finding.check_id}: {e}")
        return None


class _EnvVarExtraction(BaseModel):
    name: str = Field(description="The env var name (e.g. MONGO_PASSWORD)")
    value: str = Field(description="The plaintext value of the env var")


def _extract_env_var(field: str, resource_yaml: str) -> tuple[str, str] | None:
    """Ask the LLM to extract the env var name and plaintext value at the given field path."""
    prompt = (
        "Given the Kubernetes resource YAML below, extract the environment variable "
        f"at field path '{field}'.\n\n"
        f"```yaml\n{resource_yaml}\n```\n\n"
        "Return the env var name and its plaintext value."
    )
    try:
        resp = ollama_chat(
            _client(),
            label="ENV VAR EXTRACT",
            model=PATCH_MODEL,
            options={"temperature": 0},
            messages=[{"role": "user", "content": prompt}],
            format=_EnvVarExtraction.model_json_schema(),
            stream=False,
        )
        result = _EnvVarExtraction.model_validate_json(resp.message.content or "{}")
        if result.name:
            return result.name, result.value
    except Exception as e:
        log("ENV VAR EXTRACT", f"failed: {e}")
    return None


def _build_secret_yaml(resource_name: str, namespace: str | None, env_name: str, env_value: str) -> str:
    """Produce a Kubernetes Secret manifest for a single credential."""
    import base64 as _b64
    secret_name = f"{resource_name}-credentials"
    key = env_name.lower().replace("_", "-")
    encoded = _b64.b64encode(env_value.encode()).decode()
    doc: dict = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name},
        "type": "Opaque",
        "data": {key: encoded},
    }
    if namespace:
        doc["metadata"]["namespace"] = namespace
    return yaml.dump(doc, default_flow_style=False, allow_unicode=True)


def generate_semantic_patch(
    finding: SemanticFinding,
    yaml_content: str,
) -> tuple[YAMLPatch | None, str | None]:
    """
    Generate a YAMLPatch for a semantic finding.
    Returns (patch, extra_resource_yaml).

    For hardcoded-secret findings the patch replaces the plaintext value with
    a secretKeyRef and extra_resource_yaml contains a generated Secret manifest.
    """
    kind, _, name = finding.resource.partition("/")
    resource_doc: dict | None = None
    resource_yaml = yaml_content
    try:
        for doc in yaml.safe_load_all(yaml_content):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == kind and (doc.get("metadata") or {}).get("name") == name:
                resource_doc = doc
                resource_yaml = yaml.dump(doc, default_flow_style=False, allow_unicode=True)
                break
    except Exception:
        pass

    # --- hardcoded-secret: generate Secret resource + secretKeyRef patch ---
    extra_resource: str | None = None
    hardcoded_hint = ""
    if finding.category == "hardcoded-secret" and resource_doc is not None:
        env_info = _extract_env_var(finding.field, resource_yaml)
        if env_info:
            env_name, env_value = env_info
            namespace = (resource_doc.get("metadata") or {}).get("namespace")
            extra_resource = _build_secret_yaml(name, namespace, env_name, env_value)
            secret_name = f"{name}-credentials"
            secret_key = env_name.lower().replace("_", "-")
            hardcoded_hint = (
                f"\nSince this is a hardcoded credential, replace the env entry's 'value' field "
                f"with a valueFrom.secretKeyRef pointing to Secret '{secret_name}', key '{secret_key}'. "
                f"Use action=replace on the path up to (but not including) 'value', and set value to "
                f'the JSON: {{"valueFrom": {{"secretKeyRef": {{"name": "{secret_name}", "key": "{secret_key}"}}}}}}.'
            )

    prompt = (
        "You are a Kubernetes expert. "
        "Generate a single YAMLPatch to fix the issue described below.\n\n"
        f"Resource: {finding.resource}\n"
        f"Field:    {finding.field}\n"
        f"Problem:  {finding.description}\n"
        f"Suggestion: {finding.suggestion}\n\n"
        "Resource YAML (use this to determine the exact path):\n"
        f"```yaml\n{resource_yaml}\n```\n\n"
        "Rules:\n"
        "- Derive the path from the YAML above — it shows the real field structure\n"
        "- Use dot-notation with [N] for list indices\n"
        "- For Deployments, containers live at spec.template.spec.containers[N]\n"
        "- Produce exactly ONE patch (the most impactful fix)\n"
        "- Encode nested objects as inline JSON in the value field\n"
        + hardcoded_hint
    )

    schema = YAMLPatch.model_json_schema()

    try:
        resp = ollama_chat(
            _client(),
            label=f"SEMANTIC PATCH {finding.category}",
            model=PATCH_MODEL,
            options={"temperature": 0},
            messages=[{"role": "user", "content": prompt}],
            format=schema,
            stream=False,
        )
        raw = resp.message.content or ""
        return YAMLPatch.model_validate_json(raw), extra_resource
    except Exception as e:
        log("SEMANTIC PATCH", f"failed for {finding.resource}/{finding.field}: {e}")
        return None, extra_resource
