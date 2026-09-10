#!/usr/bin/env bash
# Print the API key value for the deployed stack.
#
# The key is deliberately NOT a CloudFormation output: outputs are stored in stack
# history and shown to anyone with read access to the stack, and this key maps
# directly to a Bedrock bill. It is fetched on demand instead.
#
# Paste the result into the Math Interpreter Client component in Unity. Do not commit
# it - .gitignore covers api-key.txt and .env for exactly this reason.
set -euo pipefail

STACK="${1:-spatial-math-interpret}"
REGION="${2:-us-east-1}"

KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name "${STACK}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
  --output text)

if [ -z "${KEY_ID}" ] || [ "${KEY_ID}" = "None" ]; then
  echo "Could not find ApiKeyId on stack '${STACK}'. Has it been deployed?" >&2
  exit 1
fi

aws apigateway get-api-key \
  --api-key "${KEY_ID}" --include-value --region "${REGION}" \
  --query 'value' --output text
