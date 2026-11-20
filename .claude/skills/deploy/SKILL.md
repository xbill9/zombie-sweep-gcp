---
name: deploy
description: Deploy or update the zombie sweep (deploy/deploy.sh), optionally apply SQL, run a sweep, and record evidence.
disable-model-invocation: true
---

Arguments: $ARGUMENTS (any of `sql`, `grants`, `run`; empty = deploy only).

1. If `sql` given: `python3 sql/apply.py` (schema + views). Do this before deploy when views changed — Toolbox tools read them.
2. `deploy/deploy.sh 2>&1 | tee /tmp/deploy.log` (idempotent; project `comglitn`, region `us-west1`). Re-run it after any edit to `agent/tools.yaml`.
3. If `grants` given: `python3 sql/apply.py grants.sql` (needs the IAM DB users deploy.sh creates).
4. If `run` given: `deploy/deploy.sh run`, then run the `/check-briefing` skill on the new run.
5. Save the commands and their output to `evidence/deploy-<YYYY-MM-DD>.txt` (append a suffix if it exists). Report what changed and the run's state/result verbatim.
6. Commit to `main` and push.
