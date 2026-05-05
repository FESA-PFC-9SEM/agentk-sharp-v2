import copy
import json
import re
import difflib
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------

def _normalize_patch_path(v: str) -> str:
    """Normalise any path format the LLM might produce to dot-notation with [N].

    Accepted inputs (all produce the same result):
      /spec/template/spec/containers/0/securityContext   (JSON Pointer)
      spec.template.spec.containers.0.securityContext    (dot + bare digit)
      spec.template.spec.containers[0].securityContext   (canonical — unchanged)
    """
    if not isinstance(v, str):
        return v

    if v.startswith("/"):
        parts = v.lstrip("/").split("/")
        segments: list[str] = []
        i = 0
        while i < len(parts):
            part = parts[i]
            if part.isdigit():
                if segments:
                    segments[-1] = f"{segments[-1]}[{part}]"
                i += 1
                continue
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                segments.append(f"{part}[{parts[i + 1]}]")
                i += 2
            else:
                segments.append(part)
                i += 1
        return ".".join(segments)

    parts = v.split(".")
    segments = []
    for part in parts:
        if re.match(r"^\d+$", part) and segments:
            segments[-1] = f"{segments[-1]}[{part}]"
        else:
            segments.append(part)
    return ".".join(segments)


# ---------------------------------------------------------------------------
# YAMLPatch model
# ---------------------------------------------------------------------------

class YAMLPatch(BaseModel):
    reasoning: str = Field(
        description=(
            "Step-by-step analysis: what the problem is, which field needs to change, "
            "and what the correct value should be. Think before writing the patch."
        )
    )
    action: Literal["replace", "add", "remove"] = Field(
        description=(
            "'replace' to change an existing field, "
            "'add' to create a new field or list item, "
            "'remove' to delete a field"
        )
    )
    path: str = Field(
        description=(
            "Dot-notation path using [N] for list indices. "
            "CORRECT: spec.template.spec.containers[0].securityContext.readOnlyRootFilesystem  "
            "WRONG:   /spec/template/spec/containers/0/securityContext  "
            "WRONG:   spec.template.spec.containers.0.securityContext  "
            "For Deployments containers are at spec.template.spec.containers[N], "
            "not spec.containers[N]."
        )
    )
    value: str | None = Field(
        default=None,
        description=(
            "New value as a YAML scalar or inline JSON for complex types. "
            "Examples: 'true' (bool), '500m' (string), "
            "'{\"cpu\": \"500m\", \"memory\": \"256Mi\"}' (dict). "
            "Omit (null) for 'remove' actions."
        )
    )
    explanation: str = Field(
        description="One sentence describing what this patch changes and why."
    )

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        return _normalize_patch_path(v)


# ---------------------------------------------------------------------------
# Path navigation helpers
# ---------------------------------------------------------------------------

def _path_segments(path: str) -> list[str | int]:
    """Turn 'spec.containers[0].image' into ['spec', 'containers', 0, 'image']."""
    parts = []
    for segment in path.split("."):
        m = re.match(r"^(.+)\[(\d+)\]$", segment)
        if m:
            parts.append(m.group(1))
            parts.append(int(m.group(2)))
        else:
            parts.append(segment)
    return parts


def _parse_value(raw: str | None):
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    low = str(raw).strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    try:
        parsed = json.loads(raw)
        return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    return raw


def apply_patch(doc: dict, patch: YAMLPatch) -> dict:
    """Apply a single YAMLPatch to a document dict. Returns a new dict."""
    doc = copy.deepcopy(doc)
    segments = _path_segments(patch.path)
    *parent_segs, leaf = segments

    node = doc
    for seg in parent_segs:
        if isinstance(seg, int):
            node = node[seg]
        else:
            if seg not in node and patch.action == "add":
                node[seg] = {}
            node = node[seg]

    if patch.action in ("replace", "add"):
        if isinstance(node, list) and isinstance(leaf, int):
            if leaf < len(node):
                node[leaf] = _parse_value(patch.value)
            else:
                node.append(_parse_value(patch.value))
        else:
            node[leaf] = _parse_value(patch.value)
    elif patch.action == "remove":
        if isinstance(node, list) and isinstance(leaf, int):
            del node[leaf]
        elif isinstance(node, dict) and leaf in node:
            del node[leaf]

    return doc


# ---------------------------------------------------------------------------
# Multi-document YAML patching
# ---------------------------------------------------------------------------

def _resource_key(doc: dict) -> str:
    """Return 'Kind/name' identifier for a YAML document."""
    kind = doc.get("kind", "")
    name = (doc.get("metadata") or {}).get("name", "")
    return f"{kind}/{name}"


def apply_patches_to_yaml(
    yaml_content: str,
    patches_by_resource: dict[str, list[YAMLPatch]],
) -> tuple[str, list[str]]:
    """
    Apply patches grouped by 'Kind/name' key to a (possibly multi-document) YAML string.

    Returns (patched_yaml_string, list_of_errors).
    """
    docs = list(yaml.safe_load_all(yaml_content))
    errors: list[str] = []

    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            continue
        key = _resource_key(doc)
        for patch in patches_by_resource.get(key, []):
            try:
                docs[i] = apply_patch(docs[i], patch)
            except Exception as e:
                errors.append(f"[{key}] patch '{patch.path}' failed: {e}")

    dumped = yaml.dump_all(docs, default_flow_style=False, allow_unicode=True)
    return dumped, errors


# ---------------------------------------------------------------------------
# Diff formatting
# ---------------------------------------------------------------------------

def format_diff(original: str, patched: str, filename: str = "manifest.yaml") -> str:
    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    ))
    return "".join(diff_lines) if diff_lines else "(no changes)"
