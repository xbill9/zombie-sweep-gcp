#!/usr/bin/env python3
"""What Zombie Sweep costs a month, and the most AlloyDB Express could bill.

    python3 docs/cost.py > docs/evidence/cost-<date>.txt

Prices: Cloud Billing Catalog API, us-west1 list prices, fetched at run time.
Usage: measured from this project (Cloud Run job executions, agent request logs,
pg_database_size). Every sum is computed here.
"""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import urllib.request

PROJECT, REGION = "comglitn", "us-west1"
DAYS = 30
HOURS_PER_MONTH = 730
IDLE_TAIL_S = 15 * 60  # Express pauses after 15 minutes idle (launch blog)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "sweep"))


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


TOKEN = sh("gcloud", "auth", "print-access-token").strip()


def api(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}",
                                               "x-goog-user-project": PROJECT})
    return json.load(urllib.request.urlopen(req))


def skus(service_id: str) -> list[dict]:
    out, tok = [], ""
    while True:
        r = api(f"https://cloudbilling.googleapis.com/v1/services/{service_id}/skus?pageSize=5000"
                + (f"&pageToken={tok}" if tok else ""))
        out += r.get("skus", [])
        tok = r.get("nextPageToken")
        if not tok:
            return out


def tiers(sku: dict) -> list[tuple[float, float]]:
    pe = sku["pricingInfo"][0]["pricingExpression"]
    return [(t["startUsageAmount"], int(t["unitPrice"].get("units", 0)) + t["unitPrice"].get("nanos", 0) / 1e9)
            for t in pe["tieredRates"]]


def price(service_id: str, description: str) -> tuple[float, float]:
    """(free units, unit price) for the SKU whose description matches exactly."""
    for s in skus(service_id):
        if s["description"] == description:
            t = tiers(s)
            free = t[1][0] if len(t) > 1 and t[0][1] == 0 else 0.0
            print(f"  price  {description}: {t}  ({s['pricingInfo'][0]['pricingExpression']['usageUnitDescription']})")
            return free, t[-1][1]
    raise SystemExit(f"SKU not found: {description}")


print(f"run at {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}  project {PROJECT}  region {REGION}\n")
print("Prices (Cloud Billing Catalog API):")
ADB, RUN = "C49F-B7F2-7416", "152E-C115-5142"
adb_free_h, adb_hr = price(ADB, "AlloyDB Express Instance Compute in Oregon")
adb_free_gb, adb_gb = price(ADB, "AlloyDB Express Cluster Database Storage in Oregon")
_, job_cpu = price(RUN, "Jobs CPU in us-west1")
_, job_mem = price(RUN, "Jobs Memory in us-west1")
_, svc_cpu = price(RUN, "Services CPU (Request-based billing)")
_, svc_mem = price(RUN, "Services Memory (Request-based billing)")

print("\nMeasured usage:")
inst = json.loads(sh("gcloud", f"--project={PROJECT}", "alloydb", "instances", "describe", "zombie-sweep-primary",
                     "--cluster=zombie-sweep", f"--region={REGION}", "--format=json"))
cu = int(inst["machineConfig"]["cpuCount"])
print(f"  AlloyDB Express instance: {cu} vCPU = {cu} compute unit(s)")

execs = json.loads(sh("gcloud", f"--project={PROJECT}", "run", "jobs", "executions", "list", "--job=zombie-sweep",
                      f"--region={REGION}", "--limit=500", "--format=json"))


def ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


job_s = [(ts(e["status"]["completionTime"]) - ts(e["status"]["startTime"])).total_seconds()
         for e in execs if e["status"].get("completionTime") and e["status"].get("startTime")]
job_mean = sum(job_s) / len(job_s)
print(f"  sweep job executions: {len(job_s)}, {sum(job_s):.1f} s in total, mean {job_mean:.1f} s (1 vCPU, 1 GiB each)")

