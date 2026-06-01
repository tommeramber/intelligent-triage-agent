# Developer Guide: Intelligent Triage Agent

This is your step-by-step companion. Read it top-to-bottom the first time,
then use it as a reference. Every file mentioned is explained, every decision
is justified, and every command is shown in context.

---

## Part 1 — Understanding the Design

### What problem are we solving?

When a system error occurs, a human SRE has to:
1. Read the error log and understand what it means.
2. Look it up in the company wiki / runbook.
3. Decide on remediation steps.
4. Act (or escalate).

This tool automates steps 1-3 by having an LLM reason over the error, consult
a mock "Company Troubleshooting Knowledge Base", and return a structured JSON
with a summary, confidence score, and action items.

---

### The Agent Pattern (why it matters)

The key insight of this exercise is **tool-calling**. The LLM doesn't just
answer from memory — it can **call functions** you define and incorporate
the results before answering.

```
LLM alone:                    LLM + Tool (agent):
─────────                     ───────────────────
"Given 500 error…             "Given 500 error, let me
 I think it might              look up the KB first…
 be a DB issue."               [calls get_troubleshooting_docs]
                               …ok, the docs say this.
                               Based on those docs: [answer]"
```

The LLM is the **reasoner**. The tool is the **knowledge source**.
You control what knowledge the agent can access, which means you control
the output without fine-tuning the model.

---

### Why Python + FastAPI?

| Criterion | Python + FastAPI |
|---|---|
| Owner familiarity | ✅ You know Python |
| Async support | ✅ Native async/await — critical for LLM I/O |
| LLM ecosystem | ✅ Best-in-class (openai, anthropic, langchain all Python-first) |
| Readability | ✅ Clean, minimal boilerplate |
| Auto-generated docs | ✅ Swagger UI at /docs for free |

---

## Part 2 — File-by-File Walkthrough

### `app/config.py` — The Settings Object

Everything configurable lives here. The `Settings` class reads from
environment variables automatically. If you add a new setting:

```python
my_new_setting: str = Field(default="value")
```

…it becomes available as the env var `MY_NEW_SETTING`.

The `@lru_cache` on `get_settings()` means the `.env` file is read exactly
once per process — no repeated disk I/O on every request.

In Kubernetes, these values come from the ConfigMap (non-sensitive) and
Secret (sensitive). The code doesn't need to change — only the source of the
env var changes.

---

### `app/models.py` — Data Contracts

Pydantic models define what the API accepts and what it returns.
They serve three purposes simultaneously:
1. **Validation** — reject bad input at the boundary (before it reaches your logic)
2. **Serialisation** — convert Python objects to/from JSON automatically
3. **Documentation** — FastAPI reads these models to generate the Swagger UI

If a caller sends a `confidence_score` of 150, Pydantic rejects it with a
clear 422 error before the agent even runs.

---

### `data/troubleshooting_docs.json` — The Knowledge Base

This is the "Company Troubleshooting Docs" the exercise requires.
Each entry follows this schema:

```json
{
  "id": "unique-identifier",
  "title": "Human-readable title",
  "error_codes": ["500"],          ← matched exactly against the input
  "keywords": ["db", "refused"],   ← matched as substrings in the description
  "description": "...",
  "common_causes": ["..."],
  "action_items": ["..."],         ← what the agent will recommend
  "confidence_boost": 20,          ← how much this entry boosts the score
  "severity": "critical"
}
```

**To add a new runbook entry:** edit the JSON file and run `make k8s-kb`
to push the update to Kubernetes. No image rebuild needed.

---

### `app/tools.py` — The Mock Tool

This is where the tool the LLM calls is defined. Two things live here:

**1. The Python function:**
```python
def get_troubleshooting_docs(error_code: str, description: str = "") -> dict:
```
This loads the JSON file once at startup (cached), then scores each entry
by how well it matches the error_code and description keywords.

