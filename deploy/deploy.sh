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
AGENT=zombie-sweep-agent
AGENT_SA=zombie-sweep-agent@$PROJECT.iam.gserviceaccount.com
# Who may open the report page in a browser (IAP, Google sign-in).
VIEWER=${VIEWER:-user:$(gcloud config get-value account 2>/dev/null)}
TOOLBOX_IMAGE=us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.15.0
HERE=$(cd "$(dirname "$0")/.." && pwd)
G="gcloud --project=$PROJECT --quiet"

if [[ "${1:-}" == run ]]; then
  $G workflows run "$WF" --location="$REGION" --format='value(state,result)'
  exit
fi

sa() { $G iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
       $G iam service-accounts create "$1" --display-name="$2"; }
# A new service account can take a minute to be visible to IAM; retry.
bind() { for _ in 1 2 3 4 5 6 7 8; do
           $G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" \
             --condition=None >/dev/null 2>&1 && { echo "bound $2 -> $1"; return; }; sleep 15; done
         echo "could not bind $2 -> $1" >&2; return 1; }

sa zombie-sweep-job "Zombie Sweep job (read-only scanner)"
sa zombie-sweep-workflow "Zombie Sweep workflow"
sa zombie-sweep-agent "Zombie Sweep agent (read-only)"

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
bind "$AGENT_SA" roles/alloydb.client
bind "$AGENT_SA" roles/alloydb.databaseUser
bind "$AGENT_SA" roles/serviceusage.serviceUsageConsumer
bind "$AGENT_SA" roles/aiplatform.user
bind "$AGENT_SA" roles/logging.logWriter
bind "$WF_SA" roles/run.jobsExecutorWithOverrides
bind "$WF_SA" roles/run.viewer
bind "$WF_SA" roles/workflows.invoker
bind "$WF_SA" roles/logging.logWriter

# Database login for the job's service account (IAM auth, no password).
# The instance rejects IAM logins until this flag is on; Express ships with no flags set.
$G alloydb instances describe $CLUSTER-primary --cluster=$CLUSTER --region=$REGION \
    --format='value(databaseFlags)' | grep -q 'alloydb.iam_authentication.*on' ||
  $G alloydb instances update $CLUSTER-primary --cluster=$CLUSTER --region=$REGION \
    --database-flags=alloydb.iam_authentication=on
DB_USER=${JOB_SA%.gserviceaccount.com}
# (there is no `alloydb users describe`; list and match)
AGENT_DB_USER=${AGENT_SA%.gserviceaccount.com}
for u in "$DB_USER" "$AGENT_DB_USER"; do
  $G alloydb users list --cluster=$CLUSTER --region=$REGION --format='value(name)' | grep -q "/users/$u\$" ||
    $G alloydb users create "$u" --cluster=$CLUSTER --region=$REGION --type=IAM_BASED
done

$G run jobs deploy "$JOB" --region="$REGION" --source="$HERE/sweep" \
  --service-account="$JOB_SA" --task-timeout=900 --max-retries=0 \
  --cpu=1 --memory=1Gi --set-env-vars="MODE=zombiescan,PROJECT_TO_SCAN=$PROJECT,DB_IAM_USER=$DB_USER"

# Workflows refuses to deploy until its service agent exists in the project.
$G beta services identity create --service=workflows.googleapis.com >/dev/null

# The agent: this app plus MCP Toolbox as a sidecar on 127.0.0.1:5000. tools.yaml
# holds no secrets; Secret Manager is how Cloud Run mounts it as a file.
$G secrets describe zombie-sweep-tools >/dev/null 2>&1 ||
  $G secrets create zombie-sweep-tools --replication-policy=automatic >/dev/null
