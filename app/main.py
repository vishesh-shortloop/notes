import json
import logging
import os
import time

import redis
from flask import Flask, Response, g, jsonify, request
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

VERSION = os.getenv("APP_VERSION", "dev")

app = Flask(__name__)
db = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), password=os.getenv("REDIS_PASSWORD"), decode_responses=True)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        ctx = trace.get_current_span().get_span_context()
        return json.dumps({
            "time": self.formatTime(record), "level": record.levelname, "msg": record.getMessage(),
            "version": VERSION, "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else None,
            **getattr(record, "fields", {}),
        })


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
log = logging.getLogger("notes")
log.addHandler(handler)
log.setLevel(os.getenv("LOG_LEVEL", "INFO"))


@app.before_request
def start_timer():
    g.start = time.perf_counter()


@app.after_request
def record(response):
    if request.path in ("/metrics", "/healthz", "/readyz"):
        return response
    path = request.url_rule.rule if request.url_rule else "unmatched"
    elapsed = time.perf_counter() - g.start
    REQUESTS.labels(request.method, path, response.status_code).inc()
    LATENCY.labels(request.method, path).observe(elapsed)
    log.info("request", extra={"fields": {"method": request.method, "path": path,
                                         "status": response.status_code, "ms": round(elapsed * 1000, 1)}})
    return response


@app.errorhandler(redis.RedisError)
def redis_down(err):
    log.error("redis error: %s", err)
    return jsonify(error="storage unavailable"), 503


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/readyz")
def readyz():
    db.ping()
    return jsonify(status="ready")


@app.get("/version")
def version():
    return jsonify(version=VERSION)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.get("/notes")
def list_notes():
    return jsonify(db.lrange("notes", 0, -1))


@app.post("/notes")
def add_note():
    text = (request.get_json(silent=True) or {}).get("text")
    if not text:
        return jsonify(error="text is required"), 400
    db.rpush("notes", text)
    return jsonify(text=text), 201
