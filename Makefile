# ─────────────────────────────────────────────────────────────────────────────
# Intelligent Triage Agent — Makefile
#
# All operations are documented here. Run `make help` to see them.
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_NAME   ?= intelligent-triage-agent
# Auto-generate a timestamp tag for every deploy (format: YYYYMMDD-HHMM).
# Override with: make deploy-minikube IMAGE_TAG=my-tag
# Use IMAGE_TAG=latest to keep the old behaviour.
IMAGE_TAG    ?= $(shell date +%Y%m%d-%H%M)
REGISTRY     ?=                     # e.g. quay.io/myorg  — set via env or CLI arg: make push REGISTRY=quay.io/myorg
NAMESPACE    ?= triage-agent
K8S_DIR      := k8s
# Python 3.12 required — pydantic-core wheels are not yet available for 3.13/3.14.
# The Dockerfile uses python:3.12-slim for the same reason.
PYTHON       := $(shell command -v python3.12 2>/dev/null || echo python3)
PORT         := 8080

# Full image reference: if REGISTRY is set, prefix it.
IMAGE_REF    := $(if $(REGISTRY),$(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG),$(IMAGE_NAME):$(IMAGE_TAG))

# Container tool — override with: make build CONTAINER_TOOL=podman
# Auto-detects podman if docker is not available.
CONTAINER_TOOL ?= $(shell command -v docker 2>/dev/null || command -v podman 2>/dev/null | xargs basename 2>/dev/null || echo docker)

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  Intelligent Triage Agent"
	@echo "  ─────────────────────────────────────────────────────────────────"
	@echo "  Local development"
	@echo "    make install        Install requirements.txt + requirements-test.txt into a venv"
	@echo "    make run            Run the app locally (no Docker)"
	@echo "    make test           Run unit tests"
	@echo ""
	@echo "  Docker"
	@echo "    make build          Build the Docker image"
	@echo "    make push           Push image to REGISTRY (set REGISTRY=...)"
	@echo "    make shell          Open a shell inside the running container"
	@echo ""
	@echo "  Kubernetes"
	@echo "    make deploy-minikube  Full Minikube deploy in one command (setup → build → load → apply → status)"
	@echo "    make minikube-setup      Enable required Minikube addons (ingress + metrics-server) — run once before k8s-apply"
	@echo "    make minikube-load       Save + load image into Minikube (Podman-compatible; alternative to eval \$\$(minikube docker-env))"
	@echo "    make minikube-load-sudo  Load image using sudo $(CONTAINER_TOOL) — use when image was built with sudo podman"
	@echo "    make k8s-apply      Apply all manifests (namespace → service account → config → deploy)  [run make minikube-setup first]"
	@echo "    make k8s-secret     Create the OpenAI secret from OPENAI_API_KEY env var"
	@echo "    make k8s-kb         Update the knowledge-base ConfigMap from local JSON"
	@echo "    make k8s-restart    Rolling-restart the Deployment"
	@echo "    make k8s-status     Show pod / deployment / HPA status"
	@echo "    make k8s-logs       Tail logs from all pods"
	@echo "    make k8s-delete     Delete all resources in the namespace"
	@echo "    make k8s-forward    Port-forward the service to localhost:$(PORT)"
	@echo ""
	@echo "  Misc"
	@echo "    make smoke-test     POST a sample error log to the running service"
	@echo "    make clean          Remove build artefacts and venv"
	@echo ""
	@echo "  Variables (override on CLI or in env):"
	@echo "    IMAGE_NAME=$(IMAGE_NAME)  IMAGE_TAG=$(IMAGE_TAG)  REGISTRY=$(REGISTRY)"
	@echo "    NAMESPACE=$(NAMESPACE)   PORT=$(PORT)"
	@echo "    CONTAINER_TOOL=$(CONTAINER_TOOL)  (auto-detected; override with CONTAINER_TOOL=podman)"
	@echo ""

# ── Local development ─────────────────────────────────────────────────────────
.PHONY: install
install:
	@python_ver=$$($(PYTHON) --version 2>&1); \
	echo "  Using: $$python_ver"; \
	echo "$$python_ver" | grep -q "3\.12" || echo "  WARNING: Python 3.12 recommended. pydantic-core has no wheels for 3.13/3.14 yet."
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip setuptools wheel
	.venv/bin/pip install -r requirements.txt -r requirements-test.txt
	@echo ""
	@echo "  Production deps: requirements.txt"
	@echo "  Test deps:       requirements-test.txt (not in Docker image)"
	@echo "  Activate with:   source .venv/bin/activate"

.PHONY: run
run: _require-env
	@echo "→ Starting app locally on port $(PORT)…"
	PYTHONPATH=. .venv/bin/uvicorn app.main:app \
		--host 0.0.0.0 --port $(PORT) --reload --log-level info

