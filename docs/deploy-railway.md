# Deploying to Railway

One Railway service runs gunicorn **and** the Celery worker in the same
container. That is deliberate for this POC: web and worker share the media
files (call audio) through one mounted volume, and Railway volumes attach to a
single service. Splitting them properly needs object storage (S3/R2) — a
recorded production delta.

`railway.json` (config-as-code) already sets the Dockerfile build, the
combined start command, `python manage.py migrate` as the pre-deploy step, and
the `/healthz` healthcheck. Static files are baked into the image at build
time (whitenoise manifest), and the dataset — including the call WAVs — ships
inside the image so seeding runs entirely in the cloud.

## 1. Create the project

In [railway.app](https://railway.app) (or `railway init` with the CLI):

1. **New Project → Deploy from GitHub repo** → `ranjan-iykyknow-ai/carrier-agent`,
   branch `main`. Railway detects the Dockerfile and `railway.json`.
2. Add **PostgreSQL** (New → Database → PostgreSQL).
3. Add **Redis** (New → Database → Redis).
4. On the app service: **Settings → Volumes → Add volume**, mount path `/data`.
5. **Settings → Networking → Generate Domain** (note the
   `…up.railway.app` domain).

## 2. Service variables

On the app service, set (Variables → Raw editor):

```env
SECRET_KEY=<generate: openssl rand -base64 48>
DEBUG=false
ALLOWED_HOSTS=${{RAILWAY_PUBLIC_DOMAIN}}
CSRF_TRUSTED_ORIGINS=https://${{RAILWAY_PUBLIC_DOMAIN}}
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
MEDIA_ROOT=/data/media
OPENAI_API_KEY=<from your .env>
DEEPGRAM_API_KEY=<from your .env>
LANGFUSE_BASE_URL=<from your .env>
LANGFUSE_PUBLIC_KEY=<from your .env>
LANGFUSE_SECRET_KEY=<from your .env>
LANGFUSE_CAPTURE_PAYLOADS=true
```

The `${{…}}` values are Railway reference variables — paste them literally.
Never commit any of these; the repo's `.env` stays local-only.

## 3. First deploy

Deploys trigger on push to `main`. The pre-deploy step migrates the database;
the healthcheck gates cutover on `/healthz`. Watch: **Deployments → View logs**.

## 4. Seed and create the login (one-time, in the deployed container)

```bash
railway ssh
```

Inside the container:

```bash
python manage.py seed            # imports the dataset and processes all 329
                                 # jobs with REAL providers (~$0.45, ~15 min).
                                 # Use --no-enqueue to import without spending.
python manage.py ensure_broker   # creates broker@goodlanelogistics.com
cat var/dev-password.txt         # copy the password NOW — the file is
                                 # ephemeral; the DB credential persists.
```

Seeding is idempotent (advisory-locked, resumable); a rerun never duplicates
records or re-spends on completed jobs. `python manage.py retry_failed_jobs`
previews/retries any provider failures.

## 5. Verify

- `https://<domain>/healthz` → ok
- Log in at `https://<domain>/` with the broker credentials.
- Dashboard populated; inbox rows open; a call inquiry plays audio
  (volume-backed media working).
- `/admin/` opens read-only with the same login.
- New traces and runtime scores appear in Langfuse (shared with local).

## Notes and deltas

- **Redeploys**: the volume and database persist; only the container FS
  (including `var/dev-password.txt`) is ephemeral.
- **Cost control**: reseeding never redispatches completed work. `make eval`
  never runs in deploy; run it locally.
- Production deltas recorded in `docs/production-deltas.md` territory: object
  storage + separate web/worker services, a supervisor for the in-container
  worker process, and bucket-vs-database reconciliation for orphaned uploads.
