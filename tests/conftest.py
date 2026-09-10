"""
Test setup.

Two things have to happen before any module under test is imported, and both are the
reason this file exists rather than the setup living in the tests themselves.

First, the import path. Lambda puts the handler's own directory on sys.path, which is
why app.py can write `from config import CONFIG` rather than
`from src.interpret.config import CONFIG`. Reproducing that here means the tests
exercise the same import graph that runs in production, instead of a rearranged one
that happens to work locally.

Second, the environment. config.py reads os.environ at import time - deliberately, so
a warm container pays for it once - which means the variables must be set before the
first import of anything that reaches it, not inside a fixture.
"""

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "interpret"
sys.path.insert(0, str(SRC))

os.environ.setdefault("MODEL_ID", "anthropic.claude-opus-5")
os.environ.setdefault("BEDROCK_REGION", "us-east-1")
os.environ.setdefault("EFFORT", "low")
os.environ.setdefault("MAX_TOKENS", "1024")
os.environ.setdefault("PROMPT_VERSION", "v1")
# Left empty on purpose: no table name means the cache is disabled, so the tests never
# reach for DynamoDB and never need credentials.
os.environ.setdefault("CACHE_TABLE", "")
os.environ.setdefault("CACHE_TTL_DAYS", "30")

import pytest  # noqa: E402


@pytest.fixture
def saddle_body() -> dict:
    """
    A well-formed request: the point (1, 1) on z = x^2 - y^2.

    Every number here is what the C# SurfaceAnalyzer actually produces for that point,
    not an invented value - fxx = 2, fyy = -2, fxy = 0, so the discriminant is -4 and
    the classification is a saddle. Using real output means a change that breaks the
    contract between the two halves shows up as a test failure.
    """
    return {
        "schemaVersion": "1.0",
        "equation": "x^2 - y^2",
        "point": {"x": 1.0, "y": 1.0, "f": 0.0},
        "gradient": {
            "dfdx": 2.0,
            "dfdy": -2.0,
            "magnitude": 2.8284,
            "steepestAscentHeadingDegrees": 315.0,
        },
        "curvature": {
            "d2fdx2": 2.0,
            "d2fdy2": -2.0,
            "d2fdxdy": 0.0,
            "discriminant": -4.0,
        },
        "classification": {
            "kind": "sloping, with saddle-like curvature",
            "shape": "saddle",
            "isCriticalPoint": False,
        },
        "tangentPlane": {"equation": "f(x, y) = 0.00 + 2.00(x - 1.00) - 2.00(y - 1.00)"},
        "sampling": {"stepH": 0.05, "domainRange": 5.0},
    }


@pytest.fixture
def origin_saddle_body(saddle_body) -> dict:
    """The origin of the same surface, where the ground IS level - a true saddle point."""
    body = dict(saddle_body)
    body["point"] = {"x": 0.0, "y": 0.0, "f": 0.0}
    body["gradient"] = {"dfdx": 0.0, "dfdy": 0.0, "magnitude": 0.0}
    body["classification"] = {
        "kind": "saddle point",
        "shape": "saddle",
        "isCriticalPoint": True,
    }
    return body
