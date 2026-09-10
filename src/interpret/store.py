"""
DynamoDB cache and query history.

Two access patterns, one table:

  1. "Have I explained this exact point before?"  - get_item on the partition key.
     Bedrock is the only usage-billed part of the stack, so every cache hit is a
     direct saving. Clicking around one interesting feature of a surface produces a
     lot of near-identical requests.

  2. "What has been asked about this equation?"   - query the GSI on
     (equation, createdAt). Not used by the endpoint yet; it is what a history panel
     in Unity would read, and having the index in place from the start costs nothing
     on an on-demand table.

Every function here fails soft. A cache is an optimisation, and an optimisation that
can take down the endpoint is a liability - if DynamoDB is unavailable the request
should still be answered, just more expensively.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import CONFIG
from payload import InterpretRequest

logger = logging.getLogger(__name__)

# Module scope, so the connection pool survives across warm invocations.
_table = None
if CONFIG.cache_enabled:
    _table = boto3.resource("dynamodb").Table(CONFIG.cache_table)


# Two decimal places, chosen to match the UI rather than picked arbitrarily.
#
# TangentReadout and the tangent-plane label both format with "F2", so the student
# sees two decimals. If two clicks are indistinguishable on screen, they describe the
# same situation and deserve the same explanation - which makes rounding the key to
# display precision exactly right, and incidentally turns a stream of near-misses
# around one feature of a surface into repeated cache hits.
_KEY_PRECISION = 2


def cache_key(request: InterpretRequest) -> str:
    """
    Build the partition key: a sha256 over everything that could change the answer.

    The model id and prompt version are part of the key on purpose. Changing either
    changes the wording that would be produced, so entries written by the old
    configuration must not be served by the new one - they age out by TTL instead of
    being served as though they were current.
    """
    r = _KEY_PRECISION
    material = {
        "promptVersion": CONFIG.prompt_version,
        "modelId": CONFIG.model_id,
        "equation": request.equation,
        "point": [
            round(request.point.x, r),
            round(request.point.y, r),
            round(request.point.f, r),
        ],
        "gradient": [
            round(request.gradient.dfdx, r),
            round(request.gradient.dfdy, r),
        ],
        "curvature": [
            round(request.curvature.d2fdx2, r),
            round(request.curvature.d2fdy2, r),
            round(request.curvature.d2fdxdy, r),
        ],
        "classification": [
            request.classification.kind,
            request.classification.shape,
            request.classification.is_critical_point,
        ],
    }

    # sort_keys is what makes this deterministic. Without it, two dicts holding the
    # same data could serialise in different orders and hash differently, and the
    # cache would silently never hit.
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_cached(key: str) -> dict[str, str] | None:
    """Return a stored explanation, or None on a miss or any failure."""
    if _table is None:
        return None

    try:
        result = _table.get_item(Key={"pk": key}, ProjectionExpression="explanation")
    except (ClientError, BotoCoreError):
        # Deliberately not re-raised: a broken cache read should cost money, not
        # availability.
        logger.warning("cache_read_failed", exc_info=True)
        return None

    item = result.get("Item")
    if not item or "explanation" not in item:
        return None

    try:
        return json.loads(item["explanation"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("cache_entry_corrupt", extra={"pk": key})
        return None


def put_cached(
    key: str,
    request: InterpretRequest,
    explanation: dict[str, str],
    model_id: str,
) -> None:
    """
    Store an explanation. Never raises.

    Numbers are written as a JSON string rather than as DynamoDB number attributes.
    boto3's resource interface rejects Python floats outright (it wants Decimal), and
    converting a nested structure back and forth would add a class of bug for no gain:
    nothing queries these values, they exist so a history view can display what was
    asked.
    """
    if _table is None:
        return

    now = datetime.now(timezone.utc)

    try:
        _table.put_item(
            Item={
                "pk": key,
                # GSI keys - the history access pattern.
                "equation": request.equation,
                "createdAt": now.isoformat(),
                # DynamoDB TTL wants epoch seconds as a number, and it deletes within
                # roughly 48 hours of expiry rather than exactly on time. That is fine
                # for a cache and worth knowing before it looks like a bug.
                "ttl": int(time.time()) + CONFIG.cache_ttl_days * 86400,
                "explanation": json.dumps(explanation),
                "request": json.dumps(
                    {
                        "point": {
                            "x": request.point.x,
                            "y": request.point.y,
                            "f": request.point.f,
                        },
                        "gradient": {
                            "dfdx": request.gradient.dfdx,
                            "dfdy": request.gradient.dfdy,
                        },
                        "classification": {
                            "kind": request.classification.kind,
                            "shape": request.classification.shape,
                            "isCriticalPoint": request.classification.is_critical_point,
                        },
                    }
                ),
                "modelId": model_id,
                "promptVersion": CONFIG.prompt_version,
            }
        )
    except (ClientError, BotoCoreError):
        logger.warning("cache_write_failed", exc_info=True)
