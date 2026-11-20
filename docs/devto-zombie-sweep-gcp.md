---
title: "Zombie Sweep on Google Cloud: A Daily Waste Report on AlloyDB Express, With a Database Bill You Can Predict"
published: false
description: "Cloud Scheduler, Workflows and Cloud Run sweep a project every day with zombiescan's 20 checks beside Google's idle-resource recommenders, store the findings in AlloyDB Express, and an ADK agent writes a briefing from SQL. The database scales to zero, caps its own storage and has a monthly ceiling you can work out in advance."
tags: googlecloud, postgres, finops, serverless
cover_image: https://raw.githubusercontent.com/xbill9/zombie-sweep-gcp/main/docs/devto-cover.696c5b30.jpg
---

This article provides a step by step guide to building a daily sweep for unused Google Cloud resources with Cloud Scheduler, Workflows, Cloud Run and AlloyDB Express. Each sweep runs zombiescan's checks beside Google's Recommender, stores every finding in PostgreSQL, and an ADK agent with MCP Toolbox writes a daily briefing in which every number comes from SQL.

Repository: https://github.com/xbill9/zombie-sweep-gcp

A tool that hunts for surprise cloud bills should not create one. AlloyDB Express is the database that makes that easy to promise: within its free tier the database costs $0.00 a month for this workload, and even with every limit maxed out, its bill has a ceiling you can compute before you create it.

---

#### Why AlloyDB Express?

AlloyDB Express is a preview configuration of AlloyDB for PostgreSQL built for prototypes and small workloads. Three properties from its documentation decide the bill:

- **It scales to zero.** "When an instance is idle, it scales down to zero compute so you aren't billed for compute during periods of inactivity." The launch post puts the idle threshold at 15 minutes, and the documentation puts the wake-up at approximately 20 seconds.
- **Storage stops at 20 GB.** "When storage reaches 20 GB, further write operations aren't allowed." Storage is billed for what is used, not for what is provisioned.
- **Compute has two sizes.** 1 vCPU (4 GB RAM) or 2 vCPUs (8 GB RAM), billed per second as 1 or 2 compute units.

A database that cannot grow past 2 compute units or 20 GB has a worst case. Priced from the Cloud Billing Catalog API for us-west1, with the instance never idle and storage full:

| Instance | Compute, 730 hours | Storage, 20 GB | Most it can bill in a month |
| --- | --- | --- | --- |
| 1 vCPU (this sweep) | $83.95 | $6.00 | **$89.95** |
| 2 vCPU | $167.90 | $6.00 | **$173.90** |

That is the ceiling for a database left busy around the clock. A daily sweep keeps it awake for minutes a day, and the free tier covers that: each month includes 50 compute units and 2 GB of storage per billing account for the first 12 months.

---

#### What Is a Zombie Resource?

A zombie resource still bills and no longer does any work. A stopped VM whose disks still cost money, a snapshot of a disk that was deleted, a static IP attached to nothing, a secret nobody has updated in months.

They collect in projects used for one experiment, in zones you tried once, and behind instances that were stopped and forgotten instead of deleted.

---

#### Doesn't Google Cloud Already Do This?

Partly. Recommender has idle-resource recommenders for VMs, persistent disks, IP addresses, custom images, Cloud SQL instances and GKE clusters. This sweep queries 7 of them.

zombiescan-gcp is an open-source scanner with 20 checks, among them orphaned snapshots, stale Artifact Registry repositories, stale secrets, Cloud NAT gateways serving nothing, Filestore instances nothing can mount, unused firewall rules and subnets, Cloud DNS zones and uptime checks for deleted resources. Zombie Sweep runs both sources in every sweep and compares them resource by resource, so the report shows which source found what.

---

#### At This Point You Should Have

- A Google Cloud project with billing enabled
- The Google Cloud CLI, with the `beta` component for AlloyDB Express
- `git` and `python3` on the machine that deploys
- The Recommender, Cloud Run, Workflows, Cloud Scheduler, AlloyDB, Secret Manager, Vertex AI and IAP APIs enabled

---

#### How It Fits Together

```
Cloud Scheduler  zombie-sweep-daily, 06:00 UTC
      |
Workflows  zombie-sweep
      |-- Cloud Run job  MODE=zombiescan   --+
      |-- Cloud Run job  MODE=recommender  --+--> AlloyDB Express  (runs, findings)
      |-- POST /briefing  (signed JWT, through IAP)          ^
      |        Cloud Run service: ADK agent + MCP Toolbox ---+  read-only SQL
      '-- Cloud Run job  MODE=finish  (stores the briefing, closes the run)
```

