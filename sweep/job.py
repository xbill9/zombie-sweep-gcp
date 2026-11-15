#!/usr/bin/env python3
"""One sweep job: run one source against one project and store the findings.

    MODE=zombiescan  RUN_ID=... PROJECT_TO_SCAN=comglitn  python3 job.py
    MODE=recommender RUN_ID=... PROJECT_TO_SCAN=comglitn  python3 job.py

Prints one JSON line with the counts, so Workflows (or a person) can read it.
Arithmetic stays in code: counts and sums are computed here and in SQL.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
from decimal import Decimal

import googleapiclient.discovery
import googleapiclient.errors
from google.cloud.alloydbconnector import Connector

import db

# Recommender, Cost category, idle/unused recommenders
# (https://docs.cloud.google.com/recommender/docs/recommenders, read 2026-11-15),
# with the location kinds each one is queried at.
RECOMMENDERS = {
    "google.compute.instance.IdleResourceRecommender": ("zone",),
    "google.compute.disk.IdleResourceRecommender": ("zone", "region"),
    "google.compute.address.IdleResourceRecommender": ("region", "global"),
    "google.compute.image.IdleResourceRecommender": ("global",),
    "google.compute.IdleResourceRecommender": ("zone",),
    "google.cloudsql.instance.IdleRecommender": ("region",),
    "google.container.DiagnosisRecommender": ("zone", "region"),
}
HOURS_PER_MONTH = Decimal("730")


def credentials():
    creds = db.local_credentials()
    if creds is None:
        import google.auth
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return creds


# ---------------------------------------------------------------- zombiescan
def run_zombiescan(project: str, creds) -> tuple[list[tuple], list[str]]:
    from zombiescan import engine
    from zombiescan.gcp import Clients
    from zombiescan.pricing import PriceTable

    engine.load_packs()
    checks = engine.select_checks(())
    result = engine.scan(Clients(creds), [project], checks, PriceTable.load(), max_workers=8)
    rows = [
        ("zombiescan", f.project, f.location or "global", f.check, f.resource_id,
         f.resource_type, Decimal(str(round(f.monthly_cost, 4))), f.approximate_cost, f.reason)
        for f in result.findings
    ]
    errors = [f"{e.check}: {getattr(e, 'message', e)}" for e in result.errors]
    return rows, errors


# --------------------------------------------------------------- recommender
def _monthly(rec: dict) -> Decimal:
    """Recommender's projected saving, normalised to a 730-hour month (positive)."""
    proj = rec.get("primaryImpact", {}).get("costProjection", {})
    cost, dur = proj.get("cost", {}), proj.get("duration", "0s")
    amount = Decimal(str(cost.get("units", "0"))) + Decimal(cost.get("nanos", 0)) / Decimal(10**9)
    seconds = Decimal(dur.rstrip("s") or "0")
    if not seconds:
        return Decimal(0)
    return abs(amount) / seconds * 3600 * HOURS_PER_MONTH


def _resource(rec: dict) -> str:
    for g in rec.get("content", {}).get("operationGroups", []):
        for op in g.get("operations", []):
            if op.get("resource"):
                return op["resource"]
    return rec.get("content", {}).get("overview", {}).get("resourceName", rec["name"])


def run_recommender(project: str, creds) -> tuple[list[tuple], list[str], int]:
    compute = googleapiclient.discovery.build("compute", "v1", credentials=creds, cache_discovery=False)
    regions = [r["name"] for r in compute.regions().list(project=project).execute().get("items", [])]
    zones = [z["name"] for z in compute.zones().list(project=project).execute().get("items", [])]
    where = {"zone": zones, "region": regions, "global": ["global"]}
    calls = [(rid, loc) for rid, kinds in RECOMMENDERS.items() for k in kinds for loc in where[k]]

    def one(call):
        rid, loc = call
        svc = googleapiclient.discovery.build("recommender", "v1", credentials=creds, cache_discovery=False)
        parent = f"projects/{project}/locations/{loc}/recommenders/{rid}"
        out, req = [], svc.projects().locations().recommenders().recommendations().list(
            parent=parent, filter="stateInfo.state=ACTIVE")
        while req is not None:
            resp = req.execute()
            out += resp.get("recommendations", [])
            req = svc.projects().locations().recommenders().recommendations().list_next(req, resp)
        return rid, loc, out

    rows, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for fut in concurrent.futures.as_completed([pool.submit(one, c) for c in calls]):
            try:
                rid, loc, recs = fut.result()
            except googleapiclient.errors.HttpError as exc:
                errors.append(f"{exc.resp.status} {exc.uri.split('/recommenders/')[-1][:80]}")
                continue
            for r in recs:
                rows.append(("recommender", project, loc, rid, _resource(r),
                             r.get("recommenderSubtype"), _monthly(r), False,
                             r.get("description")))
    return rows, errors, len(calls)


# ---------------------------------------------------------------------- main
def main() -> None:
    mode, run_id = os.environ["MODE"], os.environ["RUN_ID"]
    project = os.environ.get("PROJECT_TO_SCAN", "comglitn")
    started = time.monotonic()
    creds = credentials()
    calls = None
    if mode == "zombiescan":
        rows, errors = run_zombiescan(project, creds)
    elif mode == "recommender":
        rows, errors, calls = run_recommender(project, creds)
    else:
        sys.exit(f"unknown MODE {mode}")

    with Connector(credentials=db.local_credentials()) as connector:
        conn = db.connect(connector)
        cur = conn.cursor()
        cur.execute("INSERT INTO runs (run_id, projects) VALUES (%s, %s) ON CONFLICT (run_id) DO NOTHING",
                    (run_id, [project]))
        cur.execute("DELETE FROM findings WHERE run_id=%s AND source=%s AND project=%s",
                    (run_id, rows[0][0] if rows else mode, project))
        for r in rows:
            cur.execute(
                "INSERT INTO findings (run_id, source, project, location, check_id, resource,"
                " resource_type, monthly_cost, approximate, reason)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (run_id, *r))
        conn.commit()
        cur.execute("SELECT count(*), coalesce(sum(monthly_cost),0) FROM findings"
                    " WHERE run_id=%s AND source=%s", (run_id, mode))
        n, total = cur.fetchone()
        conn.close()

    print(json.dumps({"mode": mode, "run_id": run_id, "project": project, "findings": n,
                      "monthly_cost": float(round(total, 2)), "errors": len(errors),
                      "error_sample": errors[:5], "api_calls": calls,
                      "seconds": round(time.monotonic() - started, 1)}))


if __name__ == "__main__":
    main()
