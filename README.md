# Zombie Sweep for Google Cloud

A daily, read-only sweep for Google Cloud resources that still bill but no longer do any work. Each sweep runs [zombiescan-gcp](https://github.com/xbill9/zombiescan-gcp)'s 20 checks beside 7 of Google's idle-resource recommenders, stores every finding in **AlloyDB Express**, and an ADK agent writes a daily briefing in which every number comes from SQL.

- Article: [dev.to](https://dev.to/gde/zombie-sweep-on-google-cloud-a-daily-waste-report-on-alloydb-express-with-a-database-bill-you-can-305g) · [Medium](https://medium.com/@xbill999/zombie-sweep-on-google-cloud-a-daily-waste-report-on-alloydb-express-with-a-database-bill-you-can-ecd3112006d8) · [Substack](https://xbill9.substack.com/p/zombie-sweep-on-google-cloud-a-daily)
- Demo video: https://youtu.be/4w_AB3Eev-c

## How it fits together

```
Cloud Scheduler  zombie-sweep-daily, 06:00 UTC
      |
Workflows  zombie-sweep  (deploy/workflow.yaml)
      |-- Cloud Run job  MODE=zombiescan   --+
      |-- Cloud Run job  MODE=recommender  --+--> AlloyDB Express  (runs, findings)
      |-- POST /briefing  (signed JWT, through IAP)          ^
      |        Cloud Run service: ADK agent + MCP Toolbox ---+  read-only SQL
      '-- Cloud Run job  MODE=finish  (stores the briefing, closes the run)
```

| Path | What it is |
| --- | --- |
| `sweep/` | Cloud Run job (`job.py`), one image, three modes; `db.py` connects through the AlloyDB Python connector |
| `agent/` | Cloud Run service: ADK `LlmAgent` on Gemini, report page, `/ask`, `/briefing`, `/health` |
| `agent/tools.yaml` | MCP Toolbox SQL tools, all read-only: `list_runs`, `totals`, `top_waste`, `count_findings`, `only_one_source`, `diff_vs_previous`, `finding_history`, `run_briefing`. Mounted from Secret Manager, not baked into the image |
| `sql/` | `schema.sql` (`runs`, `findings`), `views.sql` (`findings_keyed`, `findings_compared`), `grants.sql`, `apply.py` |
| `deploy/` | `deploy.sh` (idempotent, deploys everything), `workflow.yaml`, `ar-cleanup.json` |
| `docs/` | Article, `cost.py`, evidence files |
| `scripts/` | Demo recorder and YouTube uploader |

## Why AlloyDB Express

- **It pauses when idle.** After about 15 minutes with no traffic, compute stops billing; the next connection wakes it. Measured: a cold first query took 3.6 s, a warm one 0.2 s (`docs/evidence/express-idle-test-2026-11-23.txt`).
- **It has a ceiling.** 1 or 2 vCPUs, storage capped at 20 GB. The most a 1 vCPU instance can bill in a month, never idle and storage full, is $89.95; 2 vCPU is $173.90.
- **This workload is inside the free tier.** A month of daily sweeps uses 7.95–8.56 compute-unit hours of the 50 free ones. Everything together comes to $1.08 a month at list price, $0.09 with the free tier (`docs/evidence/cost-2026-11-20.txt`, prices from the Cloud Billing Catalog API).

## Deploy

Needs the Google Cloud CLI (with `beta` for AlloyDB Express), `git` and `python3`, and the Recommender, Cloud Run, Workflows, Cloud Scheduler, AlloyDB, Secret Manager, Vertex AI and IAP APIs enabled.

```bash
PROJECT=my-project REGION=us-west1 deploy/deploy.sh     # deploy or update everything
python3 sql/apply.py                                   # schema + views
python3 sql/apply.py grants.sql                        # after deploy.sh created the IAM DB users
deploy/deploy.sh run                                   # run a sweep now and wait
```

`VIEWER` (default: your gcloud account) gets IAP access to the report page. `sql/grants.sql` names the service-account database users for project `comglitn`; change it for your project.

Run one source locally:

```bash
MODE=zombiescan RUN_ID=test PROJECT_TO_SCAN=my-project python3 sweep/job.py
```

## Rules the code keeps

- **Numbers come from SQL, never from the model.** Every count and sum is a Toolbox tool or a view; the agent quotes them. Check a briefing against SQL with `python3 .claude/skills/check-briefing/check.py [run_id]`.
- **Read-only.** The sweep lists and prices; it deletes nothing. Toolbox runs with `readOnly: true`.
- **Re-runs are safe.** A job deletes and re-inserts its own source's rows for its run.

## Limits

AlloyDB Express is in preview: no backups, no HA, no SLA, no Terraform support, PostgreSQL 18 only, `max_connections` fixed at 200, us-west1 is the only US region. The instance-status metric reports "up" even while paused; `cpu/average_utilization` is the one that stops.