One Cloud Run job image runs in three modes. One AlloyDB Express instance holds two tables and two views. One Cloud Run service holds the agent and its SQL tools.

---

#### Step 1 — Create the AlloyDB Express Cluster

The Express commands from the documentation, with this project's names:

```bash
gcloud beta alloydb clusters create zombie-sweep --region=us-west1 \
    --password="$DB_PASSWORD" --enable-express
gcloud beta alloydb instances create zombie-sweep-primary --cluster=zombie-sweep \
    --region=us-west1 --instance-type=PRIMARY --cpu-count=1 --enable-public-dns
```

```
Creating cluster...
.......done.

real	0m3.463s
Creating instance...
.......done.

real	0m3.147s
```

us-west1 is the only US region Express supports. The instance reports `PostgreSQL 18.4` and `max_connections 200`, both fixed by Express.

IAM database login needs one database flag:

```bash
gcloud alloydb instances update zombie-sweep-primary --cluster=zombie-sweep \
    --region=us-west1 --database-flags=alloydb.iam_authentication=on
```

---

#### Step 2 — Two Tables and Two Views

`runs` holds one row per sweep, keyed by the Workflows execution id, with the agent's briefing. `findings` holds one row per resource per source:

```sql
CREATE TABLE IF NOT EXISTS findings (
    run_id         text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    source         text NOT NULL CHECK (source IN ('zombiescan', 'recommender')),
    project        text NOT NULL,
    check_id       text NOT NULL,
    resource       text NOT NULL,
    monthly_cost   numeric(12, 4) NOT NULL DEFAULT 0,
    ...
    PRIMARY KEY (run_id, source, project, check_id, resource)
);
```

The two sources name the same resource differently. zombiescan stores `w1`; Recommender stores `//compute.googleapis.com/projects/p/regions/us-central1/addresses/w1`. The view `findings_keyed` reduces both to (project, kind, name), and `findings_compared` adds `found_by_both`:

```sql
CREATE OR REPLACE VIEW findings_compared AS
SELECT k.*,
       EXISTS (SELECT 1 FROM findings_keyed o
               WHERE o.run_id = k.run_id AND o.source <> k.source
                 AND o.project = k.project AND o.kind = k.kind AND o.name = k.name) AS found_by_both
FROM findings_keyed k;
```

`python3 sql/apply.py` applies the schema and views from a workstation through the AlloyDB Python connector.

---

#### Step 3 — One Cloud Run Job, Three Modes

`sweep/job.py` runs one source against one project and prints one JSON line:

- `MODE=zombiescan` runs zombiescan-gcp's 20 checks and prices each finding from the public price list
- `MODE=recommender` reads the 7 idle-resource recommenders at every zone, region or global location each one uses
- `MODE=finish` stores the briefing and marks the run `DONE`

Each source mode deletes and re-inserts its own rows for the run, so a retried job leaves no duplicates. In Cloud Run the job logs in to AlloyDB with IAM database authentication as its service account: no password in the job, and a `zombie_writer` role that can write findings and close runs.

---

#### Step 4 — Workflows Runs Both Sources, Then Asks for a Briefing

The two sources run as parallel branches, each one a Cloud Run job execution through the Workflows connector:

```yaml
- sources:
    parallel:
      for:
        value: mode
        in: ["zombiescan", "recommender"]
        steps:
          - run_source:
              call: googleapis.run.v2.projects.locations.jobs.run
```

Then the workflow posts the run id to the agent's `/briefing` route and runs the job a third time with `MODE=finish` to store what came back. A failed briefing does not fail the sweep; the run closes without one.

---

#### 🔎 Tip: Behind IAP, Workflows Signs Its Own JWT

The agent's Cloud Run service sits behind Identity-Aware Proxy, so a person opens the report page with a Google sign-in. IAP with the Google-managed OAuth client accepts a JWT signed by a service account with `aud` set to the URL, so the workflow signs one as itself and sends it as a bearer token:

```yaml
- claims:
    assign:
      - now: ${int(sys.now())}
      - claims:
          iss: ${sys.get_env("WF_SA")}
          sub: ${sys.get_env("WF_SA")}
          aud: ${sys.get_env("AGENT_URL") + "/*"}
          iat: ${now}
          exp: ${now + 900}
- sign:
    call: http.post
    args:
      url: ${"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/" + sys.get_env("WF_SA") + ":signJwt"}
      auth: {type: OAuth2}
      body:
        payload: ${json.encode_to_string(claims)}
    result: jwt
```

The workflow's service account holds Service Account Token Creator on itself only, and `roles/iap.httpsResourceAccessor` on the agent. Workflows expressions take no map literals, so the claims are built in an `assign` step first.

