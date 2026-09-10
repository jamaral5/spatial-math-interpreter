"""
The request contract.

API Gateway already validates the body against the JSON Schema in template.yaml, so
in production a malformed payload never reaches this module. It is re-validated here
anyway for three reasons:

  1. `sam local invoke` and a direct `aws lambda invoke` both bypass the gateway
     entirely, and those are the paths used while developing.
  2. The gateway validates SHAPE, not SENSE. It cannot say "this number is NaN" or
     "this classification is not one the math engine can produce".
  3. The equation string is typed by a user and ends up inside a prompt. That makes
     it untrusted input, and untrusted input gets checked at the boundary it crosses.

Wire format is camelCase. That is not an aesthetic choice: Unity's built-in
JsonUtility maps JSON keys onto C# field names verbatim, with no rename attributes,
so snake_case on the wire would force snake_case field names through the whole C#
client. camelCase keeps both sides idiomatic.
"""

import math
import re
from dataclasses import dataclass
from typing import Any


class PayloadError(ValueError):
    """A request that cannot be honoured. Maps to HTTP 400."""


# The equation grammar Unity's parser actually accepts: digits, the variables x and y,
# function names, operators, parentheses, commas and decimal points.
#
# This is the first of three layers of prompt-injection defence. On its own it is not
# sufficient - "ignore previous instructions" is unfortunately all letters and spaces -
# so it is backed by (2) the equation being rendered inside a delimited data block that
# the system prompt names as untrusted, and (3) structured output, which pins the
# response to three fixed string fields no matter what the model was talked into.
_EQUATION_ALLOWED = re.compile(r"^[0-9a-zA-Z+\-*/^(),.\s]+$")
_MAX_EQUATION_LENGTH = 256

# Classifications the C# SurfaceAnalyzer can emit. Anything else means the two halves
# of the system have drifted apart, and it is better to fail loudly here than to hand
# the model a label it will cheerfully invent meaning for.
VALID_SHAPES = frozenset({"bowl", "dome", "saddle", "troughOrRidge", "undefined"})


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    f: float


@dataclass(frozen=True)
class Gradient:
    dfdx: float
    dfdy: float
    magnitude: float
    steepest_ascent_heading_degrees: float | None


@dataclass(frozen=True)
class Curvature:
    d2fdx2: float
    d2fdy2: float
    d2fdxdy: float
    discriminant: float


@dataclass(frozen=True)
class Classification:
    kind: str
    shape: str
    is_critical_point: bool


@dataclass(frozen=True)
class InterpretRequest:
    schema_version: str
    equation: str
    point: Point
    gradient: Gradient
    curvature: Curvature
    classification: Classification
    tangent_plane_equation: str | None
    step_h: float | None
    domain_range: float | None


def _require(body: dict[str, Any], key: str) -> Any:
    if key not in body:
        raise PayloadError(f"missing required field: {key}")
    return body[key]


def _obj(body: dict[str, Any], key: str) -> dict[str, Any]:
    value = _require(body, key)
    if not isinstance(value, dict):
        raise PayloadError(f"field '{key}' must be an object")
    return value


def _number(source: dict[str, Any], key: str, path: str) -> float:
    """
    Coerce to float and reject anything that is not a real, finite number.

    NaN and infinity matter here specifically. Unity's Evaluate() returns a float and
    a divergent function (1/x at the origin, log of a negative) genuinely produces
    them. json.loads happily parses the literals NaN and Infinity, and json.dumps
    happily emits them - so without this check a NaN would travel the whole way to the
    prompt and be described to a student as if it were a slope.
    """
    if key not in source:
        raise PayloadError(f"missing required field: {path}.{key}")

    value = source[key]
    # bool is a subclass of int in Python, so True would otherwise pass as 1.0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"field '{path}.{key}' must be a number")

    value = float(value)
    if not math.isfinite(value):
        raise PayloadError(
            f"field '{path}.{key}' is not finite - the surface is undefined or "
            f"divergent at this point, so there is nothing meaningful to explain"
        )
    return value


def _optional_number(source: dict[str, Any], key: str, path: str) -> float | None:
    if key not in source or source[key] is None:
        return None
    return _number(source, key, path)


def parse_request(body: dict[str, Any]) -> InterpretRequest:
    """Turn a decoded JSON body into a validated InterpretRequest, or raise PayloadError."""
    if not isinstance(body, dict):
        raise PayloadError("request body must be a JSON object")

    equation = _require(body, "equation")
    if not isinstance(equation, str):
        raise PayloadError("field 'equation' must be a string")

    equation = equation.strip()
    if not equation:
        raise PayloadError("field 'equation' must not be empty")
    if len(equation) > _MAX_EQUATION_LENGTH:
        raise PayloadError(
            f"field 'equation' exceeds {_MAX_EQUATION_LENGTH} characters"
        )
    if not _EQUATION_ALLOWED.match(equation):
        raise PayloadError(
            "field 'equation' contains characters outside the supported math grammar"
        )

    point_raw = _obj(body, "point")
    gradient_raw = _obj(body, "gradient")
    curvature_raw = _obj(body, "curvature")
    classification_raw = _obj(body, "classification")

    kind = classification_raw.get("kind")
    shape = classification_raw.get("shape")
    is_critical = classification_raw.get("isCriticalPoint")

    if not isinstance(kind, str) or not kind.strip():
        raise PayloadError("field 'classification.kind' must be a non-empty string")
    if shape not in VALID_SHAPES:
        raise PayloadError(
            f"field 'classification.shape' must be one of {sorted(VALID_SHAPES)}"
        )
    if not isinstance(is_critical, bool):
        raise PayloadError("field 'classification.isCriticalPoint' must be a boolean")

    tangent_plane = body.get("tangentPlane") or {}
    tangent_equation = tangent_plane.get("equation") if isinstance(tangent_plane, dict) else None
    if tangent_equation is not None and not isinstance(tangent_equation, str):
        raise PayloadError("field 'tangentPlane.equation' must be a string")

    sampling = body.get("sampling") or {}
    if not isinstance(sampling, dict):
        raise PayloadError("field 'sampling' must be an object")

    return InterpretRequest(
        schema_version=str(body.get("schemaVersion", "")),
        equation=equation,
        point=Point(
            x=_number(point_raw, "x", "point"),
            y=_number(point_raw, "y", "point"),
            f=_number(point_raw, "f", "point"),
        ),
        gradient=Gradient(
            dfdx=_number(gradient_raw, "dfdx", "gradient"),
            dfdy=_number(gradient_raw, "dfdy", "gradient"),
            magnitude=_number(gradient_raw, "magnitude", "gradient"),
            steepest_ascent_heading_degrees=_optional_number(
                gradient_raw, "steepestAscentHeadingDegrees", "gradient"
            ),
        ),
        curvature=Curvature(
            d2fdx2=_number(curvature_raw, "d2fdx2", "curvature"),
            d2fdy2=_number(curvature_raw, "d2fdy2", "curvature"),
            d2fdxdy=_number(curvature_raw, "d2fdxdy", "curvature"),
            discriminant=_number(curvature_raw, "discriminant", "curvature"),
        ),
        classification=Classification(
            kind=kind.strip(),
            shape=shape,
            is_critical_point=is_critical,
        ),
        tangent_plane_equation=tangent_equation,
        step_h=_optional_number(sampling, "stepH", "sampling"),
        domain_range=_optional_number(sampling, "domainRange", "sampling"),
    )
