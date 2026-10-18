# Evidence

Captured on a local kind cluster (Kubernetes via kind v0.33, Helm 4.3.1) against commit **`1326783`**,
deployed as image `notes:1326783`. Everything below is unedited command output, produced by running the
Makefile targets and the commands shown in the README in order.

| Claim | Where below |
|---|---|
| Tests, lint, secret/misconfig and CVE scans pass | *Unit tests*, *Security scans* |
| Deployed version is traceable to the commit | *Fresh install*, *helm test*, *User journey* (`/version`), pod `VERSION` label, log `version` field |
| A bad release rolls back automatically and users keep being served | *Bad release*, *Release history* |
| Redis data survives reinstall and outage (PVC) | *User journey*, *Recovery* |
| Redis is reachable only from the API, with a password; no SA token in pods | *NetworkPolicy + Redis auth* |
| Logs ↔ traces are linked | *Structured logs*, *Trace … in Jaeger* |
| Manual changes are detected and removed by the next deploy | *Drift* |
| Metrics are scraped; SLO alert fires on an outage and clears after recovery | *Prometheus*, *Outage drill*, *Recovery* |

CI evidence: the pipeline run for the PR's final commit (GitHub → Actions → `ci`) shows the same steps on a
GitHub-hosted kind cluster, ending with `/version` matching the commit SHA.

Limitations seen in this run: `NotesHighLatency` went *pending* during the outage because failing Redis
connections are slower than normal requests; it never fired. The bad-release revision appears as `failed`
in the history by design.

```text

## Commit under test — 11:39:26Z
1326783 Let the chart override manual edits on deploy
fe62df6 Point HOME at writable /tmp for gunicorn control socket
c711822 Add CI/CD, Helm chart, security controls and observability
ca4b25e Baseline: minimal notes API

## Unit tests + chart lint — 11:39:26Z
5 passed in 0.22s
1 chart(s) linted, 0 chart(s) failed

## Security scans — 11:39:26Z
trivy fs (secrets+misconfig): 0 HIGH/CRITICAL, exit 0
trivy image notes:1326783: 0 fixable CRITICAL, exit 0
image notes:1326783 runs as uid 10001

## Fresh install (release removed; Redis PVC kept) — 11:40:16Z
STATUS: deployed
REVISION: 1
DESCRIPTION: deploy 1326783

## helm test (user journey inside the cluster) — 11:40:23Z
POD LOGS: notes-smoke (smoke)
{"text":"smoke-1792323623"}
smoke test passed for version 1326783


## Bad release: image tag that does not exist — 11:40:25Z
UPGRADE FAILED: release notes failed, and has been rolled back due to rollback-on-failure being set
still serving image: notes:1326783

## Release history — 11:41:25Z
REVISION	UPDATED                 	STATUS    	CHART      	APP VERSION	DESCRIPTION                                                                      
1       	Sun Oct 18 17:10:17 2026	superseded	notes-0.1.0	dev        	deploy 1326783                                                                   
2       	Sun Oct 18 17:10:25 2026	failed    	notes-0.1.0	dev        	Upgrade "notes" failed: resource Deployment/notes/notes-api not ready. status: In
3       	Sun Oct 18 17:11:25 2026	deployed  	notes-0.1.0	dev        	Rollback to 1                                                                    

## Workloads — 11:41:25Z
NAME                             READY   STATUS        RESTARTS   AGE   VERSION
pod/notes-api-6fd59f6899-cjd5f   1/1     Running       0          68s   1326783
pod/notes-api-6fd59f6899-nk7kv   1/1     Running       0          68s   1326783
pod/notes-api-7cf9cc9dc9-fmjpr   0/1     Terminating   0          60s   broken-tag
pod/notes-redis-0                1/1     Running       0          68s   
pod/notes-smoke                  0/1     Completed     0          62s   

NAME                  TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)    AGE   VERSION
service/notes-api     ClusterIP   10.96.94.4   <none>        80/TCP     68s   1326783
service/notes-redis   ClusterIP   None         <none>        6379/TCP   68s   1326783

NAME                                       STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE   VERSION
persistentvolumeclaim/data-notes-redis-0   Bound    pvc-69f790db-59ae-49c4-83a1-d1c842320d51   1Gi        RWO            standard       <unset>                 53m   

NAME                                          POD-SELECTOR      AGE   VERSION
networkpolicy.networking.k8s.io/notes-redis   app=notes-redis   68s   

NAME                                   MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE   VERSION
poddisruptionbudget.policy/notes-api   1               N/A               1                     68s   

## User journey via Service — 11:41:27Z
{"text":"buy milk"}

39 notes stored (survived reinstall via PVC); last: buy milk
{"version":"1326783"}


## NetworkPolicy + Redis auth — 11:41:28Z
pod without app=notes-api label -> redis: blocked
pod with app=notes-api label, no password -> redis: NOAUTH Authentication required.
api pod service account token mounted: no

## Structured logs (version + trace_id) — 11:41:38Z
[pod/notes-api-6fd59f6899-nk7kv/api] {"time": "2026-10-18 11:40:23,356", "level": "INFO", "msg": "request", "version": "1326783", "trace_id": "f8f36eba52ea7c482845a982842db9b7", "method": "GET", "path": "/version", "status": 200, "ms": 0.0}

## Trace f8f36eba52ea7c482845a982842db9b7 from that log line, in Jaeger — 11:41:38Z
  span: GET /version 249 us

## Drift: manual kubectl scale to 3 — 11:41:44Z
-  replicas: 3
+  replicas: 2
DESCRIPTION: deploy 1326783
no drift

## Prometheus (healthy) — 11:41:45Z
  pods scraped: 2
   NotesApiDown inactive
   NotesHighErrorRate inactive
   NotesHighLatency inactive

## Outage drill: Redis scaled to 0 — 11:41:45Z
  GET /notes -> HTTP 503
NAME                         READY   STATUS        RESTARTS   AGE
notes-api-6fd59f6899-4rknp   0/1     Terminating   0          25s
notes-api-6fd59f6899-cjd5f   0/1     Running       0          112s
notes-api-6fd59f6899-nk7kv   0/1     Running       0          112s
  alert state 340s after outage began:
   NotesApiDown inactive
   NotesHighErrorRate firing
   NotesHighLatency pending

## Recovery: Redis scaled back to 1 — 11:47:25Z
  GET /notes -> HTTP 200
   39 notes still present after outage; last: buy milk
  alerts cleared 286s after recovery
  alerts after recovery:
   NotesApiDown inactive
   NotesHighErrorRate inactive
   NotesHighLatency inactive

## Done — 11:52:16Z
```
