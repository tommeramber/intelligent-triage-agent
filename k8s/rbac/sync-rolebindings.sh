#!/usr/bin/env bash
# Sync RoleBindings for triage-agent-sa into every namespace labeled for agent access.
# Also patches the triage-agent ConfigMap allowlist used for hub-layer defense-in-depth.
set -euo pipefail

LABEL_KEY="${TRIAGE_ACCESS_LABEL_KEY:-triage.agent-accessible}"
LABEL_VALUE="${TRIAGE_ACCESS_LABEL_VALUE:-true}"
AGENT_NAMESPACE="${TRIAGE_AGENT_NAMESPACE:-triage-agent}"
CONFIGMAP="${TRIAGE_CONFIGMAP:-triage-agent-config}"
SA_NAME="${TRIAGE_SA_NAME:-triage-agent-sa}"
CLUSTER_ROLE="${TRIAGE_WORKLOAD_CLUSTER_ROLE:-triage-agent-workload-read}"

echo "→ Syncing RoleBindings (label ${LABEL_KEY}=${LABEL_VALUE})…"

namespaces="$(kubectl get namespaces -l "${LABEL_KEY}=${LABEL_VALUE}" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)"

if [ -z "${namespaces}" ]; then
  echo "  WARNING: no namespaces found with label ${LABEL_KEY}=${LABEL_VALUE}"
  allowlist=""
else
  allowlist="$(echo "${namespaces}" | tr ' ' ',')"
  echo "  Opted-in namespaces: ${allowlist}"
fi

for ns in ${namespaces}; do
  echo "  → RoleBinding in namespace ${ns}"
  kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: triage-agent-workload-read
  namespace: ${ns}
  labels:
    app.kubernetes.io/name: triage-agent
    app.kubernetes.io/component: rbac
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ${CLUSTER_ROLE}
subjects:
  - kind: ServiceAccount
    name: ${SA_NAME}
    namespace: ${AGENT_NAMESPACE}
EOF
done

echo "→ Patching ConfigMap ${CONFIGMAP} (K8S_ACCESS_ALLOWLIST)…"
kubectl patch configmap "${CONFIGMAP}" -n "${AGENT_NAMESPACE}" --type merge -p "{\"data\":{\"K8S_ACCESS_ALLOWLIST\":\"${allowlist}\"}}"

echo "✓ RBAC sync complete"
