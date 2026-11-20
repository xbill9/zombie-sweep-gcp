---
name: check-briefing
description: Check every figure in a sweep run's briefing (runs.briefing) against SQL computed directly on AlloyDB. Use after a sweep or agent change, or when asked whether the briefing is right.
---

1. Run `python3 .claude/skills/check-briefing/check.py $ARGUMENTS` (optional run_id; default is the latest DONE run). It connects as `postgres` from the workstation and prints the briefing plus SQL-computed counts, sums, min/max, found-by-both splits and the top 10.
2. For each number in the briefing, find the matching SQL figure in the output and mark it MATCH or MISMATCH. Do not add, count or compare numbers yourself; if a briefing figure has no matching query, add a query to `check.py` instead of computing it.
3. Also check the briefing names the run it answered from.
4. Save the output plus the per-figure verdicts to `evidence/briefing-check-<YYYY-MM-DD>.txt` and report the verdicts.

If the connection fails with 403 SERVICE_DISABLED, the gcloud token lacks a quota project; the instance also refuses connections for ~40 s after it reports READY.
