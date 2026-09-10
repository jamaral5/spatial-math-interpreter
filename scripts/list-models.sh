#!/usr/bin/env bash
# Which Claude models can this account actually invoke, in this region?
#
# RUN THIS BEFORE THE FIRST DEPLOY, to get the exact id for samconfig.toml.
#
# Know what it proves, though. This lists the models the region OFFERS. AWS has retired
# the old Model access page: serverless foundation models are enabled automatically on
# first invocation, so appearing here no longer means your account can invoke it. The
# only real proof is invoking it - send one message in the Bedrock Playground first.
#
# A model id your account cannot invoke fails at request time with a 404 or 403, not at
# deploy time, so the stack comes up looking healthy and then every click fails.
set -euo pipefail

REGION="${1:-us-east-1}"

echo "Foundation models (on-demand) in ${REGION}:"
aws bedrock list-foundation-models \
  --region "${REGION}" \
  --by-provider anthropic \
  --query 'modelSummaries[].modelId' \
  --output table || echo "  (call failed - is the AWS CLI configured, and Bedrock enabled here?)"

echo
echo "Inference profiles (cross-region) in ${REGION}:"
# Cross-region profiles carry a regional prefix such as us. or eu. and route to
# whichever region has capacity. Prefer one of these if the plain model id is absent.
aws bedrock list-inference-profiles \
  --region "${REGION}" \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `anthropic`)].inferenceProfileId' \
  --output table || echo "  (none, or the call failed)"

echo
echo "Put the id you want into samconfig.toml as ModelId, then: sam deploy"