---

#### Step 5 — The Agent: ADK With MCP Toolbox as a Sidecar

The agent is a FastAPI app running an ADK `LlmAgent` on Gemini through Vertex AI. Its only access to the data is MCP Toolbox for Databases, running as a second container in the same Cloud Run service on `127.0.0.1:5000`. Toolbox connects to AlloyDB with IAM login as the agent's service account, which holds a read-only role.

Toolbox exposes 8 SQL tools: `list_runs`, `totals`, `top_waste`, `count_findings`, `only_one_source`, `diff_vs_previous`, `finding_history` and `run_briefing`. The tool definitions live in Secret Manager and are mounted into the Toolbox container as a file.

The agent serves four routes: `GET /` for the report page, `POST /ask` for questions, `POST /briefing` for the workflow, and `GET /health`, because Cloud Run reserves `/healthz`.

---

#### Step 6 — Every Number Comes From SQL

The agent is told never to add, count, average or compare numbers itself. Every count and sum is a SQL tool, and `count_findings` takes filters and returns the exact answer:

```yaml
name: count_findings
type: postgres-sql
source: findings-db
annotations: {readOnlyHint: true}
description: |
  Exact count, total, minimum and maximum monthly cost of the findings in one
  run that match every filter given. Any filter left out matches everything.
  Use this for any "how many" or "how much" question; never count rows yourself.
  min_monthly_cost is inclusive (>=).
statement: |
  ...
  SELECT (SELECT id FROM r) AS run_id, count(*) AS findings,
         round(coalesce(sum(monthly_cost), 0), 2)::float8 AS total_monthly_cost,
         round(min(monthly_cost), 2)::float8 AS min_monthly_cost,
         round(max(monthly_cost), 2)::float8 AS max_monthly_cost
  FROM findings_compared
  WHERE run_id = (SELECT id FROM r)
    AND ($5::float8 IS NULL OR monthly_cost >= $5::float8)
    ...
```

The briefing is then checked figure by figure against SQL run directly on the database, with no model involved: `.claude/skills/check-briefing/check.py` prints the briefing beside the counts, sums and per-check totals PostgreSQL computes for the same run.

---

#### 🔎 Tip: A Read-Only Toolbox Source Needs readOnlyHint on Every Tool

The Toolbox source is declared `readOnly: true`. With that set, Toolbox keeps a `postgres-sql` tool only when the tool declares `annotations: {readOnlyHint: true}`. All 8 tools here carry it. A new tool without it does not appear in the agent's tool list.

---

#### Step 7 — Deploy With One Script

```bash
python3 sql/apply.py              # schema and views
deploy/deploy.sh                  # service accounts, IAM, job, agent, IAP, workflow, schedule
python3 sql/apply.py grants.sql   # database roles for the IAM users deploy.sh created
deploy/deploy.sh run              # one sweep now
```

```
SUCCEEDED	{"run_id":"854715f3-1beb-4f35-8f4e-30f9f02f14d3"}
```

`deploy.sh` is idempotent and safe to run again. It also keeps only the newest version of the Toolbox secret enabled and sets an Artifact Registry cleanup policy that keeps the newest 3 images, so repeated deploys do not leave a growing pile of billable versions behind.

The schedule is `0 6 * * *` in UTC. Across 11 successful sweeps, each took 54 to 127 seconds end to end.

---

#### What the Sweep Found

Run `854715f3`, 2026-11-20, one project:

| Source | Findings | Monthly |
| --- | --- | --- |
| zombiescan | 27 | $128.86 |
| Recommender | 1 | $7.30 |

One resource was found by both: a static IP address, `w1`, at $7.30 a month. Everything else came from zombiescan alone:

| Check | Findings | Monthly |
| --- | --- | --- |
| stopped-instance | 5 | $65.28 |
| orphaned-snapshot | 5 | $53.19 |
| stale-artifact-repository | 4 | $2.55 |
| stale-secret | 8 | $0.54 |
| unmanaged-gcs-bucket | 2 | $0.00 |
| unused-firewall-rule | 2 | $0.00 |

The agent's briefing for that run, as stored in `runs.briefing`:

```
Totals by source: zombiescan has 27 findings costing 128.86 dollars a month; recommender has 1 findings costing 7.3 dollars a month.
Top waste: snapshot-w5-opt (53.19), windows (23.94), debian13b (13.06), w1 (10.88), and w2 (10.88).
Diff vs previous run fe2aac43-c1a1-49d4-8645-1afbd80ff204: 28 findings costing 136.16 are unchanged.
```

Every figure in it matches the SQL computed for the same run.

---

#### Compare and Contrast

