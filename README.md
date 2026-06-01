# Intelligent Triage Agent

> AI-powered "First Responder" for system errors. Accepts a raw error log,
> consults a company knowledge base via tool-calling, and returns a structured
> triage report with summary, confidence score, and action items.

---

## Quick Start — Minikube (one command)

**Prerequisites:** Python 3.12, Podman or Docker, Minikube, kubectl, OpenAI API key.

```bash
# Put your key in .env — demo-up reads it automatically:
echo "OPENAI_API_KEY=sk-your-key" >> .env

make demo-up

# In a separate terminal:
make k8s-forward

# Test it (all demo scenarios in one pass):
make smoke-test-all
```

> **Image tagging:** every build is tagged with a timestamp (`YYYYMMDD-HHMM`). To verify
> which build is currently running in the cluster:
> ```bash
> kubectl get pods -o jsonpath='{.items[0].spec.containers[0].image}'
> ```

Full step-by-step guide: [DEPLOYMENT.md](./DEPLOYMENT.md)

---

## Quick Start — Local (no Kubernetes)

```bash
cp .env.example .env   # fill in OPENAI_API_KEY
make install
source .venv/bin/activate
make run
# → http://localhost:8080  (UI + Swagger at /docs)
```

---

## What it Does

The agent acts as an automated first-responder for system errors. POST a single-key JSON object
where the key is the error code and the value is the error description:

```bash
curl -X POST http://localhost:8080/triage \
  -H "Content-Type: application/json" \
  -d '{"500": "DB connection refused to postgres:5432"}'
```

Response:

```json
{
  "summary": "Database connection refused — postgres pod is unreachable from the app.",
  "confidence_score": 90,
  "action_items": [
    "Check DB pod status: kubectl get pods -l app=postgres -n <namespace>",
    "Test connectivity: kubectl exec -it <pod> -- nc -zv postgres 5432",
    "Verify DB_HOST env var in Deployment spec"
  ],
  "docs_useful": ["Database Connection Refused"],
  "docs_consulted": ["Database Connection Refused"],
  "kb_keyword_match": true,
  "raw_error": "500: DB connection refused to postgres:5432",
  "evidence_sources": {
    "knowledge_base": "mcp",
    "kubernetes": {
      "invoked": [],
      "status": "not_invoked",
      "message": null,
      "evidence_obtained": false
    }
  }
}
```

The agent uses **OpenAI tool-calling** (function calling) to autonomously decide when to query
the Company Troubleshooting Knowledge Base before forming its conclusion — mimicking the reasoning
pattern of a real SRE. The KB is exposed via a **stdio MCP server** in the same container (not a
separate pod). In Minikube, **hardened in-cluster Kubernetes MCP** runs in the same pod (Python
stdio server + namespace-scoped RBAC + `triage.agent-accessible` label opt-in). See
[ARCHITECTURE.md](./ARCHITECTURE.md), [k8s/rbac/README.md](./k8s/rbac/README.md), and
[k8s/demo/README.md](./k8s/demo/README.md).

---

## Project Structure

```
intelligent-triage-agent/
├── app/
│   ├── __init__.py         # package marker
│   ├── main.py             # FastAPI app, endpoints, middleware, HTML UI
│   ├── agent.py            # LLM reasoning loop (MCP-backed tools)
│   ├── kb/search.py        # KB domain logic (JSON matching)
│   ├── mcp/                # Stdio MCP client hub
│   ├── tools.py            # Re-exports KB schema (compat)
│   mcp_servers/kb_server.py  # KB MCP server (stdio, same container)
│   ├── models.py           # Pydantic request/response models
│   └── config.py           # Settings loaded from env vars / .env
├── data/
│   └── troubleshooting_docs.json   # Company KB mock data
├── k8s/
│   ├── namespace.yaml
│   ├── serviceaccount.yaml
│   ├── configmap.yaml      # app config + KB volume
│   ├── secret.yaml         # API key (Phase-1: base64 in cluster)
│   ├── deployment.yaml     # 2 replicas, security context, probes
│   ├── service.yaml        # ClusterIP + Ingress
│   └── hpa.yaml            # Autoscaler (2–10 replicas)
├── tests/
│   └── test_agent.py       # Unit + integration tests (offline, mocked LLM)
├── Dockerfile              # Multi-stage build, non-root user
├── Makefile                # All operations (run `make help`)
├── requirements.txt        # Pinned Python dependencies
├── .env.example            # Template for local config
└── README.md               # This file
```

---

## Documentation

| Document | Contents |
|---|---|
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Step-by-step deploy guide, all `make` commands, config reference, API reference, troubleshooting |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Technology decisions, ADRs, mock tool comparison, security posture, scaling strategy, phase-2 roadmap |
| [README.md](./README.md) | This file — overview and quick start |
