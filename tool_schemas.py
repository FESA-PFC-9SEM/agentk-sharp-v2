TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_resources",
            "description": (
                "List Kubernetes resources in the cluster. "
                "Use this to inspect pods, deployments, services, configmaps, daemonsets, "
                "statefulsets, replicasets, namespaces, or nodes. "
                "Pass namespace='all' to list across all namespaces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "description": "Resource type to list. One of: pod, deployment, service, configmap, daemonset, statefulset, replicaset, namespace, node",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace. Use 'all' for all namespaces. Defaults to 'default'.",
                        "default": "default",
                    },
                },
                "required": ["resource_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_resource",
            "description": (
                "Delete a specific Kubernetes resource by name. "
                "Only use this when the user explicitly confirms they want to delete. "
                "Supported types: pod, deployment, service, configmap, daemonset, statefulset."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "description": "Resource type to delete: pod, deployment, service, configmap, daemonset, statefulset",
                    },
                    "name": {
                        "type": "string",
                        "description": "Exact name of the resource to delete",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace where the resource lives. Defaults to 'default'.",
                        "default": "default",
                    },
                },
                "required": ["resource_type", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_manifest",
            "description": (
                "Apply a Kubernetes manifest to the cluster (kubectl apply). "
                "Accepts raw YAML content, supports multi-document manifests. "
                "Only call this after the user has explicitly confirmed they want to apply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "yaml_content": {
                        "type": "string",
                        "description": "Full YAML content of the manifest to apply",
                    },
                },
                "required": ["yaml_content"],
            },
        },
    },
]