**2. The OpenAI tool schema (`TOOL_SCHEMA`):**
This is a JSON object that describes the function to the LLM — its name,
what it does, and what parameters it expects. The LLM reads this description
to decide when and how to call the function.

Key insight: **the LLM never directly calls your Python code**.
It returns a JSON "tool call" message. Your code intercepts that message,
runs the Python function, and feeds the result back to the LLM.

```
LLM → "I want to call get_troubleshooting_docs(500, 'db refused')"
Your code → actually runs get_troubleshooting_docs(500, 'db refused')
Your code → sends result back to LLM
LLM → now answers with the knowledge from the tool result
```

---

### `app/agent.py` — The Reasoning Loop

The agent loop is the heart of the system. Here is what happens on every request:

```
messages = [system_prompt, user_message]

loop (up to 3 iterations):
    response = openai.chat.completions.create(messages, tools=[TOOL_SCHEMA])

    if response.finish_reason == "tool_calls":
        # LLM wants to call a tool
        run the tool
        append tool result to messages
        continue loop

    if response.finish_reason == "stop":
        # LLM gave a final answer
        parse JSON from response.content
        return TriageResponse
```

The `temperature=0.2` setting makes the LLM more deterministic — important
for a triage system where you want consistent, predictable output.

The `SYSTEM_PROMPT` constant tells the LLM:
- Its role (SRE first-responder)
- Its rules (ALWAYS call the tool first)
- The exact output format (JSON schema)

Keeping the system prompt as a module-level constant means you can version
it, A/B test it, or swap it without touching business logic.

---

### `app/main.py` — The API Layer

FastAPI wires everything together:

```
GET  /         → HTML UI (for manual testing in a browser)
GET  /health   → {"status": "ok"}  ← Kubernetes uses this for probes
POST /triage   → calls agent.run_triage() → returns TriageResponse
GET  /docs     → Swagger UI (automatic)
```

The `log_requests` middleware logs every request with its duration in
milliseconds — useful for spotting slow LLM responses.

---

## Part 3 — Kubernetes Architecture

### Why these resources?

| Resource | Purpose |
|---|---|
| `Namespace` | Isolation — everything for this app lives in `triage-agent` |
| `ServiceAccount` | Least-privilege identity for the pod (no K8s API access) |
| `ConfigMap (config)` | Non-sensitive app settings (model name, log level, etc.) |
| `ConfigMap (triage-kb)` | The knowledge base JSON — updatable without an image rebuild |
| `Secret` | OpenAI API key — base64 encoded, not in ConfigMap |
| `Deployment` | Manages the pod replicas, rolling updates, health checks |
| `Service (ClusterIP)` | Internal stable DNS name for the pods |
| `Ingress` | Exposes the service to external traffic via a hostname |
| `HPA` | Automatically scales replicas 2→10 based on CPU/memory |

### The Secret Strategy (Phase 1 vs Phase 2)

**Phase 1 (current):** The OpenAI API key is stored in a Kubernetes Secret.
This is base64-encoded (not encrypted) but:
- It's not in your git repo (the file has a placeholder)
- It's not in the container image
- `make k8s-secret` creates it directly from your shell environment variable

```bash
export OPENAI_API_KEY=sk-your-real-key
make k8s-secret    # creates/updates the Secret in the cluster
```

**Phase 2:** Use one of:
- **Sealed Secrets** (Bitnami) — encrypt the secret with a cluster key so
  the encrypted YAML is safe to commit to git (GitOps-friendly)
- **External Secrets Operator** — sync secrets from Vault / AWS Secrets
  Manager / Azure Key Vault into K8s Secrets automatically
- **OpenShift Credentials** — OCP's native credential injection

---

## Part 4 — Running the System

### Step-by-step: First Run (Local)

```bash
# 1. Enter the project
cd ~/intelligent-triage-agent

# 2. Create your .env file with your API key
cp .env.example .env
# Edit .env — set OPENAI_API_KEY=sk-your-key

# 3. Create the virtual environment and install dependencies
make install

# 4. Activate the venv
source .venv/bin/activate

# 5. Start the app
make run
```