.PHONY: test
test:
	@echo "→ Running tests (requires requirements-test.txt to be installed)…"
	PYTHONPATH=. .venv/bin/pytest tests/ -v

# ── Docker ────────────────────────────────────────────────────────────────────
.PHONY: build
build:
	@echo "→ Building image $(IMAGE_REF) with $(CONTAINER_TOOL)…"
	$(CONTAINER_TOOL) build --tag $(IMAGE_REF) .

.PHONY: push
push: build
	@[ -n "$(REGISTRY)" ] || (echo "ERROR: REGISTRY is not set. Example: make push REGISTRY=quay.io/myorg" && exit 1)
	$(CONTAINER_TOOL) push $(IMAGE_REF)

.PHONY: shell
shell:
	$(CONTAINER_TOOL) exec -it triage-agent /bin/sh

# ── Kubernetes ────────────────────────────────────────────────────────────────
.PHONY: minikube-setup
minikube-setup:
	@echo "→ Enabling Minikube addons required for this project…"
	minikube addons enable ingress
	minikube addons enable metrics-server
	@echo "→ Waiting for metrics-server to be ready…"
	kubectl rollout status deployment/metrics-server -n kube-system --timeout=90s
	@echo "✓ Minikube is ready. Run: make k8s-apply"

.PHONY: minikube-load
minikube-load:
	@echo "→ Loading image into Minikube (Podman-compatible approach)…"
	@echo "  Note: if you built with 'sudo podman build', run this with sudo too: sudo make minikube-load"
	$(CONTAINER_TOOL) save $(IMAGE_REF) | minikube image load --overwrite=true -
	@echo "✓ Image loaded into Minikube"

.PHONY: minikube-load-sudo
minikube-load-sudo:
	@echo "→ Saving image to /tmp/triage-agent.tar with sudo $(CONTAINER_TOOL)…"
	sudo $(CONTAINER_TOOL) save $(IMAGE_REF) -o /tmp/triage-agent.tar
	@echo "→ Loading image into Minikube from file (more reliable than pipe)…"
	minikube image load /tmp/triage-agent.tar
	@echo "→ Verifying image is visible in Minikube…"
	minikube image ls | grep $(IMAGE_NAME) || echo "WARNING: image not found in minikube — check the load step"
	@echo "✓ Done"

.PHONY: k8s-apply
k8s-apply:
	@echo "→ Applying Kubernetes manifests…"
	kubectl apply -f $(K8S_DIR)/namespace.yaml
	@echo "→ Creating/updating OpenAI secret…"
	$(MAKE) k8s-secret
	kubectl apply -f $(K8S_DIR)/serviceaccount.yaml
	kubectl apply -f $(K8S_DIR)/configmap.yaml
	# NOTE: secret.yaml is NOT applied here — it contains only a placeholder key.
	# The real secret is always managed by 'make k8s-secret' above.
	kubectl apply -f $(K8S_DIR)/deployment.yaml
	kubectl apply -f $(K8S_DIR)/service.yaml
	kubectl apply -f $(K8S_DIR)/hpa.yaml
	@echo ""
	@echo "→ Waiting for rollout…"
	kubectl rollout status deployment/triage-agent -n $(NAMESPACE) --timeout=180s

