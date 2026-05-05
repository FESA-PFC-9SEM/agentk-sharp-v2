import json
import subprocess
from datetime import datetime, timezone

import yaml
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

# Fields injected by the API server that are noise for static analysis
_RUNTIME_FIELDS = {
    "metadata": {"resourceVersion", "uid", "creationTimestamp", "generation",
                 "managedFields", "selfLink"},
    "status": None,  # drop the whole key
}


def _load_config():
    try:
        k8s_config.load_kube_config()
    except Exception:
        k8s_config.load_incluster_config()


def _age(creation_timestamp) -> str:
    if not creation_timestamp:
        return "unknown"
    delta = datetime.now(timezone.utc) - creation_timestamp
    s = int(delta.total_seconds())
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _pod_summary(pod) -> dict:
    container_statuses = pod.status.container_statuses or []
    restarts = sum(cs.restart_count for cs in container_statuses)
    ready = sum(1 for cs in container_statuses if cs.ready)
    total = len(pod.spec.containers)

    state = pod.status.phase or "Unknown"
    for cs in container_statuses:
        if cs.state.waiting:
            state = cs.state.waiting.reason or state
            break
        if cs.state.terminated and cs.state.terminated.reason:
            state = cs.state.terminated.reason
            break

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "status": state,
        "ready": f"{ready}/{total}",
        "restarts": restarts,
        "node": pod.spec.node_name,
        "age": _age(pod.metadata.creation_timestamp),
    }


def _deployment_summary(dep) -> dict:
    spec = dep.spec
    status = dep.status
    return {
        "name": dep.metadata.name,
        "namespace": dep.metadata.namespace,
        "ready": f"{status.ready_replicas or 0}/{spec.replicas or 0}",
        "up_to_date": status.updated_replicas or 0,
        "available": status.available_replicas or 0,
        "age": _age(dep.metadata.creation_timestamp),
    }


def _service_summary(svc) -> dict:
    ports = [
        f"{p.port}/{p.protocol}" + (f":{p.node_port}" if p.node_port else "")
        for p in (svc.spec.ports or [])
    ]
    return {
        "name": svc.metadata.name,
        "namespace": svc.metadata.namespace,
        "type": svc.spec.type,
        "cluster_ip": svc.spec.cluster_ip,
        "external_ip": svc.spec.external_i_ps or svc.status.load_balancer.ingress or [],
        "ports": ports,
        "age": _age(svc.metadata.creation_timestamp),
    }


def _configmap_summary(cm) -> dict:
    return {
        "name": cm.metadata.name,
        "namespace": cm.metadata.namespace,
        "keys": list((cm.data or {}).keys()),
        "age": _age(cm.metadata.creation_timestamp),
    }


def _node_summary(node) -> dict:
    conditions = {c.type: c.status for c in (node.status.conditions or [])}
    ready = conditions.get("Ready", "Unknown")
    roles = [
        k.replace("node-role.kubernetes.io/", "")
        for k in (node.metadata.labels or {})
        if k.startswith("node-role.kubernetes.io/")
    ]
    return {
        "name": node.metadata.name,
        "roles": roles or ["worker"],
        "status": "Ready" if ready == "True" else "NotReady",
        "age": _age(node.metadata.creation_timestamp),
        "version": node.status.node_info.kubelet_version if node.status.node_info else None,
    }


_NAMESPACED_RESOURCES = {
    "pod": ("core", "list_namespaced_pod"),
    "pods": ("core", "list_namespaced_pod"),
    "deployment": ("apps", "list_namespaced_deployment"),
    "deployments": ("apps", "list_namespaced_deployment"),
    "service": ("core", "list_namespaced_service"),
    "services": ("core", "list_namespaced_service"),
    "svc": ("core", "list_namespaced_service"),
    "configmap": ("core", "list_namespaced_config_map"),
    "configmaps": ("core", "list_namespaced_config_map"),
    "cm": ("core", "list_namespaced_config_map"),
    "daemonset": ("apps", "list_namespaced_daemon_set"),
    "daemonsets": ("apps", "list_namespaced_daemon_set"),
    "ds": ("apps", "list_namespaced_daemon_set"),
    "statefulset": ("apps", "list_namespaced_stateful_set"),
    "statefulsets": ("apps", "list_namespaced_stateful_set"),
    "sts": ("apps", "list_namespaced_stateful_set"),
    "replicaset": ("apps", "list_namespaced_replica_set"),
    "replicasets": ("apps", "list_namespaced_replica_set"),
    "rs": ("apps", "list_namespaced_replica_set"),
}

