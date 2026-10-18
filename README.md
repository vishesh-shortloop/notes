# Notes API — CI/CD, Kubernetes and observability

A tiny Flask API that stores notes in Redis. The app is intentionally small; the work is in how it is
built, checked, released, run and observed on Kubernetes (a local [kind](https://kind.sigs.k8s.io) cluster).

- Baseline: tag `baseline` (the bare app, no ops tooling).
- Improvements: one PR from branch `devops` into `main`.
- Evidence from a real run: [docs/EVIDENCE.md](docs/EVIDENCE.md).

## Architecture

```
client ──> Service notes-api (ClusterIP :80)
              └─> Deployment notes-api (2 pods, gunicorn :8000)
                    ├─> Service notes-redis ──> StatefulSet notes-redis (1 pod, 1Gi PVC, AOF on)
                    ├─> /metrics ──> Prometheus ──> Grafana dashboard + alert rules ──> Alertmanager
                    ├─> OTLP traces ──> Jaeger
                    └─> JSON logs (with trace_id) ──> kubectl logs
```

| Endpoint | Purpose |
|---|---|
| `POST /notes` `{"text": "..."}` / `GET /notes` | The user journey |
| `GET /healthz` | Liveness: process is up |
| `GET /readyz` | Readiness: Redis answers `PING` (pod leaves the Service when Redis is down) |
| `GET /version` | Running version (= git commit SHA baked into the image) |
| `GET /metrics` | Prometheus metrics |

Failure boundary: if Redis is down, note requests return `503` (not a crash), readiness fails, and the
error-rate alert fires. Liveness only checks the process, so pods are not restarted for a dependency outage.

## Repository layout

| Path | What |
|---|---|
| `app/` | Flask app + pytest tests |
| `Dockerfile` | Non-root image, version label, OpenTelemetry auto-instrumentation |
| `chart/` | Helm chart: Deployment, Service, Redis StatefulSet, ConfigMap, NetworkPolicy, PDB, ServiceMonitor, alert rules, Grafana dashboard, `helm test` smoke test |
| `chart/values.yaml` / `values-ci.yaml` | Local defaults / CI overrides (no monitoring stack, 1 replica) |
| `monitoring/` | kube-prometheus-stack values, Jaeger |
| `.github/workflows/ci.yml` | Pipeline |
| `Makefile` | Every local operation (all commands pin the `kind-notes` context) |

## Run it locally

Needs Docker, kind, kubectl, Helm 4, Python 3.12+.

```bash
make test          # unit tests + helm lint
make cluster       # kind cluster "notes"
make monitoring    # Prometheus, Grafana, Alertmanager, Jaeger (~3 min)
make build         # image notes:<git sha>, loaded into kind
make deploy        # helm upgrade --install, waits for readiness, auto-rolls back on failure
make smoke         # helm test: POST a note, read it back, check /version == deployed tag
make status        # helm history + pods/services/PVC with their version label
```

Try it:

```bash
make port-forward  # app :8080, Grafana :3000, Prometheus :9090, Jaeger :16686
curl -X POST localhost:8080/notes -H 'content-type: application/json' -d '{"text":"hello"}'
curl localhost:8080/notes
curl localhost:8080/version
make grafana-password   # user "admin"; dashboard "Notes API"
```

Clean up with `make down`.

## CI/CD pipeline

`.github/workflows/ci.yml` runs on every PR and on pushes to `main`:

1. **test** — pytest, `helm lint`, Trivy repo scan for committed secrets and Kubernetes/Dockerfile misconfigurations (fails on HIGH/CRITICAL).
2. **build** — builds the image tagged with the full commit SHA (`APP_VERSION` = SHA), Trivy image scan (fails on fixable CRITICAL CVEs).
3. **e2e** — creates a throwaway kind cluster, deploys *that exact image* with the Helm chart, runs `helm test`, and checks `/version` returns the commit SHA.
4. **publish** (main only) — pushes the image to GHCR. Runs in the `production` GitHub environment, so it waits for a required reviewer's approval.

Setup on GitHub: create environment `production` under *Settings → Environments* and add yourself as a required reviewer. No other secrets are needed (`GITHUB_TOKEN` pushes to GHCR).

Why this split: checks and the deploy test are automatic because they are cheap and safe; publishing a
release artifact is the human gate. GitHub-hosted runners cannot reach a laptop's kind cluster, so the
pipeline proves deployability on its own kind cluster and the local cluster is deployed with `make deploy`.

## Release control

- **Strategy: rolling update** with `maxSurge: 1, maxUnavailable: 0` — a new pod must pass `/readyz` before an old one is removed, so there is no downtime. A PodDisruptionBudget keeps one pod during node drains. Blue-green/canary would need double capacity or a mesh/ingress controller, which this app does not justify.
- **Automatic rollback**: `helm upgrade --rollback-on-failure --wait` reverts to the previous release if the new pods are not ready within the timeout.
- **Manual rollback**: `make rollback` (previous revision) or `make rollback REV=3`.
- **Traceability**: image tag = commit SHA; the same SHA is in `/version`, every log line (`version`), the pod label `app.kubernetes.io/version`, the image label `org.opencontainers.image.revision`, and the Helm revision description (`helm history`).
- **Drift**: `make drift` diffs what Helm last applied against the live cluster (`kubectl diff`), so manual `kubectl edit`/`scale` changes show up. `make deploy` uses `--force-conflicts`, so the chart in git always wins and the next deploy removes drift. Config changes roll pods automatically via a ConfigMap checksum annotation.

## Configuration

One chart, values per environment. `values.yaml` holds the local defaults; `values-ci.yaml` only overrides what
differs in CI. A new environment would be one more small values file rather than a copied set of manifests.

## Kubernetes choices

| Concern | Choice | Why |
|---|---|---|
| API workload | Deployment, 2 replicas | Stateless; two replicas survive one pod failure |
| Redis | StatefulSet + headless Service + 1Gi PVC, append-only file on | Data survives pod restarts; stable identity |
| Health | readiness `/readyz` (checks Redis), liveness `/healthz` (process only) | Remove unhealthy pods from traffic without restart loops on dependency outages |
| Resources | requests + limits on every container | Predictable scheduling, no noisy neighbour |
| Networking | ClusterIP only, accessed with port-forward | Nothing exposed outside the cluster |
| Scaling | Fixed replicas | kind has no metrics-server; an HPA on CPU would be the next step |

## Security

- **Secrets**: the Redis password is generated in-cluster by `make secret` (`openssl rand`) and in CI per run; it never exists in the repo, values files or logs. The chart only references the Secret by name.
- **Permissions**: containers run as non-root with a read-only root filesystem, all Linux capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`. Service account tokens are not mounted (the app never talks to the Kubernetes API). The pipeline's `GITHUB_TOKEN` is read-only except `packages: write` in the publish job.
- **Exposure**: ClusterIP services only. A NetworkPolicy allows only `notes-api` pods to reach Redis, and Redis requires a password.
- **Checks**: Trivy secret + misconfiguration scan of the repo, Trivy CVE scan of the image, both blocking.

## Observability and reliability

- **Metrics**: `http_requests_total{method,path,status}` and `http_request_duration_seconds` histogram, scraped every 15s by a ServiceMonitor. Grafana dashboard *Notes API*: requests by status, success rate, pods up, p95 latency.
- **Logs**: one JSON line per request with `method`, `path`, `status`, `ms`, `version` and `trace_id`; `kubectl -n notes logs -l app=notes-api`.
- **Traces**: OpenTelemetry auto-instrumentation (Flask + Redis) exports to Jaeger; each request shows the HTTP span and the Redis call. The `trace_id` in a log line opens the matching trace in Jaeger.

**What healthy means (SLOs)** and what happens when it is not:

| SLO / alert | Condition | Response |
|---|---|---|
| Availability ≥ 99% — `NotesHighErrorRate` | 5xx > 1% for 5 min | Check `/readyz` and the Redis pod; if it began after a deploy, `make rollback` |
| p95 latency < 300 ms — `NotesHighLatency` | for 10 min | Find slow spans in Jaeger; check CPU throttling against limits |
| `NotesApiDown` | no pod scraped for 2 min | `kubectl get pods`, `describe`, `helm history` |

**Verifying recovery** (a Redis outage drill):

```bash
kubectl --context kind-notes -n notes scale statefulset notes-redis --replicas=0
# requests now return 503, pods go NotReady, NotesHighErrorRate fires after ~5 min
kubectl --context kind-notes -n notes scale statefulset notes-redis --replicas=1
# pods become Ready again, notes written before the outage are still there (PVC), the alert resolves
```

## Limitations

- Single local environment on kind; there is no cloud deployment and no TLS/Ingress.
- GitHub cannot reach the local cluster, so CD deploys to an ephemeral kind cluster in CI, and the local cluster is updated with `make deploy`.
- Redis is a single replica (no HA); the PVC protects data from pod restarts, not node loss.
- Logs stay in the cluster (`kubectl logs`); no Loki/ELK. Jaeger stores traces in memory.
- Alertmanager has no receiver configured (alerts are visible in Prometheus/Alertmanager UIs only).