You should see:
```
INFO:     Started server process [12345]
INFO:     Uvicorn running on http://0.0.0.0:8080
```

Open http://localhost:8080 in your browser.
Click one of the quick-example buttons, then click "Analyse".

### Step-by-step: First Run (Kubernetes / Minikube)

```bash
# Prerequisites:
#   minikube start
#   minikube addons enable ingress
#   export OPENAI_API_KEY=sk-your-key

# Build image inside Minikube's Docker daemon
eval $(minikube docker-env)
make build

# Deploy everything
make k8s-apply        # creates namespace, configmap, secret, deployment, service, hpa

# Check pods are Running
make k8s-status

# Forward the service port to your laptop
make k8s-forward
# In another terminal:
make smoke-test-all   # runs all POST /triage demo scenarios (payload, response, MCP/tools per case)
```

### Updating the Knowledge Base Without Rebuilding

```bash
# Edit the docs
vim data/troubleshooting_docs.json

# Push to cluster and restart pods to reload
make k8s-kb
```

---

## Part 5 — Mock Tool: Options We Chose Between

The exercise says the agent must have at least one tool — a mock function
that retrieves "Company Troubleshooting Docs". Here are the four approaches
we evaluated:

### Option A: Hardcoded Dict (rejected)
```python
DOCS = {"500": {"action_items": ["Restart Pod"]}}
```
Simple but static. Any update requires code change + image rebuild.

### Option B: JSON File ✅ (chosen)
The KB lives in `data/troubleshooting_docs.json`, mounted from a Kubernetes
ConfigMap. Update via `make k8s-kb`. Realistic pattern used in real runbook
systems. No extra dependencies.

### Option C: SQLite (rejected for now)
More powerful querying, but SQLite + multiple replicas = file-locking hell.
A good Phase-2 option if you switch to PostgreSQL.

### Option D: Separate HTTP Microservice (Phase 2)
The most architecturally correct: a second K8s Deployment exposes
`GET /docs?code=500`. The triage agent calls it over the cluster network.
Adds realism and demonstrates K8s service discovery, but too complex for
the initial implementation. Clear upgrade path.

---

## Part 6 — Scaling and Performance Notes

### Where is the bottleneck?

For this workload the bottleneck is almost always the **OpenAI API latency**
(typically 1-5 seconds per request). The agent itself adds only a few
milliseconds.

### What happens under high load?

```
10 concurrent requests
        ↓
FastAPI handles them concurrently (async/await — no threads needed)
        ↓
10 parallel outbound HTTPS calls to api.openai.com
        ↓
OpenAI rate limits kick in if you exceed your RPM (requests per minute)
        ↓
→ Add replica pods → more outbound connections, but same total RPM cap
→ Real fix: request queue + worker pool (Phase 2)
```

### CPU/Memory sizing rationale

The Deployment requests `100m` CPU and `128Mi` memory.
These are comfortable for:
- Idle state: ~10m CPU, ~80Mi memory (Python + FastAPI baseline)
- Under load: ~200m CPU (JSON parsing, async I/O), ~150Mi memory

The HPA scales at 60% CPU average, giving headroom before throttling.

---

## Part 7 — Code Quality Checklist

Before submitting or presenting:

- [ ] `make install && make run` starts cleanly
- [ ] `make smoke-test-all` reports 3/3 scenarios passed
- [ ] `make test` all tests pass
- [ ] `make build` produces a working Docker image
- [ ] `make k8s-apply && make k8s-status` shows pods Running
- [ ] Swagger UI at `/docs` matches the expected request/response shapes
- [ ] The HTML UI at `/` works with all four example buttons
- [ ] `kubectl logs` shows clean startup with no errors
- [ ] The HPA is created: `kubectl get hpa -n triage-agent`