_CLUSTER_RESOURCES = {
    "namespace": ("core", "list_namespace"),
    "namespaces": ("core", "list_namespace"),
    "ns": ("core", "list_namespace"),
    "node": ("core", "list_node"),
    "nodes": ("core", "list_node"),
}

_DELETE_MAP = {
    "pod": ("core", "delete_namespaced_pod"),
    "pods": ("core", "delete_namespaced_pod"),
    "deployment": ("apps", "delete_namespaced_deployment"),
    "deployments": ("apps", "delete_namespaced_deployment"),
    "service": ("core", "delete_namespaced_service"),
    "services": ("core", "delete_namespaced_service"),
    "svc": ("core", "delete_namespaced_service"),
    "configmap": ("core", "delete_namespaced_config_map"),
    "configmaps": ("core", "delete_namespaced_config_map"),
    "cm": ("core", "delete_namespaced_config_map"),
    "daemonset": ("apps", "delete_namespaced_daemon_set"),
    "daemonsets": ("apps", "delete_namespaced_daemon_set"),
    "ds": ("apps", "delete_namespaced_daemon_set"),
    "statefulset": ("apps", "delete_namespaced_stateful_set"),
    "statefulsets": ("apps", "delete_namespaced_stateful_set"),
    "sts": ("apps", "delete_namespaced_stateful_set"),
}


def list_resources(resource_type: str, namespace: str = "default") -> str:
    _load_config()
    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    apis = {"core": core, "apps": apps}

    key = resource_type.lower()
    all_ns = namespace.lower() in ("all", "")

    try:
        if key in _CLUSTER_RESOURCES:
            api_key, method = _CLUSTER_RESOURCES[key]
            items = getattr(apis[api_key], method)().items

            if key in ("namespace", "namespaces", "ns"):
                result = [
                    {"name": n.metadata.name, "status": n.status.phase, "age": _age(n.metadata.creation_timestamp)}
                    for n in items
                ]
            else:
                result = [_node_summary(n) for n in items]

        elif key in _NAMESPACED_RESOURCES:
            api_key, method = _NAMESPACED_RESOURCES[key]
            fn = getattr(apis[api_key], method)
            items = (fn().items if all_ns else fn(namespace=namespace).items)

            summaries = {
                "pod": _pod_summary,
                "pods": _pod_summary,
                "deployment": _deployment_summary,
                "deployments": _deployment_summary,
                "service": _service_summary,
                "services": _service_summary,
                "svc": _service_summary,
                "configmap": _configmap_summary,
                "configmaps": _configmap_summary,
                "cm": _configmap_summary,
            }
            summarize = summaries.get(key, lambda x: {"name": x.metadata.name, "namespace": x.metadata.namespace})
            result = [summarize(item) for item in items]
        else:
            return json.dumps({"error": f"Unsupported resource type: '{resource_type}'. Supported: pod, deployment, service, configmap, daemonset, statefulset, replicaset, namespace, node"})

        return json.dumps({"resource_type": resource_type, "namespace": namespace, "count": len(result), "items": result}, default=str)

    except ApiException as e:
        return json.dumps({"error": f"Kubernetes API error: {e.status} {e.reason}", "detail": e.body})
    except Exception as e:
        return json.dumps({"error": str(e)})


