# Analytics Dashboard

Local-first Flask dashboard for social analytics and TikTok transcript exports.

## Local Development

```bash
./run.sh
```

The local app opens at:

```text
http://127.0.0.1:5455
```

Local data files such as `analytics.db` and `uploads/` are intentionally ignored by Git.

## Environment

Copy `.env.example` to `.env` for Docker/local production-style runs and set real values.

Important variables:

- `SECRET_KEY`: required for production sessions/CSRF.
- `AUTH_REQUIRED`: set `true` before any public deployment.
- `ADMIN_USERNAME`: admin username for HTTP Basic auth.
- `ADMIN_PASSWORD_HASH`: Werkzeug password hash for the admin password.
- `DATABASE_URL`: SQLite URL, for example `sqlite:////data/analytics.db`.
- `UPLOAD_DIR`: persistent upload directory.
- `ENABLE_OPENCLAW`: keep `false` online unless OpenClaw is explicitly deployed.
- `OPENCLAW_TOKEN`: required only when OpenClaw is enabled.
- `TRANSCRIPTION_ENABLED`: keep `false` online unless ffmpeg/Whisper/runtime are ready.

Create a password hash:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('change-me'))"
```

## Production Start

```bash
gunicorn app:app --bind 0.0.0.0:${PORT:-5455} --workers 2 --timeout 180
```

## Docker

```bash
docker compose up --build
```

The compose setup stores runtime data in a Docker volume mounted at `/data`.

## Deployment Notes

The safest minimal online version is private/admin-only:

- enable auth with `AUTH_REQUIRED=true`
- use persistent storage for `DATABASE_URL` and `UPLOAD_DIR`
- keep `TRANSCRIPTION_ENABLED=false` unless long-running jobs are isolated
- keep `ENABLE_OPENCLAW=false` unless OpenClaw is explicitly available
- do not commit `analytics.db`, `uploads/`, `.env`, `venv/`, or generated media