From the AlloyDB Express documentation's own comparison:

| | AlloyDB Express | AlloyDB |
| --- | --- | --- |
| Compute when idle | 🥇 scales to zero | always provisioned |
| Pricing | 🥇 usage-based, monthly free tier | provisioned hourly, 30-day trial |
| Largest bill possible | 🥇 bounded: 2 vCPU, 20 GB | grows with the cluster |
| Compute | 1 or 2 vCPU, one zonal instance | 🥇 up to 288 vCPUs, read pools |
| Storage | up to 20 GB | 🥇 up to 128 TB |
| Backups, PITR, HA | ❌ none | 🥇 yes |
| SLA | ❌ none | 🥇 99.99% for regional HA |
| PostgreSQL versions | 18 | 🥇 14 to 18 |
| Terraform | ❌ | 🥇 yes |

---

#### What Does It Cost?

`docs/cost.py` prices the sweep from the Cloud Billing Catalog API and measures usage from the project: 37 Cloud Run job executions averaging 24.4 seconds, 7 briefings averaging 31.6 seconds, and the database at 17880855 bytes. For 30 daily sweeps:

| Item | List price | With the Express free tier |
| --- | --- | --- |
| AlloyDB Express compute, 7.95 to 8.56 CU-hours | $0.91 to $0.98 | 🥇 $0.00 |
| AlloyDB Express storage | $0.0050 | 🥇 $0.00 |
| Cloud Run job, 3 executions a sweep | $0.0439 | $0.0439 |
| Cloud Run agent, 1 briefing a sweep | $0.0491 | $0.0491 |
| **Total, upper end** | **$1.08** | **$0.09** |

AlloyDB compute hours are each sweep's duration plus the 15-minute idle period before the instance pauses. The 50 free compute units leave 41.44 a month after the sweeps: room for about 166 more wake-ups from people opening the report page or asking the agent a question.

The database line stays at $0.00 for as long as the free tier lasts, and after that it has the ceiling worked out above: $89.95 a month for this 1 vCPU instance if it never sleeps at all.

---

#### So, Which One?

For a scheduled job that writes a few megabytes a day and is read now and then, use AlloyDB Express. It sleeps between sweeps, its storage cannot grow past 20 GB, and its largest possible bill is a number you can compute before you create it.

Move to full AlloyDB when the data needs backups, high availability or an SLA. The upgrade is in place and keeps the connection string; there is no way back to Express afterwards.

---

#### Summary

The goal of this article was to run a daily sweep for unused Google Cloud resources without adding a database bill to watch. The key to the solution was AlloyDB Express, which scales to zero between sweeps, caps its own storage, and fits this workload inside its free tier, with every number in the report computed by SQL. The results on one project were:

- 🟢 Two sources per sweep: zombiescan's 20 checks and 7 Recommender idle recommenders
- 🟢 28 findings, $136.16 a month, one resource found by both sources
- 🟢 Every figure in the agent's briefing matches SQL run on the same data
- 🟢 $0.09 a month within the Express free tier, $1.08 at list price
- 🟢 A worst-case database bill of $89.95 a month, computed in advance
- ⚠️ AlloyDB Express is a preview, with no backups, no high availability and no SLA
- ❌ Gemini tokens for the briefing are not priced here

Scope: one Google Cloud project in us-west1, swept daily from 2026-11-15 to 2026-11-20, one 1 vCPU AlloyDB Express instance. Costs are estimates from Cloud Billing Catalog list prices and measured run times, not a bill; AlloyDB active hours assume the 15-minute idle period from the launch post. They exclude Gemini, Secret Manager, Workflows, Cloud Scheduler, Artifact Registry, Cloud Build and logging. The sweep is read-only and deletes nothing.

The strategy for using AlloyDB Express as the store for a daily waste sweep was validated with an incremental step by step approach.

---

#### References

- [Code on GitHub](https://github.com/xbill9/zombie-sweep-gcp)
- [zombiescan-gcp](https://github.com/xbill9/zombiescan-gcp)
- [AlloyDB Express overview](https://docs.cloud.google.com/alloydb/docs/express-overview)
- [Introducing AlloyDB Express](https://cloud.google.com/blog/products/databases/introducing-alloydb-express-for-rapid-testing-and-prototyping)
- [Recommender: list of recommenders](https://docs.cloud.google.com/recommender/docs/recommenders)
- [MCP Toolbox for Databases](https://github.com/googleapis/genai-toolbox)
- [IAP for Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)
- [Zombie Sweep on AWS](https://dev.to/aws-builders/zombie-sweep-a-daily-serverless-hunt-for-unused-aws-resources-1ph1)