wf = json.loads(sh("gcloud", f"--project={PROJECT}", "workflows", "executions", "list", "zombie-sweep",
                   f"--location={REGION}", "--limit=100", "--format=json"))
wf_s = [(ts(e["endTime"]) - ts(e["startTime"])).total_seconds() for e in wf if e.get("state") == "SUCCEEDED"]
print(f"  successful sweeps: {len(wf_s)}, {min(wf_s):.0f}-{max(wf_s):.0f} s end to end")

logs = json.loads(sh("gcloud", f"--project={PROJECT}", "logging", "read",
                     'resource.type="cloud_run_revision" AND resource.labels.service_name="zombie-sweep-agent" '
                     'AND httpRequest.requestUrl:"/briefing" AND httpRequest.status=200',
                     "--freshness=30d", "--limit=500", "--format=json"))
brief_s = [float(e["httpRequest"]["latency"].rstrip("s")) for e in logs if e.get("httpRequest", {}).get("latency")]
brief_mean = sum(brief_s) / len(brief_s)
print(f"  agent briefings: {len(brief_s)}, mean {brief_mean:.1f} s (2 vCPU, 1.5 GiB across app + Toolbox)")

import db  # noqa: E402
from google.cloud.alloydbconnector import Connector  # noqa: E402

with Connector(credentials=db.local_credentials()) as c:
    cur = db.connect(c).cursor()
    cur.execute("select pg_database_size('postgres')")
    db_bytes = cur.fetchone()[0]
db_gib = db_bytes / 2**30
print(f"  database size: {db_bytes} bytes = {db_gib:.4f} GiB")

print(f"\nOne month = {DAYS} daily sweeps:")
adb_h = [(s + IDLE_TAIL_S) / 3600 * DAYS * cu for s in (min(wf_s), max(wf_s))]
print(f"  AlloyDB compute: {adb_h[0]:.2f}-{adb_h[1]:.2f} CU-hours (sweep time + 15 min idle tail)")
print(f"    list price ${adb_h[0]*adb_hr:.2f}-${adb_h[1]*adb_hr:.2f};"
      f" with {adb_free_h:.0f} free CU-hours ${max(0, adb_h[1]-adb_free_h)*adb_hr:.2f}")
print(f"  AlloyDB storage: list ${db_gib*adb_gb:.4f}; within {adb_free_gb:.0f} GiB free tier: "
      f"${max(0, db_gib-adb_free_gb)*adb_gb:.2f}")
job_m = job_mean * 3 * DAYS * (job_cpu + job_mem)
brief_m = brief_mean * DAYS * (2 * svc_cpu + 1.5 * svc_mem)
print(f"  Cloud Run job, 3 executions a sweep: ${job_m:.4f}")
print(f"  Cloud Run agent, one briefing a sweep: ${brief_m:.4f}")
tail_left = (adb_free_h - adb_h[1]) / (IDLE_TAIL_S / 3600 * cu)
print(f"  free CU-hours left after the sweeps: {adb_free_h - adb_h[1]:.2f}, about {tail_left:.0f} more 15-minute wake-ups")

adb_list = adb_h[1] * adb_hr + db_gib * adb_gb
adb_free = max(0, adb_h[1] - adb_free_h) * adb_hr + max(0, db_gib - adb_free_gb) * adb_gb
print(f"  total, upper end: ${job_m + brief_m + adb_list:.2f} at list price;"
      f" ${job_m + brief_m + adb_free:.2f} within the AlloyDB free tier")

print("\nThe most AlloyDB Express can bill in a month (instance never idle, storage full):")
for n in (1, 2):
    comp = n * HOURS_PER_MONTH * adb_hr
    stor = 20 * adb_gb
    print(f"  {n} CU: compute {n} x {HOURS_PER_MONTH} h x ${adb_hr} = ${comp:.2f}, storage 20 GB x ${adb_gb} = ${stor:.2f},"
          f" ceiling ${comp+stor:.2f}")
