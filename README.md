# Intelligent Triage Agent

> AI-powered "First Responder" for system errors. Accepts a raw error log,
> consults a company knowledge base via tool-calling, and returns a structured
> triage report with summary, confidence score, and action items.

---

## Quick Start — Minikube (one command)

**Prerequisites:** Python 3.12, Podman or Docker, Minikube, kubectl, OpenAI API key.

```bash
# Put your key in .env — deploy-minikube reads it automatically:
echo "OPENAI_API_KEY=sk-your-key" >> .env

make deploy-minikube

# In a separate terminal:
make k8s-forward

# Test it:
make smoke-test
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
  "docs_consulted": ["Database Connection Refused", "HTTP 500 – Internal Server Error"],
  "raw_error": "500: DB connection refused to postgres:5432"
}
```

The agent uses **OpenAI tool-calling** (function calling) to autonomously decide when to query
the Company Troubleshooting Knowledge Base before forming its conclusion — mimicking the reasoning
pattern of a real SRE. See [ARCHITECTURE.md](./ARCHITECTURE.md) for design details.

---

## Project Structure

```
intelligent-triage-agent/
├── app/
│   ├── __init__.py         # package marker
│   ├── main.py             # FastAPI app, endpoints, middleware, HTML UI
│   ├── agent.py            # LLM reasoning loop (tool-calling agent)
│   ├── tools.py            # Mock KB tool + OpenAI tool schema
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
