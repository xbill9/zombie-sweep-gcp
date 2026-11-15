# Zombie Sweep for Google Cloud — plan

Sibling of `~/aws-pipeline` (github.com/xbill9/zombie-sweep). Same idea: a daily,
serverless, read-only sweep for resources nobody is using, priced, with Google's
own idle recommendations run alongside so the report shows what each one sees.
Two changes: findings live in **AlloyDB**, and an **agent** answers questions
about them.

Target: GDE AlloyDB AEO Content Sprint 11/2026, due **2026-11-30**. Registered
2026-11-15 as row 10 of the sheet's "Project List" tab (status WIP). To finish:
share with `#AlloyDBSprint`, then fill the row's social-post (N) and
result-URL (O) columns and mark Completed (Q).

## What already exists

- `~/zombiescan-gcp` (github.com/xbill9/zombiescan-gcp, last commit 9c74922,
  2026-09-22): the engine. `engine.load_packs()`, `engine.select_checks(())`,
  `engine.scan(clients, projects, checks, pricing, max_workers)` — the same
  shape the AWS sweep bundles. **20 checks** (counted 2026-11-15):
  disabled-kms-key, empty-vpc-network, gke-idle-cluster, idle-cloud-nat,
  idle-filestore, idle-forwarding-rule, orphaned-snapshot,
  stale-artifact-repository, stale-secret, stopped-instance,
  stopped-sql-instance, unattached-disk, unbounded-log-bucket,
  unmanaged-gcs-bucket, unused-dns-zone, unused-firewall-rule, unused-image,
  unused-static-ip, unused-subnet, unused-uptime-check.
  It also ships `mcp_server.py`.
- The fan-out unit is the **project**, not the region (zombiescan-gcp PLAN.md).

## Google's side of the comparison

Recommender, Cost category, idle/unused recommenders — 7, from
https://docs.cloud.google.com/recommender/docs/recommenders (read 2026-11-15):

| Recommender | ID |
|---|---|
| Idle VM | google.compute.instance.IdleResourceRecommender |
| Idle persistent disk | google.compute.disk.IdleResourceRecommender |
| Idle IP address | google.compute.address.IdleResourceRecommender |
| Idle custom image | google.compute.image.IdleResourceRecommender |
| Idle reservations | google.compute.IdleResourceRecommender |
| Cloud SQL idle instance | google.cloudsql.instance.IdleRecommender |
| Idle GKE cluster | google.container.DiagnosisRecommender |

Not stated on that page, still to check: API pricing, which locations each
recommender is queried at, quotas.

The AWS angle carries over directly: zombiescan-gcp checks Filestore, Cloud NAT,
forwarding rules, Artifact Registry, secrets, KMS, DNS, log buckets, GCS,
firewall rules, subnets, uptime checks and VPCs — none of which is on the
recommender list above.

## Architecture

Built and running (2026-11-15): Scheduler (06:00 UTC daily), Workflows, the
Cloud Run job, AlloyDB. Not built yet: the agent, the report page.
Deploy: `deploy/deploy.sh`; sweep now: `deploy/deploy.sh run`. Evidence:
`evidence/first-cloud-run-2026-11-15.txt`.

```
Cloud Scheduler (daily)
  -> Workflows
       for project comglitn (more projects later):
         Cloud Run job: zombiescan-gcp checks for that project
         Cloud Run job: Recommender idle recommendations for that project
       report job
  -> AlloyDB: findings(run_id, source, project, check, resource, monthly_cost, reason, ...)
  -> report page on GCS behind a load balancer or Firebase Hosting

  -> agent step: daily briefing written from SQL tool results

Agent: ADK on Cloud Run, tools from MCP Toolbox for Databases against AlloyDB
  "what costs the most?", "what did Recommender miss?", "what changed since yesterday?"
```

Read-only service account; deny secret payload and object reads, as the AWS
sweep's IAM deny does.

## Database: AlloyDB Express

Sources, read 2026-11-15: the launch post (2026-11-04, preview)
https://cloud.google.com/blog/products/databases/introducing-alloydb-express-for-rapid-testing-and-prototyping
and https://docs.cloud.google.com/alloydb/docs/express-overview (Pre-GA terms).

Why it fits a once-a-day sweep the way DynamoDB on-demand fit the AWS one:

- Compute pauses after 15 minutes idle and costs $0.00 while paused (blog);
  resumes on the next connection in about 20 s (docs; the blog says "seconds").
- $0.115 per CU-hour, billed per second; 1 or 2 CU (1 vCPU/4 GB, 2 vCPU/8 GB).
- Storage $0.30 per GB-month for what is used, up to 20 GB, then writes stop.
- Free tier: 50 compute units and 2 GB a month per billing account, "for the
  first 12 months" (docs; the blog gives no time limit).
- Full AlloyDB AI: pgvector, ScaNN, natural-language queries, Gemini from SQL.
- Auth Proxy and the Python connector work; IAM database auth supported.
- One-way, in-place upgrade to a full AlloyDB cluster keeps the connection string.

Estimate (arithmetic, `python3` in the session, not measured): each sweep
bills its run time plus the 15-minute idle tail. 30 sweeps a month at 1 CU is
8.5–10 CU-hours, $0.98–$1.15 before the free tier; at 2 CU, $1.96–$2.30.
Agent questions wake it too and add their own 15-minute tails.

Constraints that shape the build:

- Regions: asia-east1, asia-east2, asia-northeast1, asia-southeast1,
  australia-southeast1, europe-west2, **us-west1** — the only US one.
- No public IP: public DNS endpoint or Private Service Connect.
- No SLA, no HA, no backups or PITR, no CMEK, PostgreSQL 18 only,
  max_connections fixed at 200.
- Terraform not supported (docs; the blog says it is) — create with gcloud.
- Not stated anywhere read: the exact create command, Cloud Run specifics,
  google_ml_integration, which MCP server the blog means.

## The agent

Two jobs, one ADK agent (Python, Gemini), its tools served by MCP Toolbox for
Databases from AlloyDB:

1. **In the pipeline.** The last Workflows step calls the agent once per sweep.
   It writes the daily briefing at the top of the report page: what is new
   since yesterday, what costs the most, what Recommender missed.
2. **On demand.** The same agent on Cloud Run answers questions about the
   findings: "what can I delete today and save the most?", "why is this disk a
   zombie?", "which checks has Recommender never matched?"

Every number comes from SQL, never from the model (CLAUDE.md: arithmetic
belongs in code). The Toolbox tools are fixed, parameterised queries that
return exact counts and sums:

| Tool | Returns |
|---|---|
| `top_waste(run_id, n)` | the n most expensive findings |
| `totals(run_id)` | findings and monthly cost by source and by check |
| `recommender_gap(run_id)` | findings no Recommender recommendation matched |
| `diff_runs(run_id, prev_run_id)` | new, resolved and unchanged findings |
| `finding(resource)` | one finding's full row and its history |

The agent's role is choosing the tool and writing the sentence; the briefing
quotes the tool output. Read-only database role for the agent.

Stretch: embed each finding's reason with AlloyDB AI and use a ScaNN index
for "find findings like this one".

## Scope

Project `comglitn` only, for now: the sweep runs there and scans only there.

## Open questions

None blocking. Region is us-west1 (the only US region for Express).
