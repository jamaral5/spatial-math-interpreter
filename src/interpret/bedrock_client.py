"""
The Bedrock call.

Uses the Anthropic SDK's Bedrock Mantle client rather than boto3's bedrock-runtime.
That is a deliberate trade:

  boto3 is already present in the Lambda runtime, so it would add nothing to the
  deployment package. But its Converse API is a lowest-common-denominator surface
  across every Bedrock vendor, which means the two features this service is built
  on - structured output and the effort setting - have to be smuggled through
  `additionalModelRequestFields` as untyped JSON.

  The Mantle client speaks the Messages API directly, so both are first-class, and
  the request shape is identical to the first-party Claude API. If this project ever
  moves off Bedrock, the change is the client constructor and nothing else.

The cost is roughly 20 MB of dependency in the bundle and a slower cold start. For an
endpoint fired by a human clicking a surface, that is the right side of the trade.
"""

import json
import logging
import re
from dataclasses import dataclass

import anthropic
from anthropic import AnthropicBedrockMantle

from config import CONFIG
from prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ModelError(RuntimeError):
    """
    A Bedrock call that did not produce a usable explanation.

    Carries an HTTP status and a stable machine-readable code so the handler can map
    failures without string-matching on messages.
    """

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


_client: AnthropicBedrockMantle | None = None


def _get_client() -> AnthropicBedrockMantle:
    """
    The Bedrock client, built once per container and reused.

    Lazy rather than constructed at import for two reasons. Constructing it resolves
    AWS credentials, so at import time a credential problem becomes an unhandled
    exception during initialisation - which Lambda reports as an opaque runtime error
    with no request context. Built here, the same problem surfaces inside the handler
    where it can be caught and returned as a clean message. It also keeps this module
    importable in a test that never intends to call Bedrock.

    Timeout maths: the Lambda is given 30s by template.yaml, and the SDK's wall-clock
    worst case is timeout x (max_retries + 1). 12s x 2 = 24s leaves headroom for the
    cache lookup and the response write. Leaving the SDK defaults (10 minutes, 2
    retries) would mean Lambda kills the container mid-retry and the caller sees a
    timeout rather than a useful error.
    """
    global _client
    if _client is None:
        _client = AnthropicBedrockMantle(
            aws_region=CONFIG.bedrock_region,
            timeout=12.0,
            max_retries=1,
        )
    return _client


@dataclass(frozen=True)
class ModelResult:
    explanation: dict[str, str]
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


# Model families that accept adaptive thinking and the effort setting.
#
# An ALLOWLIST rather than a blocklist, and that choice matters. ModelId is a free-text
# stack parameter, so an unrecognised value is far more likely to be an OLDER model than
# a newer one. An allowlist degrades safely - an unknown id gets the conservative
# request that works everywhere - whereas a blocklist would confidently send a Claude 5
# request to a 2025-era model and earn a 400 on every single call.
#
# Matched as substrings against the normalised id, which is what makes this survive both
# naming conventions: the Messages-API form (anthropic.claude-sonnet-5) and the older
# dated Bedrock form (anthropic.claude-sonnet-4-20250514-v1:0).
_ADAPTIVE_THINKING_FAMILIES = (
    "opus-5", "opus-4-8", "opus-4-7", "opus-4-6",
    "sonnet-5", "sonnet-4-6",
    "fable-5", "mythos-5",
)


def _short_form(model_id: str) -> str:
    """
    Strip a dated InvokeModel suffix, turning
    'anthropic.claude-sonnet-4-20250514-v1:0' into 'anthropic.claude-sonnet-4'.

    Used only to build a helpful error message - never to rewrite the configured id
    behind the operator's back. Silently calling a different model than the one someone
    put in samconfig.toml would be a far worse failure than a clear 404.
    """
    return re.sub(r"-\d{8}(-v\d+(:\d+)?)?$", "", model_id)


def _supports_adaptive_thinking(model_id: str) -> bool:
    normalised = model_id.lower().replace(".", "-").replace("_", "-")
    return any(family in normalised for family in _ADAPTIVE_THINKING_FAMILIES)


def _tuning_for(model_id: str) -> dict:
    """
    Per-model request tuning.

    The model id is a stack parameter, so it can legitimately be pointed at a model with
    a different feature set without anyone touching this code. Two tiers of support:

      Structured output is unconditional. It is the load-bearing feature here - it is
      what pins the response to three string fields and stops a hallucinated number
      reaching the UI as structured data - so a model that cannot do it is not a
      candidate for this service at all.

      Adaptive thinking and the effort setting arrived with the Claude 5 and 4.6+
      families. Sending either to anything older - Sonnet 4, Sonnet 4.5, Haiku 4.5 -
      is a 400 on every call, which presents as a totally broken endpoint rather than
      as a configuration problem.

    On Opus 5 thinking is on by default; it is still passed explicitly so the request
    means the same thing on models where omitting it means "off".

    Effort stays low on purpose. This endpoint restates numbers it was handed - the
    textbook description of a simple task - and low effort is the cheapest setting that
    still writes cleanly.
    """
    output_config: dict = {
        "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}
    }

    if not _supports_adaptive_thinking(model_id):
        # Logged rather than silent: running an older model is a legitimate choice, but
        # it should be a visible one. If the prose quality ever disappoints, this line
        # in CloudWatch is the first thing worth checking.
        logger.info(
            "model_tuning_conservative model_id=%s reason=no_adaptive_thinking_or_effort",
            model_id,
        )
        return {"output_config": output_config}

    output_config["effort"] = CONFIG.effort
    return {
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
    }