.PHONY: k8s-secret
k8s-secret: _require-apikey
	@echo "→ Creating/updating OpenAI secret from OPENAI_API_KEY…"
	kubectl create secret generic triage-agent-secret \
		--namespace=$(NAMESPACE) \
		--from-literal=OPENAI_API_KEY=$(OPENAI_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -

.PHONY: k8s-kb
k8s-kb:
	@echo "→ Updating knowledge-base ConfigMap from local JSON…"
	kubectl create configmap triage-kb \
		--namespace=$(NAMESPACE) \
		--from-file=troubleshooting_docs.json=data/troubleshooting_docs.json \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "→ Restarting pods to reload the cache…"
	kubectl rollout restart deployment/triage-agent -n $(NAMESPACE)

.PHONY: k8s-restart
k8s-restart:
	kubectl rollout restart deployment/triage-agent -n $(NAMESPACE)
	kubectl rollout status deployment/triage-agent -n $(NAMESPACE) --timeout=180s

.PHONY: k8s-status
k8s-status:
	@echo "── Pods ──────────────────────────────────────────────────────────────"
	kubectl get pods -n $(NAMESPACE) -o wide
	@echo ""
	@echo "── Deployment ────────────────────────────────────────────────────────"
	kubectl get deployment triage-agent -n $(NAMESPACE)
	@echo ""
	@echo "── HPA ───────────────────────────────────────────────────────────────"
	kubectl get hpa -n $(NAMESPACE)
	@echo ""
	@echo "── Service / Ingress ─────────────────────────────────────────────────"
	kubectl get svc,ingress -n $(NAMESPACE)

.PHONY: k8s-logs
k8s-logs:
	kubectl logs -n $(NAMESPACE) -l app=triage-agent --all-containers --follow --tail=100

.PHONY: k8s-delete
k8s-delete:
	@echo "WARNING: This will delete all resources in namespace $(NAMESPACE)."
	@read -p "Continue? [y/N] " ans && [ "$${ans}" = "y" ]
	kubectl delete namespace $(NAMESPACE)

.PHONY: k8s-forward
k8s-forward:
	@echo "→ Forwarding service to http://localhost:$(PORT)"
	kubectl port-forward svc/triage-agent-svc $(PORT):80 -n $(NAMESPACE)

.PHONY: deploy-minikube
deploy-minikube:
	$(eval _TAG     := $(shell date +%Y%m%d-%H%M))
	$(eval _API_KEY := $(or $(OPENAI_API_KEY),$(shell grep '^OPENAI_API_KEY=' .env 2>/dev/null | cut -d= -f2)))
	@[ -n "$(_API_KEY)" ] || (echo "ERROR: OPENAI_API_KEY not found. Set it in .env or export it." && exit 1)
	@echo "$(_API_KEY)" | grep -q "^sk-your" && (echo "ERROR: OPENAI_API_KEY is still the placeholder. Edit .env with your real key." && exit 1) || true
	@echo "═══════════════════════════════════════════════════"
	@echo "  Full Minikube deployment — intelligent-triage-agent"
	@echo "  Image tag : $(_TAG)"
	@echo "  API key   : $$(echo $(_API_KEY) | cut -c1-14)..."
	@echo "═══════════════════════════════════════════════════"
	@echo ""
	@echo "Step 1/5: Enable Minikube addons (ingress + metrics-server)…"
	$(MAKE) minikube-setup
	@echo ""
	@echo "Step 2/5: Build container image [$(IMAGE_NAME):$(_TAG)]…"
	sudo --preserve-env=OPENAI_API_KEY $(MAKE) build CONTAINER_TOOL=$(CONTAINER_TOOL) IMAGE_TAG=$(_TAG)
	@echo ""
	@echo "Step 3/5: Load image into Minikube…"
	sudo rm -f /tmp/triage-agent.tar
	$(MAKE) minikube-load-sudo IMAGE_TAG=$(_TAG)
	@echo ""
	@echo "Step 4/5: Deploy all Kubernetes manifests…"
	$(MAKE) k8s-apply OPENAI_API_KEY=$(_API_KEY)
	@echo "→ Uploading knowledge base docs to ConfigMap…"
	$(MAKE) k8s-kb
	@echo "→ Updating deployment to use image: localhost/$(IMAGE_NAME):$(_TAG)…"
	kubectl set image deployment/triage-agent triage-agent=localhost/$(IMAGE_NAME):$(_TAG) -n $(NAMESPACE)
	kubectl rollout status deployment/triage-agent -n $(NAMESPACE) --timeout=180s
	@echo ""
	@echo "Step 5/5: Set default namespace + show status…"
	kubectl config set-context --current --namespace=triage-agent
	$(MAKE) k8s-status
	@echo ""
	@echo "═══════════════════════════════════════════════════"
	@echo "  Deployment complete! Image: $(IMAGE_NAME):$(_TAG)"
	@echo "  Run in a separate terminal: make k8s-forward"
	@echo "  Then test:                  make smoke-test"
	@echo "═══════════════════════════════════════════════════"

# ── Smoke test ────────────────────────────────────────────────────────────────
.PHONY: smoke-test
smoke-test:
	@echo "→ Sending test request to http://localhost:$(PORT)/triage…"
	curl -s -X POST http://localhost:$(PORT)/triage \
		-H "Content-Type: application/json" \
		-d '{"500": "DB connection refused to postgres:5432"}' \
		| python3 -m json.tool

# ── Helpers ───────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	rm -rf .venv __pycache__ app/__pycache__ tests/__pycache__ .pytest_cache

# Guard: .env must exist for local runs
.PHONY: _require-env
_require-env:
	@[ -f .env ] || (echo "ERROR: .env file not found. Copy .env.example → .env and fill in OPENAI_API_KEY." && exit 1)

# Guard: OPENAI_API_KEY must be set for secret creation
.PHONY: _require-apikey
_require-apikey:
	@[ -n "$(OPENAI_API_KEY)" ] || (echo "ERROR: OPENAI_API_KEY is not set. Run: export OPENAI_API_KEY=sk-..." && exit 1)
	@echo "$(OPENAI_API_KEY)" | grep -q "^sk-your" && (echo "ERROR: OPENAI_API_KEY is still the placeholder. Set a real key: export OPENAI_API_KEY=sk-..." && exit 1) || true
	@echo "  Using API key: $$(echo $(OPENAI_API_KEY) | cut -c1-12)..."
