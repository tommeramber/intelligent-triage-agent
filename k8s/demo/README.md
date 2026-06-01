# Triage demo workloads (`triage-demo` namespace)

Three isolated scenarios for Minikube + **hardened in-cluster** Kubernetes MCP. The namespace is opted in with `triage.agent-accessible=true` so RBAC and the agent allowlist permit read-only inspection.

| # | Manifest | Symptom | What to inspect |
|---|----------|---------|-----------------|
| 1 | `01-crashloop-missing-config.yaml` | `CrashLoopBackOff` | Pod `api-missing-config` — missing `REQUIRED_CONFIG` |
| 2 | `02-oom-low-limit.yaml` | `OOMKilled` | Pod `worker-oom` — memory limit 48Mi |
| 3 | `03-db-wrong-host.yaml` | Connection errors | Logs on `app-wrong-db-host` — DNS `postgres-wrong` |

## One-command setup

```bash
# From repo root, with OPENAI_API_KEY in .env or env
make demo-up
```

`demo-up` keeps the HPA installed but pins it to **2 replicas** (`minReplicas=maxReplicas=2`) so rollouts stay stable. To restore normal autoscaling (2–10 replicas on CPU/memory):

```bash
make k8s-hpa-unfreeze
```

## Validation (after setup)

```bash
make k8s-forward          # terminal 1
make smoke-test-all       # terminal 2 — all POST /triage demo scenarios
kubectl get pods -n triage-demo
kubectl get rolebinding triage-agent-workload-read -n triage-demo
```

## Remove

```bash
make demo-delete
```