NEW_TOOLS=$($G secrets versions add zombie-sweep-tools --data-file="$HERE/agent/tools.yaml" --format='value(name.basename())')
# Only the newest version is used (mounted as :latest); disable the rest so they stop piling up.
$G secrets versions list zombie-sweep-tools --format='value(name.basename(),state)' |
  while read -r v state; do
    [[ "$v" != "$NEW_TOOLS" && "${state,,}" == enabled ]] && $G secrets versions disable "$v" --secret=zombie-sweep-tools >/dev/null
  done
$G secrets add-iam-policy-binding zombie-sweep-tools --member="serviceAccount:$AGENT_SA" \
  --role=roles/secretmanager.secretAccessor >/dev/null
AGENT_IMAGE=$REGION-docker.pkg.dev/$PROJECT/cloud-run-source-deploy/$AGENT:$(date -u +%Y%m%dT%H%M%S)
$G builds submit "$HERE/agent" --tag="$AGENT_IMAGE" --region="$REGION" >/dev/null
$G run deploy "$AGENT" --region="$REGION" --service-account="$AGENT_SA" --no-allow-unauthenticated \
  --timeout=300 --max-instances=2 --iap \
  --container=app --image="$AGENT_IMAGE" --port=8080 --cpu=1 --memory=1Gi \
    --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,PROJECT_TO_SCAN=$PROJECT" \
  --container=toolbox --image="$TOOLBOX_IMAGE" --cpu=1 --memory=512Mi \
    --args="--config=/app/tools.yaml,--address=127.0.0.1,--port=5000" \
    --set-secrets="/app/tools.yaml=zombie-sweep-tools:latest" \
    --set-env-vars="PROJECT=$PROJECT,REGION=$REGION,CLUSTER=$CLUSTER,INSTANCE=$CLUSTER-primary,DB_IAM_USER=$AGENT_DB_USER"
AGENT_URL=$($G run services describe "$AGENT" --region="$REGION" --format='value(status.url)')
$G run services add-iam-policy-binding "$AGENT" --region="$REGION" \
  --member="serviceAccount:$WF_SA" --role=roles/run.invoker >/dev/null
# IAP fronts the agent: IAP's service agent invokes it; the viewer (browser) and
# the workflow (a JWT it signs as itself) pass IAP.
$G beta services identity create --service=iap.googleapis.com >/dev/null
PROJECT_NUMBER=$($G projects describe "$PROJECT" --format='value(projectNumber)')
$G run services add-iam-policy-binding "$AGENT" --region="$REGION" \
  --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
for m in "$VIEWER" "serviceAccount:$WF_SA"; do
  $G iap web add-iam-policy-binding --resource-type=cloud-run --service="$AGENT" --region="$REGION" \
    --member="$m" --role=roles/iap.httpsResourceAccessor --condition=None >/dev/null
done
$G iam service-accounts add-iam-policy-binding "$WF_SA" \
  --member="serviceAccount:$WF_SA" --role=roles/iam.serviceAccountTokenCreator >/dev/null

# Every deploy pushes new images here; keep the newest 3 per image, delete older ones.
$G artifacts repositories set-cleanup-policies cloud-run-source-deploy --location="$REGION" \
  --policy="$HERE/deploy/ar-cleanup.json" --no-dry-run >/dev/null

$G workflows deploy "$WF" --location="$REGION" --source="$HERE/deploy/workflow.yaml" \
  --service-account="$WF_SA" --set-env-vars="AGENT_URL=$AGENT_URL,WF_SA=$WF_SA"

URI="https://workflowexecutions.googleapis.com/v1/projects/$PROJECT/locations/$REGION/workflows/$WF/executions"
if $G scheduler jobs describe "$WF-daily" --location="$REGION" >/dev/null 2>&1; then verb=update; else verb=create; fi
$G scheduler jobs $verb http "$WF-daily" --location="$REGION" --schedule="0 6 * * *" --time-zone=UTC \
  --uri="$URI" --http-method=POST --update-headers=Content-Type=application/json --message-body='{"argument": "{}"}' \
  --oauth-service-account-email="$WF_SA"
echo deployed
