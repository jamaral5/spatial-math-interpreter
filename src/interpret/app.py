"""
Lambda entry point.

The orchestration is deliberately boring, and the ordering is the whole design:

    validate  ->  cache lookup  ->  build prompt  ->  Bedrock  ->  cache write

The cache sits in front of the model, not behind it, because the model call is the
only step that costs money per request. Everything before it is free.

Note what this file does NOT do: no arithmetic, no geometry, no classification. Every
number in the response either came from Unity or came back from the model as prose.
That is the project's central rule, and keeping the handler this thin is what makes it
easy to confirm at a glance.
"""

import json
import logging
import os

from bedrock_client import ModelError, generate_explanation
from config import CONFIG, SCHEMA_VERSION
from payload import PayloadError, parse_request
from prompt import build_user_message
from store import cache_key, get_cached, put_cached

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_CORS_HEADERS = {
    "Content-Type": "application/json",
    # Only consulted by a browser, so this matters for a future WebGL build and is
    # inert for a standalone or headset build.
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def _respond(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body),
    }


def _error(status: int, code: str, message: str, request_id: str = "") -> dict:
    """
    Errors carry the same top-level shape as a success, with the explanation fields
    left empty.

    That is a concession to Unity's JsonUtility, which cannot express a discriminated
    union or an optional nested object. One flat shape means the C# client has exactly
    one DTO and checks one field (errorCode) rather than guessing at which of two
    layouts arrived.
    """
    return _respond(
        status,
        {
            "schemaVersion": SCHEMA_VERSION,
            "surfaceType": "",
            "atThisPoint": "",
            "directionalBehavior": "",
            "classificationKind": "",
            "cached": False,
            "modelId": "",
            "promptVersion": CONFIG.prompt_version,
            "requestId": request_id,
            "errorCode": code,
            "errorMessage": message,
        },
    )


def _decode_body(event: dict) -> dict:
    """Pull the JSON body out of an API Gateway proxy event."""
    body = event.get("body")

    if body is None:
        raise PayloadError("request body is empty")

    # A direct `aws lambda invoke` or a unit test passes the object already decoded;
    # the gateway always passes a string. Supporting both keeps local testing honest.
    if isinstance(body, dict):
        return body

    if event.get("isBase64Encoded"):
        import base64

        try:
            body = base64.b64decode(body).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PayloadError("request body is not valid base64 UTF-8") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PayloadError("request body is not valid JSON") from exc


def lambda_handler(event: dict, context) -> dict:
    request_id = getattr(context, "aws_request_id", "") if context else ""

    # ---- validate -------------------------------------------------------
    try:
        request = parse_request(_decode_body(event))
    except PayloadError as exc:
        logger.info("bad_request: %s", exc)
        return _error(400, "invalid_payload", str(exc), request_id)

    log_context = {
        "requestId": request_id,
        "equation": request.equation,
        "kind": request.classification.kind,
    }

    # A schema mismatch is worth a log line but not a rejection. The version exists so
    # a mismatch is visible when something behaves oddly; failing the request would
    # break every older Unity build the moment the backend moves forward.
    if request.schema_version and request.schema_version != SCHEMA_VERSION:
        logger.warning(
            "schema_version_mismatch client=%s server=%s",
            request.schema_version,
            SCHEMA_VERSION,
        )

    # ---- cache lookup ---------------------------------------------------
    key = cache_key(request)
    cached = get_cached(key)

    if cached is not None:
        logger.info("cache_hit %s", json.dumps(log_context))
        return _respond(
            200,
            {
                "schemaVersion": SCHEMA_VERSION,
                **cached,
                # Echoed from Unity's own second-derivative test, never read out of
                # the model's answer. Whatever the prose says, the label the UI shows
                # is the one the C# math engine computed.
                "classificationKind": request.classification.kind,
                "cached": True,
                "modelId": CONFIG.model_id,
                "promptVersion": CONFIG.prompt_version,
                "requestId": request_id,
                "errorCode": "",
                "errorMessage": "",
            },
        )

    # ---- model ----------------------------------------------------------
    try:
        result = generate_explanation(build_user_message(request))
    except ModelError as exc:
        logger.error("model_error %s %s", exc.code, json.dumps(log_context))
        return _error(exc.status, exc.code, exc.message, request_id)

    logger.info(
        "cache_miss %s",
        json.dumps(
            {
                **log_context,
                "inputTokens": result.input_tokens,
                "outputTokens": result.output_tokens,
                "cacheReadTokens": result.cache_read_tokens,
            }
        ),
    )

    # ---- cache write ----------------------------------------------------
    # After the response is fully in hand, so a storage failure cannot cost the caller
    # an answer that has already been paid for.
    put_cached(key, request, result.explanation, result.model_id)

    return _respond(
        200,
        {
            "schemaVersion": SCHEMA_VERSION,
            **result.explanation,
            "classificationKind": request.classification.kind,
            "cached": False,
            "modelId": result.model_id,
            "promptVersion": CONFIG.prompt_version,
            "requestId": request_id,
            "errorCode": "",
            "errorMessage": "",
        },
    )
