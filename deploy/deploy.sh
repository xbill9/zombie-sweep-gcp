#!/usr/bin/env bash
# Deploy the sweep: service accounts and IAM, the Cloud Run job, the workflow,
# and the daily schedule. Idempotent; run again to update.
#
#   deploy/deploy.sh          deploy or update
#   deploy/deploy.sh run      start a sweep now and wait for it
set -euo pipefail
PROJECT=${PROJECT:-comglitn}
REGION=${REGION:-us-west1}
CLUSTER=zombie-sweep
JOB=zombie-sweep
WF=zombie-sweep
JOB_SA=zombie-sweep-job@$PROJECT.iam.gserviceaccount.com
WF_SA=zombie-sweep-workflow@$PROJECT.iam.gserviceaccount.com
HERE=$(cd "$(dirname "$0")/.." && pwd)
G="gcloud --project=$PROJECT --quiet"

if [[ "${1:-}" == run ]]; then
  $G workflows run "$WF" --location="$REGION" --format='value(state,result)'
  exit
fi

sa() { $G iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
       $G iam service-accounts create "$1" --display-name="$2"; }
bind() { $G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" \
           --condition=None >/dev/null; echo "bound $2 -> $1"; }

sa zombie-sweep-job "Zombie Sweep job (read-only scanner)"
sa zombie-sweep-workflow "Zombie Sweep workflow"

# The scanner role is zombiescan-gcp's own least-privilege role, unchanged.
ROLE_FILE=$(mktemp); curl -fsSL \
  https://raw.githubusercontent.com/xbill9/zombiescan-gcp/9c74922/policy/zombiescan-scanner-role.yaml > "$ROLE_FILE"
if $G iam roles describe zombiescanScanner >/dev/null 2>&1; then
  $G iam roles update zombiescanScanner --file="$ROLE_FILE" >/dev/null
else
  $G iam roles create zombiescanScanner --file="$ROLE_FILE" >/dev/null
fi
rm -f "$ROLE_FILE"

bind "$JOB_SA" "projects/$PROJECT/roles/zombiescanScanner"
bind "$JOB_SA" roles/recommender.viewer
bind "$JOB_SA" roles/alloydb.client
bind "$JOB_SA" roles/alloydb.databaseUser
bind "$JOB_SA" roles/serviceusage.serviceUsageConsumer
bind "$JOB_SA" roles/logging.logWriter
bind "$WF_SA" roles/run.jobsExecutorWithOverrides
bind "$WF_SA" roles/run.viewer
bind "$WF_SA" roles/workflows.invoker
bind "$WF_SA" roles/logging.logWriter

# Database login for the job's service account (IAM auth, no password).
DB_USER=${JOB_SA%.gserviceaccount.com}
$G alloydb users describe "$DB_USER" --cluster=$CLUSTER --region=$REGION >/dev/null 2>&1 ||
  $G alloydb users create "$DB_USER" --cluster=$CLUSTER --region=$REGION --type=IAM_BASED

$G run jobs deploy "$JOB" --region="$REGION" --source="$HERE/sweep" \
  --service-account="$JOB_SA" --task-timeout=900 --max-retries=0 \
  --cpu=1 --memory=1Gi --set-env-vars="MODE=zombiescan,PROJECT_TO_SCAN=$PROJECT,DB_IAM_USER=$DB_USER"

$G workflows deploy "$WF" --location="$REGION" --source="$HERE/deploy/workflow.yaml" \
  --service-account="$WF_SA"

URI="https://workflowexecutions.googleapis.com/v1/projects/$PROJECT/locations/$REGION/workflows/$WF/executions"
if $G scheduler jobs describe "$WF-daily" --location="$REGION" >/dev/null 2>&1; then verb=update; else verb=create; fi
$G scheduler jobs $verb http "$WF-daily" --location="$REGION" --schedule="0 6 * * *" --time-zone=UTC \
  --uri="$URI" --http-method=POST --message-body='{"argument": "{}"}' \
  --oauth-service-account-email="$WF_SA"
echo deployed
