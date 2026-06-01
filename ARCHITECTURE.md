# Architecture & Design Decisions — Intelligent Triage Agent

This document covers all architectural choices, technology decisions, and design trade-offs.
For deployment instructions, see [DEPLOYMENT.md](./DEPLOYMENT.md).

---

## Table of Contents

1. [Architecture](#architecture)
2. [Technology Choices & Rationale](#technology-choices--rationale)
3. [Mock Tool: Options Considered](#mock-tool-options-considered)
4. [Scaling Considerations](#scaling-considerations)
5. [Security Posture](#security-posture)
6. [Phase-2 Roadmap](#phase-2-roadmap)
7. [Design Considerations](#design-considerations)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Kubernetes Cluster                      │
│  Namespace: triage-agent                                  │
│                                                           │
│  ┌───────────────────────────────────────────────────┐   │
│  │                  Pod (×2 replicas)                │   │
│  │                                                   │   │
│  │   ┌──────────┐    ┌──────────┐    ┌───────────┐   │   │
│  │   │  FastAPI  │───▶│  Agent   │───▶│ OpenAI API│   │   │
│  │   │  main.py  │    │ agent.py │    │ (external)│   │   │
│  │   └──────────┘    └────┬─────┘    └───────────┘   │   │
│  │                        │ MCP (stdio)               │   │
│  │                   ┌────▼──────┐  ┌──────────────┐  │   │
│  │                   │  McpHub   │─▶│ KB MCP server │  │   │
│  │                   │  (client) │  │ (same pod)    │  │   │
│  │                   └────┬──────┘  └───────┬───────┘  │   │
│  │                        │ optional      │          │   │
│  │                        ▼               ▼          │   │
│  │              ┌─────────────────┐  troubleshooting  │   │
│  │              │ K8s MCP (pod)   │  _docs.json       │   │
│  │              └─────────────────┘  (ConfigMap)     │   │
│  └───────────────────────────────────────────────────┘   │
│                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐   │
│  │  Service  │  │   HPA    │  │ConfigMap │  │ Secret  │   │
│  │(ClusterIP)│  │(2-10 pod)│  │(app cfg) │  │(API key)│   │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘   │
│                                                           │
│  ┌──────────────────────┐                                 │
│  │  Ingress / Route     │  ← external traffic entry       │
│  └──────────────────────┘                                 │
└─────────────────────────────────────────────────────────┘
```

### Request Flow

```
Client
  │
  │  POST /triage  {"500": "DB refused"}
  ▼
FastAPI (main.py)
  │  validates request with Pydantic
  ▼
Agent (agent.py)
  │  builds system + user prompt
  │
  │  ── Iteration 1 ────────────────────────────────────────────────────────
  │  sends messages[] + tool schema to OpenAI
  │◀─────── LLM responds: "I want to call get_troubleshooting_docs(500, DB)" ─┐
  │                                                                            │
  │  dispatches tool call → McpHub → KB MCP server (stdio)                     │
  │  keyword + code matching against troubleshooting_docs.json                 │
  │  returns top-3 matching runbook entries                                    │
  │                                                                            │
  │  appends tool result to messages[]  ───────────────────────────────────────┘
  │
  │  ── Iteration 2 ────────────────────────────────────────────────────────
  │  sends updated messages[] to OpenAI
  │◀─── LLM responds: final JSON answer (finish_reason="stop") ─────────────────
  │
  │  parses JSON → TriageResponse model
  ▼
FastAPI
  │  serialises and returns HTTP 200 JSON
  ▼
Client receives structured response
```

---

## Technology Choices & Rationale

### Language: Python 3.12

| Alternative | Why Python won |
|---|---|
| Go | Excellent performance, but less readable for LLM/AI patterns; no owner familiarity |
| Node.js | Good async support, but Python has richer LLM ecosystem |
| Java/Spring | Heavyweight — slow startup, large image, overkill for this scope |
| **Python** | Owner-familiar, large LLM SDK ecosystem, readable async code, fast iteration |

### Web Framework: FastAPI

- **Async-native** — critical when the main work is waiting for an LLM API call (I/O-bound)
- Auto-generates **Swagger UI** at `/docs` for free
- **Pydantic** integration for request/response validation — single source of truth for the data contract
- **Lifespan context** (`asynccontextmanager`) creates one `AsyncOpenAI(timeout=30.0)` client at startup and stores it in `app.state.openai_client`. All requests share the same client (connection pooling, no per-request constructor overhead). The 30s timeout prevents indefinite hangs if OpenAI is slow or down.
- **`/metrics` endpoint** exposed via `prometheus-fastapi-instrumentator` — tracks request counts, latency histograms, and in-progress requests. Phase 2: deploy `kube-prometheus-stack` + Grafana dashboards for request rate, LLM latency, and confidence score distribution.
- Alternative considered: Flask — sync-by-default, no built-in schema generation

### LLM Integration: OpenAI SDK (direct, no framework)

| Alternative | Trade-off |
|---|---|
| LangChain | Abstracts tool-calling nicely, but adds ~50 MB of dependencies and significant magic |
| LlamaIndex | Better for RAG pipelines, overkill here |
| **Direct OpenAI SDK** | Transparent, minimal, easy to read and explain, easy to swap provider |

The agent loop is only ~30 lines — a framework would cost more than it saves.

### LLM Provider: OpenAI (gpt-4o-mini default)

- **gpt-4o-mini**: cheap (~$0.00015/1k input tokens), fast, reliable tool-calling
- **gpt-4o**: higher accuracy for ambiguous errors, ~20× more expensive
- **Ollama (local)**: no API cost, air-gapped, but requires GPU or much slower CPU inference
  - To use Ollama: set `OPENAI_API_KEY=ollama`, point the base URL in `agent.py` to your Ollama endpoint

---

## Mock Tool: Options Considered

The exercise requires at least one Tool the agent can call. Here are the four realistic options, with trade-offs:

### Option A: Hardcoded In-Memory Dict

```python
DOCS = {
    "500": {"action_items": ["Restart Pod", ...]},
    ...
}
```

**Pros:** Zero dependencies, instant, dead-simple to read.
**Cons:** Updating docs requires a code change and image rebuild. Not realistic.

---

### Option B: JSON Config File ✅ (CHOSEN)

The docs live in `data/troubleshooting_docs.json`, loaded at startup and cached in memory. In Kubernetes, the file is mounted from a ConfigMap, so you can update the knowledge base by running:

```bash
make k8s-kb
```

…and rolling-restarting the pods — no image rebuild needed.

**Pros:** Config-driven, no code change to update docs, realistic KB pattern, ConfigMap-mountable, easy to version-control the docs separately.
**Cons:** Module-level cache means a pod restart is needed after updates. Not queryable (full scan). No fuzzy matching.

---

### Option C: SQLite Database

Docs stored in a SQLite file; agent queries with SQL.

**Pros:** Queryable (LIKE, FTS5), realistic DB pattern, persistent across pod restarts.
**Cons:** SQLite doesn't work well across replicas (file locking). Adds complexity. Overkill for ~10 runbook entries.

---

### Option D: Separate Mock HTTP Microservice

A second FastAPI/Flask service that exposes a `/docs?code=500` endpoint, deployed as its own K8s Deployment.

**Pros:** True service-to-service architecture, demonstrates K8s service discovery, most realistic for a real enterprise scenario.
**Cons:** Two deployments to manage. More YAML. Harder to set up for a demo. Adds a network failure mode.

**Verdict:** JSON + ConfigMap is the pragmatic choice — it's realistic (ConfigMap-backed knowledge bases are common), easy to demo, and keeps the stack simple. Option D is a clean Phase-2 upgrade path.

---

## Scaling Considerations

The system was designed to scale horizontally from day one:

### Stateless Pods

The agent has **no local state** between requests. Every request is self-contained:
- The LLM call is to an external API.
- The KB JSON is read-only, loaded at startup.
- No session affinity is needed — any pod can handle any request.

This means adding replicas via the HPA immediately increases throughput linearly.

### HPA Configuration

> **Metrics Server prerequisite:** HPA is built into Kubernetes — no operator needed. But it requires Metrics Server to read pod CPU/memory metrics. On Minikube, run `make minikube-setup` or `minikube addons enable metrics-server`. On OpenShift, it's pre-installed. Without it, the HPA object applies but shows `<unknown>` metrics and won't scale.

The `hpa.yaml` scales between **2 and 10 replicas** based on CPU (>60%) and memory (>75%). Under a traffic spike:
- Scale-up: triggered within 30 seconds, adds up to 2 pods/minute.
- Scale-down: waits 5 minutes to avoid oscillation ("flapping").

### Bottleneck: OpenAI API Rate Limits

At scale, the real bottleneck is the **OpenAI API rate limit** (requests per minute and tokens per minute). Mitigations:
- Use `gpt-4o-mini` (much higher RPM limits than `gpt-4o`).
- Add a request queue (e.g. Redis + Celery or Kafka) in front of the agent for burst handling.
- Use the OpenAI Batch API for non-real-time workloads.

### Phase-2 Scaling Path

```
Current:  FastAPI → OpenAI (synchronous per-request)

Phase 2:  API Gateway → Message Queue (Kafka/RabbitMQ)
                              ↓
                        Worker Pool (N agents consuming from queue)
                              ↓
                        Results store (Redis / DB)
                              ↓
                        Webhook / polling for async response
```

This decouples ingestion rate from LLM throughput and allows independent scaling of the API layer vs. the agent workers.

---

## Security Posture

### Phase-1 (this implementation)

| Control | Status | Notes |
|---|---|---|
| Non-root container user (UID 1001) | ✅ | Enforced in Dockerfile + securityContext |
| Read-only root filesystem | ✅ | `readOnlyRootFilesystem: true` in Deployment |
| Drop all Linux capabilities | ✅ | `capabilities.drop: [ALL]` |
| No privilege escalation | ✅ | `allowPrivilegeEscalation: false` |
| Dedicated ServiceAccount | ✅ | No access to K8s API, token not auto-mounted |
| API key in K8s Secret | ✅ | Not in ConfigMap, not in image |
| `.env` in `.gitignore` | ✅ | Prevents accidental key commit |
| seccompProfile: RuntimeDefault | ✅ | Reduces kernel attack surface |
| Content moderation (OpenAI Moderation API) | ✅ | Pre-flight check on every request. **Covers:** hate speech, harassment, self-harm, sexual content, violence. **Does NOT cover:** prompt injection or adversarial instructions (different mitigation layer — Phase 2). Adds ~100–300 ms latency per request (accepted trade-off). |

### Phase-2 Security Improvements

| Improvement | Tool | Why |
|---|---|---|
| Encrypted secrets at rest | External Secrets Operator + Vault/AWS Secrets Manager | K8s Secrets are base64, not encrypted by default |
| GitOps-safe secrets | Sealed Secrets (Bitnami) or SOPS | Allows committing encrypted YAML to git |
| TLS termination | cert-manager + Let's Encrypt | HTTPS for the Ingress |
| Network policies | `NetworkPolicy` objects | Restrict pod-to-pod and egress traffic |
| Image signing | cosign (Sigstore) | Verify the image hasn't been tampered with |
| SBOM generation | Syft + Grype | Know what's in your image, scan for CVEs |
| Rate limiting | Ingress annotations (`limit-rps`, `limit-connections`) | Prevent API abuse and OpenAI cost runaway — one annotation, zero code changes |

---

## Phase-2 Roadmap

| Feature | Description |
|---|---|
| **Async queue** | Decouple API from agent with Kafka/Redis for burst handling |
| **Local LLM (Ollama)** | Run llama3 or mistral in-cluster — no external API cost/dependency |
| **Vector DB RAG** | Replace keyword matching with semantic search (Chroma, Weaviate, pgvector) |
| **Multi-tool agent** | Add tools: `get_pod_logs`, `check_metrics`, `call_pagerduty` |
| **Webhook output** | POST the triage result to Slack / PagerDuty / Jira |
| **Metrics (Prometheus)** | `/metrics` endpoint already exposed via `prometheus-fastapi-instrumentator`. Phase 2: deploy Prometheus (helm install kube-prometheus-stack) + Grafana dashboards for request rate, LLM latency, confidence score distribution. |
| **Audit log** | Persist every triage result to a DB for trend analysis |
| **Separate KB microservice** | Split the mock tool into its own Deployment with its own scaling policy |
| **Encrypted secrets** | ESO + Vault / Sealed Secrets |
| **Rate limiting** | Ingress-level per-IP limiting (nginx annotations) + exponential backoff in `agent.py` for OpenAI 429 responses |
| **CI/CD pipeline** | GitHub Actions: test → build → push → deploy |

---

## Design Considerations

### `docs_consulted` — tool-sourced, not LLM-claimed

`TriageResponse.docs_consulted` is populated from the **actual tool call results** — specifically, the `matched_entries[].title` fields returned by `get_troubleshooting_docs()`. The LLM's own `docs_consulted` field in its JSON output is ignored.

This prevents hallucination: the LLM can claim to have consulted any document, but the actual tool results are the ground truth. If `docs_consulted` is empty in a response, it means `get_troubleshooting_docs()` returned zero entries — the KB ConfigMap is likely empty. Fix: `make k8s-kb`.

---

### KB Pre-fetch vs. Tool-Calling Pattern

The current design makes **2 LLM calls per request**: iteration 0 forces a tool call (KB lookup), iteration 1 produces the final answer. An alternative design would pre-fetch the KB results *before* the first LLM call and inject them directly into the system prompt, reducing to **1 LLM call** (~500ms–2s latency saved, ~50% token cost reduction).

This optimization is intentionally not implemented. The exercise explicitly requires the agent to demonstrate **tool-calling behaviour** — pre-fetching would remove that pattern entirely. This section exists to show awareness of the trade-off, not as a future work item.
