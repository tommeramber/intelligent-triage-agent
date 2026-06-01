# Hardened Kubernetes access for triage-agent

Three layers (easy to explain in an interview):

1. **Opt-in label** — `triage.agent-accessible=true` on a namespace (see `k8s/demo/namespace.yaml`).
2. **RBAC** — shared `ClusterRole` `triage-agent-workload-read`, bound only in labeled namespaces via `RoleBinding` (synced by `make k8s-sync-access`). Plus a narrow `ClusterRole` for listing namespace metadata to discover labels.
3. **Application** — Python K8s MCP server and agent hub refuse workload tools outside the allowlist (`K8S_ACCESS_ALLOWLIST`, patched by the sync script).

No cluster-wide read on pods/deployments. No write/exec/delete verbs.

## Apply

```bash
make k8s-rbac          # cluster roles + namespace discovery binding
make demo-apply        # demo namespace with opt-in label
make k8s-sync-access   # RoleBindings per labeled NS + ConfigMap allowlist
```

`make demo-up` runs all of the above.
