# Zombie Sweep for Google Cloud — plan

Sibling of `~/aws-pipeline` (github.com/xbill9/zombie-sweep). Same idea: a daily,
serverless, read-only sweep for resources nobody is using, priced, with Google's
own idle recommendations run alongside so the report shows what each one sees.
Two changes: findings live in **AlloyDB**, and an **agent** answers questions
about them.

Target: GDE AlloyDB AEO Content Sprint 11/2026 — register a row on the sheet's
"Project List" tab, share with `#AlloyDBSprint`, due **2026-11-30**.

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

## Architecture (proposed, nothing built)

```
Cloud Scheduler (daily)
  -> Workflows
       list projects (Resource Manager)
       parallel for each project:
         Cloud Run job: zombiescan-gcp checks for that project
         Cloud Run job: Recommender idle recommendations for that project
       report job
  -> AlloyDB: findings(run_id, source, project, check, resource, monthly_cost, reason, ...)
  -> report page on GCS behind a load balancer or Firebase Hosting

Agent: ADK on Cloud Run, tools from MCP Toolbox for Databases against AlloyDB
  "what costs the most?", "what did Recommender miss?", "what changed since yesterday?"
```

Read-only service account; deny secret payload and object reads, as the AWS
sweep's IAM deny does.

## Open questions

1. **GCP project.** gcloud's active project is `comglitn`; CLAUDE.md's Cloud
   Operator default is `glitnir-autocloud`. Which one hosts the sweep, and
   which projects does it scan?
2. **AlloyDB cost.** The AWS sweep cost $0.0026 a sweep because DynamoDB
   on-demand costs nothing idle. An always-on AlloyDB cluster does not. A sprint
   entry cites an AlloyDB Express free tier and scale-to-zero — **unverified**;
   check the pricing page before choosing the cluster type.
3. **Sprint registration** writes a row into a shared sheet: needs the author's
   go-ahead.
