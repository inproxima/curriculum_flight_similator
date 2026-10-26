# Public prototype on Render

The prototype is hosted on [Render](https://render.com) so that anyone with the link can use the whole app with
no sign-in. This is separate from the AWS design in [deployment.md](deployment.md), which has sign-in and has
never been applied.

## What runs
| Piece | Render resource | Notes |
|---|---|---|
| App | One **web service** (Docker, Starter plan) built from [`infra/Dockerfile.render`](../infra/Dockerfile.render) | It serves the API and the built web app on one origin. Background jobs (extraction, mapping, outline import) run inside the same process (`CFS_JOB_RUNNER=thread`), so there is no Redis and no separate worker. Migrations run on every start. |
| Database | One **PostgreSQL 16** (Basic 256 MB) | pgvector is enabled by the first migration. Uploaded originals are stored here too (`CFS_STORAGE_BACKEND=db`, table `stored_objects`), so one database backup covers everything. |

Pushing to the deployed branch redeploys automatically.

## Settings (service → Environment)
| Variable | Value |
|---|---|
| `CFS_ENV` | `production` |
| `CFS_AUTH_MODE` | `public_demo`: no sign-in. Every visitor is the same administrator in one shared workspace. |
| `CFS_SECRET_KEY` | A random value of 64 hex characters (it signs job-stream tokens) |
| `CFS_DATABASE_URL` | The database's **internal** URL (Render's `postgresql://` form is accepted) |
| `CFS_CORS_ORIGINS` | `["https://<service>.onrender.com"]` |
| `CFS_OPENAI_API_KEY`, `CFS_ANTHROPIC_API_KEY` | Entered in the Render dashboard only. Leave them empty to turn AI off; the rest of the app still works. |
| `CFS_AI_MONTHLY_BUDGET_USD` | `50`: all AI calls stop for the month once this is spent |

The image already sets `CFS_JOB_RUNNER=thread`, `CFS_STORAGE_BACKEND=db` and `CFS_WEB_DIST_DIR`.

## Copying a local workspace to Render
The data never goes through Git. With the local database running:

1. In the Render dashboard, open the database. Under **Networking**, add this computer's IP to the inbound rules.
2. Copy its **External Database URL**.
3. Run `scripts/load_into_render.sh` from the repository root. It reads the URL from the clipboard, dumps the
   local database, restores it, migrates it to the current schema, copies the original files from
   `data/objects` into it ([`scripts/copy_objects_to_db.py`](../scripts/copy_objects_to_db.py)), and compares row
   counts. It refuses to touch a database that already has tables.
4. Copy the database's **Internal Database URL** into the web service's `CFS_DATABASE_URL`.

## Things to know
- **Anyone with the link can change or delete anything**, including running AI jobs on the configured keys. The
  monthly budget and the per-job cap bound the spend. Keep a dump from before sharing, so you can restore it.
- Everyone shares one user, so the job center shows every visitor's running jobs.
- Everything that is uploaded is viewable by anyone with the link, including the imported University of Calgary
  documents.
- The Starter plan has 512 MB of memory. If large scanned PDFs (OCR) run out of memory, move to Standard.
- Jobs survive restarts. A job that was running when the service restarted goes back to the queue after 10 minutes
  without a heartbeat, and the service picks up queued jobs every minute.
- To stop paying, suspend or delete the web service and the database in the Render dashboard.