def delete_resource(resource_type: str, name: str, namespace: str = "default") -> str:
    _load_config()
    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    apis = {"core": core, "apps": apps}

    key = resource_type.lower()

    if key not in _DELETE_MAP:
        return json.dumps({"error": f"Unsupported resource type for deletion: '{resource_type}'. Supported: pod, deployment, service, configmap, daemonset, statefulset"})

    api_key, method = _DELETE_MAP[key]
    try:
        getattr(apis[api_key], method)(name=name, namespace=namespace)
        return json.dumps({"status": "deleted", "resource_type": resource_type, "name": name, "namespace": namespace})
    except ApiException as e:
        return json.dumps({"error": f"Kubernetes API error: {e.status} {e.reason}", "detail": e.body})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Get resource YAML (for cluster-based analysis)
# ---------------------------------------------------------------------------

def _strip_runtime(doc: dict) -> dict:
    """Remove server-injected fields that add noise to static analysis."""
    doc = dict(doc)
    meta = doc.get("metadata")
    if isinstance(meta, dict):
        doc["metadata"] = {
            k: v for k, v in meta.items()
            if k not in {"resourceVersion", "uid", "creationTimestamp",
                         "generation", "managedFields", "selfLink"}
        }
    doc.pop("status", None)
    return doc


_LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"

_READ_METHODS = {
    "pod": ("core", "read_namespaced_pod"),
    "pods": ("core", "read_namespaced_pod"),
    "deployment": ("apps", "read_namespaced_deployment"),
    "deployments": ("apps", "read_namespaced_deployment"),
    "service": ("core", "read_namespaced_service"),
    "services": ("core", "read_namespaced_service"),
    "svc": ("core", "read_namespaced_service"),
    "configmap": ("core", "read_namespaced_config_map"),
    "configmaps": ("core", "read_namespaced_config_map"),
    "cm": ("core", "read_namespaced_config_map"),
    "daemonset": ("apps", "read_namespaced_daemon_set"),
    "daemonsets": ("apps", "read_namespaced_daemon_set"),
    "ds": ("apps", "read_namespaced_daemon_set"),
    "statefulset": ("apps", "read_namespaced_stateful_set"),
    "statefulsets": ("apps", "read_namespaced_stateful_set"),
    "sts": ("apps", "read_namespaced_stateful_set"),
}


def get_resource_yaml(resource_type: str, name: str, namespace: str = "default") -> tuple[str, str]:
    """
    Fetch a resource and return its YAML for static analysis.

    Strategy:
      1. If the resource has the kubectl last-applied-configuration annotation,
         return that — it is the closest to the original manifest.
      2. Otherwise fall back to the live state with runtime-only fields stripped.

    Returns (yaml_string, source) where source is 'last-applied' or 'live-state'.
    """
    _load_config()
    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    apis = {"core": core, "apps": apps}

    key = resource_type.lower()
    if key not in _READ_METHODS:
        return json.dumps({"error": f"Unsupported resource type: '{resource_type}'"}), "error"

    api_key, method = _READ_METHODS[key]
    try:
        obj = getattr(apis[api_key], method)(name=name, namespace=namespace)
        raw = client.ApiClient().sanitize_for_serialization(obj)

        last_applied = (raw.get("metadata") or {}).get("annotations") or {}
        last_applied = last_applied.get(_LAST_APPLIED)

        if last_applied:
            # The annotation value is a JSON string — convert to YAML
            doc = json.loads(last_applied)
            return yaml.dump(doc, default_flow_style=False, allow_unicode=True), "last-applied"

        # Fallback: clean live state
        clean = _strip_runtime(raw)
        return yaml.dump(clean, default_flow_style=False, allow_unicode=True), "live-state"

    except ApiException as e:
        return json.dumps({"error": f"Kubernetes API error: {e.status} {e.reason}"}), "error"
    except Exception as e:
        return json.dumps({"error": str(e)}), "error"


# ---------------------------------------------------------------------------
# Apply manifest
# ---------------------------------------------------------------------------

def apply_manifest(yaml_content: str) -> str:
    """Apply a Kubernetes manifest (kubectl apply -f -). Supports multi-document YAML."""
    try:
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=yaml_content,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.dumps({"status": "applied", "output": result.stdout.strip()})
        return json.dumps({"status": "error", "output": result.stderr.strip() or result.stdout.strip()})
    except FileNotFoundError:
        return json.dumps({"error": "kubectl not found in PATH"})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "kubectl apply timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})