def generate_explanation(user_message: str) -> ModelResult:
    """
    Send one already-rendered prompt to Bedrock and return the parsed explanation.

    Raises ModelError for every failure mode; the caller never sees a raw SDK
    exception.
    """
    tuning = _tuning_for(CONFIG.model_id)

    try:
        response = _get_client().messages.create(
            model=CONFIG.model_id,
            max_tokens=CONFIG.max_tokens,
            # Explicit cache breakpoint on the system block. The system prompt is
            # byte-identical on every request, so it is the one genuinely cacheable
            # part of this call. Whether it actually caches depends on the model's
            # minimum cacheable prefix - if the prompt is shorter than that minimum,
            # this is silently ignored rather than an error. The cache token counts
            # are logged below so it can be confirmed instead of assumed.
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            **tuning,
        )

    # Most specific first. Collapsing these into one `except Exception` would lose the
    # distinction that actually matters to a caller: whether retrying could ever help.
    except anthropic.NotFoundError as exc:
        # Bedrock has two id conventions, and the console shows the wrong one for this
        # client. The dated form (anthropic.claude-sonnet-4-20250514-v1:0) belongs to
        # the legacy InvokeModel API; the Messages-API client this service uses wants
        # the short form (anthropic.claude-sonnet-4). A 404 here is far more often that
        # mismatch than a genuinely absent model, so say so rather than sending someone
        # to re-check access they already have.
        short_form = _short_form(CONFIG.model_id)
        hint = (
            f" This id is in the dated InvokeModel form; this service uses the "
            f"Messages API, so try '{short_form}' in samconfig.toml instead."
            if short_form != CONFIG.model_id
            else " Run scripts/list-models.sh to see what this account offers."
        )
        raise ModelError(
            "model_not_found",
            f"Model '{CONFIG.model_id}' was not found in {CONFIG.bedrock_region}.{hint}",
            status=502,
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ModelError(
            "model_access_denied",
            f"Access to '{CONFIG.model_id}' is denied in {CONFIG.bedrock_region}. "
            f"Open it in the Bedrock Playground and send one message - models enable "
            f"on first invocation, and Anthropic models may ask a first-time user for "
            f"use-case details. If that succeeds, check IAM and SCP policies.",
            status=502,
        ) from exc
    except anthropic.RateLimitError as exc:
        raise ModelError(
            "rate_limited",
            "Bedrock is rate limiting this account. Try again shortly.",
            status=429,
        ) from exc
    except anthropic.APITimeoutError as exc:
        raise ModelError(
            "model_timeout",
            "The model did not respond in time.",
            status=504,
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise ModelError(
            "model_unreachable", "Could not reach Bedrock.", status=502
        ) from exc
    except anthropic.APIStatusError as exc:
        raise ModelError(
            "model_error",
            f"Bedrock returned {exc.status_code}.",
            status=502,
        ) from exc

    # Check why generation stopped BEFORE reading content. A refusal is an HTTP 200
    # whose content is not the answer, so reading blindly would hand the student a
    # policy message formatted as a maths explanation.
    if response.stop_reason == "refusal":
        raise ModelError(
            "model_refused",
            "The model declined to answer this request.",
            status=502,
        )

    if response.stop_reason == "max_tokens":
        # The JSON is truncated mid-string and will not parse. Worth its own code:
        # it means MaxTokens is set too low, which is a configuration fix rather
        # than a transient failure.
        raise ModelError(
            "response_truncated",
            "The explanation was cut off by the token limit.",
            status=502,
        )

    text = next(
        (block.text for block in response.content if block.type == "text"), None
    )
    if not text:
        raise ModelError("empty_response", "The model returned no text.", status=502)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Should be unreachable: structured output guarantees valid JSON. Kept because
        # "should be unreachable" is not the same as "is unreachable", and a schema
        # failure that silently produced garbage prose would be very hard to trace.
        raise ModelError(
            "malformed_response",
            "The model's response was not valid JSON.",
            status=502,
        ) from exc

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    logger.info(
        "bedrock_call",
        extra={
            "model_id": CONFIG.model_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": cache_read,
            "stop_reason": response.stop_reason,
        },
    )

    return ModelResult(
        explanation={
            "surfaceType": str(parsed.get("surfaceType", "")),
            "atThisPoint": str(parsed.get("atThisPoint", "")),
            "directionalBehavior": str(parsed.get("directionalBehavior", "")),
        },
        model_id=CONFIG.model_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=cache_read,
    )
