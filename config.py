import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# OLLAMA_MODEL = "qwen2.5:14b"
# PATCH_MODEL = "qwen2.5-coder:14b"
# SEMANTIC_MODEL = "qwen2.5:14b"

OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL",   "qwen3.5:4b")
PATCH_MODEL    = os.getenv("PATCH_MODEL",    "qwen2.5-coder:14b")
SEMANTIC_MODEL = os.getenv("SEMANTIC_MODEL", "mistral:latest")
EMBED_MODEL    = os.getenv("EMBED_MODEL",    "nomic-embed-text:v1.5")

OLLAMA_HOST    = os.getenv("OLLAMA_HOST",    "http://localhost:11434")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

GPU_NAME        = os.getenv("GPU_NAME",        "unknown")
CUDA_VERSION    = os.getenv("CUDA_VERSION",    "unknown")
DRIVER_VERSION  = os.getenv("DRIVER_VERSION",  "unknown")

MIN_CONFIDENCE_RAG_QUERIES = 0.75

SYSTEM_PROMPT = """You are an expert Kubernetes configuration assistant. Your role is to help users:
- Inspect and list Kubernetes resources in their cluster
- Identify misconfigurations, issues, and anomalies in manifests
- Delete resources when explicitly requested by the user
- Provide clear, actionable guidance on Kubernetes best practices

When analyzing resources, look for:
- Pods in CrashLoopBackOff, OOMKilled, or Pending states
- Missing resource limits/requests
- Deployments with 0 ready replicas
- Services with no endpoints
- Misconfigured health probes

Always confirm before deleting resources. Be concise and technical."""

MAX_TOOL_ITERATIONS = 10
