#!/usr/bin/env bash
# End-to-end check against the deployed stack.
#
# Sends the saddle example and prints the response. Run it twice: the first call
# should come back "cached": false, the second "cached": true. That second run is the
# real test - it proves the DynamoDB layer is working, and every cache hit is a
# Bedrock call not paid for.
set -euo pipefail

STACK="${1:-spatial-math-interpret}"
REGION="${2:-us-east-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="${HERE}/../contracts/examples/saddle-on-a-slope.json"

ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "${STACK}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)

KEY=$("${HERE}/get-api-key.sh" "${STACK}" "${REGION}")

echo "POST ${ENDPOINT}"
echo
curl -sS -X POST "${ENDPOINT}" \
  -H 'Content-Type: application/json' \
  -H "x-api-key: ${KEY}" \
  -d @"${PAYLOAD}" | python -m json.tool
