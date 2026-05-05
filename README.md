# AgentK-Sharp

A Kubernetes configuration assistant that combines static analysis, RAG-based benchmark context, and LLM-powered patch generation to detect and fix misconfigurations in Kubernetes manifests.

Built as part of a TCC (undergraduate thesis) research project evaluating small language models for automated Kubernetes security remediation.

---

## Features

- **Chat interface** — conversational agent that can list, inspect, and delete cluster resources via the Kubernetes API
- **Static analysis** — runs [Checkov](https://www.checkov.io/) against manifests and groups findings by CIS Kubernetes Benchmark category
- **RAG context** — retrieves relevant CIS Benchmark sections (PDF) for each finding using embedding-based similarity search
- **LLM patch generation** — generates `YAMLPatch` objects (dot-notation path + value) for each finding, grounded in the manifest chunk and benchmark context
- **Semantic review** — separate LLM pass that detects issues static analysis misses: hardcoded credentials, port/label mismatches, resource sizing, typos, coherence problems
- **Secret generation** — when a hardcoded credential is detected, automatically generates a Kubernetes `Secret` resource and rewrites the env var to use `secretKeyRef`
- **Selective apply** — UI checkboxes to choose which fixes to apply; produces a unified diff and downloadable patched manifest
- **Ignore list** — permanently skip Checkov check IDs via `skip_checks.json`
- **Batch test runner** — runs all manifests N times, saves patched YAMLs and a CSV with per-LLM-call metrics for research analysis

---

## Architecture

```
app.py                  Streamlit UI (Chat tab + Analyze Manifest tab)
agent.py                Stateless chat loop with Ollama tool calling
k8s_tools.py            Kubernetes API wrappers (list, get, delete, apply)
tool_schemas.py         Ollama tool definitions

workflow/
  analysis.py           Top-level orchestrator: checkov → RAG → LLM → patch
  checkov_runner.py     Checkov subprocess runner, parses findings
  rag.py                PDF chunking, embedding (nomic-embed-text), cosine retrieval
  llm.py                Patch generation LLM calls (checkov + semantic)
  semantic_review.py    Two-pass semantic review (security + quality prompts)
  patch.py              YAMLPatch model, path normalisation, multi-doc YAML patching
  classification.py     Groups findings by category from checkov_tests_classification.json

analyze.py              CLI entrypoint: analyze a manifest, print report, output patched YAML
run_tests.py            Batch test runner for research experiments
config.py               All configuration (reads from .env)
logger.py               Timestamped logging to stderr + daily log file; metrics hook
```

### Analysis pipeline

```
manifest.yaml
    │
    ├─ Checkov ──────────────────────────────────────────────── list[CheckovFinding]
    │                                                                    │
    │                                                           RAG (CIS PDF)
    │                                                                    │
    │                                                           LLM → YAMLPatch
    │
    └─ Semantic review (LLM) ────────────────────────────── list[SemanticFinding]
                                                                         │
                                                                LLM → YAMLPatch
                                                                (+ Secret resource
                                                                 for hardcoded creds)
                                                                         │
                                                              patched manifest + diff
```

---

## Setup

### Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) (local or remote)
- `kubectl` configured for your cluster (for the Chat tab)

```bash
pip install -r requirements.txt
```

### Models

Pull the required models in Ollama:

```bash
ollama pull qwen2.5:14b
ollama pull qwen2.5-coder:14b
ollama pull mistral
ollama pull nomic-embed-text:v1.5
```

### Configuration

Copy `.env` and fill in your values:

```bash
# Ollama connection
OLLAMA_HOST=http://localhost:11434
OLLAMA_API_KEY=                        # leave empty for local, set for remote VM

# Models
OLLAMA_MODEL=qwen2.5:14b               # chat agent
PATCH_MODEL=qwen2.5-coder:14b          # patch generation
SEMANTIC_MODEL=mistral:latest          # semantic review
EMBED_MODEL=nomic-embed-text:v1.5      # RAG embeddings

# Hardware (for test runner CSV)
GPU_NAME=
CUDA_VERSION=
DRIVER_VERSION=
```

### CIS Benchmark PDF

Place `CIS_Kubernetes_Benchmark_V2.0.0_PDF.pdf` in the `docs/` folder. The RAG index is built on first run and cached in `.rag_cache/`.

---

## RAG / Embedding pipeline

The project uses Retrieval-Augmented Generation to give the patch LLM grounding in the CIS Kubernetes Benchmark instead of relying on model memorisation alone.

### 1. PDF chunking

The benchmark PDF is parsed page by page with [PyMuPDF](https://pymupdf.readthedocs.io/). Each chunk boundary is detected by a section-header regex (`N.N` or `N.N.N` followed by a title, e.g. `5.2.6 Ensure that root containers are not admitted`). Everything between two consecutive headers — the recommendation text, rationale, remediation steps, and any URLs — becomes one `RAGChunk`. Text is capped at 3 000 characters per chunk to avoid oversized embeddings. URLs found in the text are extracted separately and stored as `references`.

### 2. Embedding

Each chunk is embedded by calling Ollama's `/api/embed` endpoint with the `nomic-embed-text:v1.5` model. The section number and title are prepended to the chunk text before embedding so the model can encode structural context:

```text
5.2.6 Ensure that root containers are not admitted
<recommendation body...>
```

Chunks are sent to Ollama in batches of 32. The result is a float32 matrix of shape `(N_chunks, embedding_dim)`.

### 3. Index cache

Building the index takes a few minutes on first run. The result — chunks + embedding matrix — is serialised with `pickle` to `.rag_cache/<pdf-stem>_<mtime>.pkl`. Subsequent runs load the cache instantly. The cache key includes the PDF's modification time, so changing the PDF automatically triggers a rebuild.

### 4. Retrieval

For each Checkov finding, a query string is formed from the check ID, check name, and the manifest chunk that triggered it:

```text
CKV_K8S_6 Do not admit root containers
<yaml lines checkov flagged>
```

This query is embedded with the same model and compared against every chunk embedding using cosine similarity. The top-3 results above the confidence threshold (`MIN_CONFIDENCE_RAG_QUERIES = 0.75`) are returned. Results below the threshold are discarded to avoid injecting irrelevant context into the LLM prompt.

### 5. Usage in patch generation

The retrieved chunks are included verbatim in the patch-generation prompt alongside the manifest fragment, giving the LLM the exact CIS recommendation, remediation guidance, and reference URLs it needs to produce a correct and well-reasoned fix.

---

## Usage

### Streamlit UI

```bash
streamlit run app.py
```

Navigate to `http://localhost:8501`.

- **Chat tab** — ask questions about your cluster or issue commands
- **Analyze Manifest tab** — upload a YAML file or fetch a resource directly from the cluster, run static + semantic analysis, select fixes, download the patched manifest

### CLI

```bash
python analyze.py path/to/manifest.yaml > patched.yaml
```

Findings are printed to stderr; the patched manifest is written to stdout.

### Batch test runner

```bash
python run_tests.py           # 5 runs per manifest (default)
python run_tests.py --runs 3  # custom number of runs
```

Processes every manifest under `scenarios/real/`. For each run:
- Saves the patched YAML to `scenarios/real/output/<pid>/run{N}_patched_{name}.yaml`
- Appends one CSV row per LLM call to `test_results_<pid>.csv`

CSV columns: `timestamp`, `manifest_name`, `issue`, `test_run`, `language_model`, `gpu_name`, `cuda_version`, `driver_version`, `ollama_version`, `result`, `input_tokens`, `output_tokens`, `answer_latency_ms`, `load_latency_ms`, `patch`.

To run two instances in parallel safely, just launch the script in two terminals — each process writes to its own CSV and output subfolder.

---

## Skipping Checkov checks

Add check IDs to `skip_checks.json` to suppress them permanently:

```json
{ "skip": ["CKV_K8S_20", "CKV_K8S_22"] }
```

The Analyze tab also has a per-finding "Ignore forever" checkbox that writes to this file automatically.

---

## Remote Ollama (VM)

To point the project at a remote Ollama instance, set in `.env`:

```bash
OLLAMA_HOST=https://your-vm.example.com
OLLAMA_API_KEY=your-secret-key
```

On the VM, protect the Ollama port with an Nginx reverse proxy that validates the `Authorization: Bearer <key>` header and a TLS certificate from Let's Encrypt. Bind Ollama itself to `127.0.0.1` so it is not directly reachable from the internet.
