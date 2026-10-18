import os

import redis
from flask import Flask, jsonify, request

app = Flask(__name__)
db = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), password=os.getenv("REDIS_PASSWORD"), decode_responses=True)


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
