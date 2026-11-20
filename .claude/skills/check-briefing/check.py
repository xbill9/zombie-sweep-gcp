#!/usr/bin/env python3
"""Print a run's briefing next to the figures SQL computes for that run.

    python3 .claude/skills/check-briefing/check.py [run_id]

Omitted run_id means the latest DONE run. Every number printed is computed by
PostgreSQL; compare the briefing's numbers against these, never re-add them.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sweep"))
import db  # noqa: E402
from google.cloud.alloydbconnector import Connector  # noqa: E402

QUERIES = {
    "by source": """
        SELECT source, count(*), sum(monthly_cost), min(monthly_cost), max(monthly_cost)
        FROM findings WHERE run_id = %s GROUP BY source ORDER BY source""",
    "all sources": """
        SELECT count(*), sum(monthly_cost) FROM findings WHERE run_id = %s""",
    "found by both / one source": """
        SELECT source, found_by_both, count(*), sum(monthly_cost)
        FROM findings_compared WHERE run_id = %s GROUP BY 1, 2 ORDER BY 1, 2""",
    "only one source, by check": """
        SELECT source, check_id, count(*), sum(monthly_cost)
        FROM findings_compared WHERE run_id = %s AND NOT found_by_both
        GROUP BY 1, 2 ORDER BY 1, 4 DESC, 2""",
    "top 10 by monthly cost": """
        SELECT source, check_id, resource, monthly_cost, approximate
        FROM findings WHERE run_id = %s ORDER BY monthly_cost DESC, resource LIMIT 10""",
}

with Connector(credentials=db.local_credentials()) as c:
    conn = db.connect(c)
    cur = conn.cursor()
    if len(sys.argv) > 1:
        cur.execute("SELECT run_id, started_at, status, briefing FROM runs WHERE run_id = %s", (sys.argv[1],))
    else:
        cur.execute("SELECT run_id, started_at, status, briefing FROM runs"
                    " WHERE status = 'DONE' ORDER BY started_at DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        sys.exit("no such run")
    run_id, started, status, briefing = row
    print(f"run_id {run_id}  started {started}  status {status}\n")
    print("=== briefing ===")
    print(briefing or "(none)")
    for title, sql in QUERIES.items():
        cur.execute(sql, (run_id,))
        cols = [d[0] for d in cur.description]
        print(f"\n=== {title} ===")
        print("\t".join(cols))
        for r in cur.fetchall():
            print("\t".join(str(v) for v in r))
    conn.close()
