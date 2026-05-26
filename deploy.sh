#!/usr/bin/env bash
# Deploy BLUEPRINT to Google Cloud Run
# Usage: bash deploy.sh
# Prerequisites:
#   gcloud auth login && gcloud auth application-default login
#   gcloud config set project <YOUR_PROJECT_ID>
#   Store secrets in Google Secret Manager (see below)
#
# Secret Manager secrets required:
#   GEMINI_API_KEY        — AI Studio API key (paid tier)
#   ELASTIC_URL           — Elastic Cloud Serverless endpoint
#   ELASTIC_API_KEY       — Elastic API key
#   ELASTIC_MCP_URL       — Elastic MCP server URL (optional)
#   SLACK_WEBHOOK_URL     — Slack incoming webhook (optional, for high-risk alerts)
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project)}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
SERVICE="blueprint"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

echo "▶ Project: ${PROJECT}  Region: ${REGION}  Service: ${SERVICE}"
echo "▶ Building and pushing Docker image via Cloud Build…"
gcloud builds submit --tag "${IMAGE}" .

echo "▶ Deploying to Cloud Run…"

# Build optional SLACK secret flag
SLACK_SECRET=""
if gcloud secrets describe SLACK_WEBHOOK_URL --project="${PROJECT}" &>/dev/null; then
  SLACK_SECRET=",SLACK_WEBHOOK_URL=SLACK_WEBHOOK_URL:latest"
fi

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 80 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 10 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_REGION=${REGION},GEMINI_MODEL=gemini-3-flash-preview,VERTEX_MODEL=gemini-2.5-flash" \
  --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,ELASTIC_URL=ELASTIC_URL:latest,ELASTIC_API_KEY=ELASTIC_API_KEY:latest,ELASTIC_MCP_URL=ELASTIC_MCP_URL:latest${SLACK_SECRET}"

URL=$(gcloud run services describe "${SERVICE}" --platform managed --region "${REGION}" --format "value(status.url)")
echo ""
echo "✅ Deployed: ${URL}"
echo "   Health:   ${URL}/api/health"
echo "   Docs:     ${URL}/docs"
echo "   Compare:  ${URL}  (click Compare in the nav)"
echo ""
echo "Next: update APP_URL in .env to ${URL}"
