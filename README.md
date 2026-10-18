# Notes API

A tiny Flask API that stores notes in Redis.

```bash
pip install -r app/requirements.txt
REDIS_HOST=localhost flask --app app.main run
curl -X POST localhost:5000/notes -H 'content-type: application/json' -d '{"text":"hello"}'
curl localhost:5000/notes
```
