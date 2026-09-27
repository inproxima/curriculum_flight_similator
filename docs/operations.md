# Operations runbook

## Health
- `GET /api/v1/health`: process liveness (the ALB health check).
- `GET /api/v1/ready`: database (with pgvector version), broker (Redis locally, SQS in AWS), storage, OCR, and AI
  route availability.
- **AI & usage** page (or `GET /api/v1/ai/usage`): model runs, failures, spend, and per-run audit.

## Backups
| Data | Mechanism | Retention |
|---|---|---|
| PostgreSQL (AWS) | RDS automated backups with point-in-time restore; final snapshot on deletion | 14 days (`db_backup_retention_days`) |
| Documents (AWS) | S3 versioning; noncurrent versions expire after 180 days | 180 days for overwritten/deleted objects |
| Local | `pg_dump` plus a tar of `data/objects` (see README) | Operator's choice |

Take a manual snapshot before risky migrations:
```bash
aws rds create-db-snapshot --db-instance-identifier cfs-<env> --db-snapshot-identifier cfs-<env>-pre-<change>
```

## Restore
**Database to a point in time.** RDS creates a new instance; the original is kept.
```bash
aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier cfs-<env> \
  --target-db-instance-identifier cfs-<env>-restore --restore-time 2026-10-01T12:00:00Z \
  --db-subnet-group-name cfs-<env> --vpc-security-group-ids <db-sg>
```
Verify the restored data, then point `CFS_DB_HOST` at it (update `aws_db_instance` or swap names) and redeploy.
Application state is entirely in PostgreSQL: jobs, reviews, scenarios, audit, and model runs.

**A document object.** List its versions and copy the wanted version back:
```bash
aws s3api list-object-versions --bucket <documents-bucket> --prefix orgs/<org>/originals/<sha[:2]>/<sha>
aws s3api copy-object --bucket <documents-bucket> --key <key> --copy-source "<documents-bucket>/<key>?versionId=<id>"
```
Keys are content-addressed by SHA-256. A restored object must hash to the value recorded in
`document_versions.sha256`; ingestion verifies this.

**Local:**
```bash
pg_restore -d cfs --clean backup.dump
tar xzf objects.tgz
```

## Rollback
1. **Automatic:** the ECS deployment circuit breaker restores the previous task definition when new tasks fail
   health checks.
2. **Manual:** run `scripts/deploy.sh <env> <previous-git-sha>` to redeploy a previous image. ECR keeps the last 30
   immutable tags.
3. **Migrations are forward-only in production.** Write additive migrations (new columns and tables first, remove
   later) so the previous image keeps working after a migration. If a migration must be undone, restore the
   pre-change snapshot rather than running `alembic downgrade` on production data.
4. **Web app:** re-run the deploy for the previous SHA (publishes the old `dist/`) and invalidate `/index.html`.

## Jobs
- **Progress:** stored in `jobs` and `job_events`. The UI streams it and can reconnect safely.
- **Stuck jobs:** jobs still `running` without a heartbeat for 10 minutes are re-queued when a worker starts, and
  stages are idempotent. Retry from the Documents page or with `POST /api/v1/jobs/{id}/retry`.
- **Dead-letter queue:** messages there triggered an alarm. Inspect them, fix the cause, and retry the job through
  the API (the job record is the source of truth). Then purge the DLQ.
- **Cancellation** is application-level (`cancel_requested`), checked between stages and tool steps. It does not
  depend on Celery remote control, which SQS lacks.

## Secrets and access
- **Provider keys:** rotate by updating the Secrets Manager secret, then forcing a new deployment
  (`aws ecs update-service --force-new-deployment` for `api` and `worker`).
- **Database password:** RDS manages it; the tasks read it at start.
- **Revoke a user:** remove them from the IdP groups; they lose access at their next request. Admins can also set
  roles under `/api/v1/admin/memberships`, but IdP groups override those on the next sign-in when present.
- **Audit:** `audit_events` records reviews, publishing, scenario changes, source assignments, document fetches, AI
  actions, and role changes.

## Model providers
- **Outages:** each route falls back to its second provider only when every included document permits it. Refusals
  are never re-routed.
- **Stop all AI:** set `CFS_AI_PROVIDER_ALLOWLIST=` (empty) and redeploy. The map, review, and deterministic
  analysis keep working.
- **Model changes:** after changing models or prompts, re-run `python -m cfs.evals.bhsc --retrieval`
  (see [evaluation.md](evaluation.md)).
