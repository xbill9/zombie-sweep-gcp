# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Daily sweep for idle ("zombie") GCP resources. Cloud Scheduler `zombie-sweep-daily` → Workflow `zombie-sweep` (`deploy/workflow.yaml`) → Cloud Run job `zombie-sweep` (`sweep/job.py`, `MODE=zombiescan|recommender|finish`) → AlloyDB Express. Agent `zombie-sweep-agent` (ADK + MCP Toolbox sidecar) writes the briefing into `runs.briefing` and serves the report page.

GCP project is `comglitn`, region `us-west1` (defaults in `deploy/deploy.sh`, `sweep/db.py`). This overrides the global Cloud Operator default project.

## Commands

- Deploy or update everything (idempotent): `deploy/deploy.sh`. Run a sweep now and wait: `deploy/deploy.sh run`.
- Apply SQL: `python3 sql/apply.py` (schema.sql + views.sql); `python3 sql/apply.py grants.sql` only after deploy.sh has created the IAM DB users.
- Run the job locally: `MODE=zombiescan RUN_ID=<id> PROJECT_TO_SCAN=comglitn python3 sweep/job.py`.
- Locally, `sweep/db.py` connects as `postgres` (password from secret `zombie-sweep-db-password`, gcloud token with quota project). In Cloud Run it uses IAM DB auth via `DB_IAM_USER`.
- View the report page: `gcloud --project=comglitn run services proxy zombie-sweep-agent --region=us-west1 --port=8765` → http://127.0.0.1:8765 (8080 is taken on this machine). deploy.sh grants the deploying account invoker and adds `VIEWER_AUD`, the audience of this workstation's gcloud identity tokens; without it Cloud Run returns 401 "access token could not be verified".
- Toolbox returns a bare object, not a list, for a one-row result; `call()` in `agent/app.py` normalizes it.
- No tests or CI. Verification is checking figures against SQL; record results in `evidence/<what>-<YYYY-MM-DD>.txt`.

## Rules

- **Numbers come from SQL, never the model.** Every count/sum lives in `agent/tools.yaml` or `sql/views.sql`; the agent prompt (`agent/app.py`) forbids the model doing arithmetic. Add a tool or view instead of asking the model to compute.
- Toolbox source is `readOnly: true`: every new `postgres-sql` tool in `agent/tools.yaml` needs `annotations: {readOnlyHint: true}` or Toolbox silently drops it.
- `agent/tools.yaml` is not in the image (`agent/.gcloudignore`); it is mounted from Secret Manager `zombie-sweep-tools`. After editing it, re-run `deploy/deploy.sh`.
- Health route is `/health`; Cloud Run reserves `/healthz`.
- `sql/grants.sql` hardcodes the `@comglitn.iam` service-account users; change it if the project changes.
- zombiescan-gcp is pinned to commit `9c74922` in both `sweep/requirements.txt` and the role URL in `deploy/deploy.sh`; bump both together.
- Re-running a job is safe: it deletes and re-inserts its own source's rows for that run.
- AlloyDB Express: no backups/HA, no Terraform; changing DB flags takes minutes.
