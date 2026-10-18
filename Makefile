# Every command pins the kind context so nothing touches another cluster.
CLUSTER  := notes
CTX      := kind-$(CLUSTER)
NS       := notes
TAG      ?= $(shell git rev-parse --short HEAD)
IMAGE    ?= notes
KUBECTL  := kubectl --context $(CTX)
HELM     := helm --kube-context $(CTX)

.PHONY: test cluster monitoring build secret deploy smoke status rollback drift port-forward grafana-password down

test:
	pip install -q -r app/requirements-dev.txt && python -m pytest -q app
	helm lint chart

cluster:
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --name $(CLUSTER)

monitoring:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
	$(HELM) upgrade --install monitoring prometheus-community/kube-prometheus-stack --version 92.10.1 \
	  -n monitoring --create-namespace -f monitoring/prometheus-values.yaml --wait --timeout 10m
	$(KUBECTL) apply -f monitoring/jaeger.yaml

build:
	docker build --build-arg APP_VERSION=$(TAG) -t $(IMAGE):$(TAG) .
	kind load docker-image $(IMAGE):$(TAG) --name $(CLUSTER)

# Random Redis password generated in-cluster; never written to the repo.
secret:
	$(KUBECTL) create namespace $(NS) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) -n $(NS) get secret notes-redis >/dev/null 2>&1 || \
	  $(KUBECTL) -n $(NS) create secret generic notes-redis --from-literal=redis-password=$$(openssl rand -hex 24)

# --rollback-on-failure rolls back automatically if the new pods never become ready.
deploy: secret
	$(HELM) upgrade --install notes chart -n $(NS) --set image.repository=$(IMAGE) --set image.tag=$(TAG) \
	  --rollback-on-failure --wait --timeout 3m --description "deploy $(TAG)" $(EXTRA)

smoke:
	$(HELM) test notes -n $(NS) --logs

status:
	$(HELM) history notes -n $(NS)
	$(KUBECTL) -n $(NS) get pods,svc,pvc -L app.kubernetes.io/version

rollback:
	$(HELM) rollback notes $(REV) -n $(NS) --wait

# Shows any live change that differs from what Helm last applied.
drift:
	$(HELM) get manifest notes -n $(NS) | $(KUBECTL) diff -n $(NS) -f - && echo "no drift"

port-forward:
	$(KUBECTL) -n $(NS) port-forward svc/notes-api 8080:80 & \
	$(KUBECTL) -n monitoring port-forward svc/monitoring-grafana 3000:80 & \
	$(KUBECTL) -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 & \
	$(KUBECTL) -n monitoring port-forward svc/jaeger 16686:16686 & wait

grafana-password:
	@$(KUBECTL) -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

down:
	kind delete cluster --name $(CLUSTER)
