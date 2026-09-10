"""
The handler, end to end, with Bedrock and DynamoDB stubbed.

What is being tested here is the orchestration and the response contract - that the
cache sits in front of the model, that failures come back in the shape Unity expects,
and above all that the classification Unity sees is Unity's own.
"""

import json

import pytest

import app
from bedrock_client import ModelError, ModelResult


class FakeContext:
    aws_request_id = "test-request-id"


def invoke(body: dict) -> tuple[int, dict]:
    """Call the handler the way API Gateway does, with the body as a JSON string."""
    response = app.lambda_handler(
        {"body": json.dumps(body), "isBase64Encoded": False}, FakeContext()
    )
    return response["statusCode"], json.loads(response["body"])


@pytest.fixture
def stub_model(monkeypatch):
    """Replace the Bedrock call. No test in this file should reach the network."""
    calls = []

    def fake(user_message: str) -> ModelResult:
        calls.append(user_message)
        return ModelResult(
            explanation={
                "surfaceType": "A saddle surface.",
                "atThisPoint": "The ground slopes here.",
                "directionalBehavior": "It climbs along x and falls along y.",
            },
            model_id="anthropic.claude-opus-5",
            input_tokens=500,
            output_tokens=120,
            cache_read_tokens=0,
        )

    monkeypatch.setattr(app, "generate_explanation", fake)
    return calls


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """Cache disabled by default; the cache tests opt back in."""
    monkeypatch.setattr(app, "get_cached", lambda key: None)
    monkeypatch.setattr(app, "put_cached", lambda *args, **kwargs: None)


def test_happy_path(saddle_body, stub_model):
    status, body = invoke(saddle_body)

    assert status == 200
    assert body["surfaceType"] == "A saddle surface."
    assert body["atThisPoint"] == "The ground slopes here."
    assert body["directionalBehavior"] == "It climbs along x and falls along y."
    assert body["cached"] is False
    assert body["errorCode"] == ""
    assert body["requestId"] == "test-request-id"
    assert len(stub_model) == 1


def test_classification_is_echoed_from_the_client_never_from_the_model(
    saddle_body, stub_model
):
    """
    The rule that makes the architecture defensible.

    The stubbed model returns prose that says nothing about a saddle, yet the label
    the UI renders still has to be the one Unity's second-derivative test produced.
    If this ever reads from the generated text, the maths becomes advisory.
    """
    status, body = invoke(saddle_body)

    assert status == 200
    assert body["classificationKind"] == saddle_body["classification"]["kind"]


def test_response_shape_is_identical_on_success_and_failure(saddle_body, stub_model):
    """
    Unity's JsonUtility cannot express a union type, so the C# client has exactly one
    DTO. Both outcomes must therefore carry the same keys.
    """
    _, ok = invoke(saddle_body)

    saddle_body["equation"] = ""
    _, failed = invoke(saddle_body)

    assert ok.keys() == failed.keys()


class TestValidation:
    def test_invalid_payload_is_a_400_and_never_reaches_the_model(
        self, saddle_body, stub_model
    ):
        del saddle_body["gradient"]

        status, body = invoke(saddle_body)

        assert status == 400
        assert body["errorCode"] == "invalid_payload"
        assert stub_model == []          # the expensive call was never made

    def test_malformed_json_is_a_400(self, stub_model):
        response = app.lambda_handler({"body": "{not json"}, FakeContext())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["errorCode"] == "invalid_payload"

    def test_missing_body_is_a_400(self, stub_model):
        response = app.lambda_handler({}, FakeContext())

        assert response["statusCode"] == 400

    def test_a_body_that_arrives_already_decoded_is_accepted(self, saddle_body, stub_model):
        """`sam local invoke` and a direct lambda invoke both pass an object, not a string."""
        response = app.lambda_handler({"body": saddle_body}, FakeContext())

        assert response["statusCode"] == 200


class TestCaching:
    def test_a_cache_hit_skips_the_model(self, saddle_body, stub_model, monkeypatch):
        monkeypatch.setattr(
            app,
            "get_cached",
            lambda key: {
                "surfaceType": "cached surface",
                "atThisPoint": "cached point",
                "directionalBehavior": "cached direction",
            },
        )

        status, body = invoke(saddle_body)

        assert status == 200
        assert body["cached"] is True
        assert body["surfaceType"] == "cached surface"
        assert stub_model == []          # Bedrock is the only billed step; it was skipped

    def test_a_cache_hit_still_echoes_the_clients_classification(
        self, saddle_body, stub_model, monkeypatch
    ):
        monkeypatch.setattr(
            app,
            "get_cached",
            lambda key: {
                "surfaceType": "s",
                "atThisPoint": "a",
                "directionalBehavior": "d",
            },
        )

        _, body = invoke(saddle_body)

        assert body["classificationKind"] == saddle_body["classification"]["kind"]

    def test_the_cache_is_written_after_a_miss(self, saddle_body, stub_model, monkeypatch):
        written = []
        monkeypatch.setattr(
            app, "put_cached", lambda key, req, expl, model: written.append(key)
        )

        invoke(saddle_body)

        assert len(written) == 1


class TestModelFailures:
    @pytest.mark.parametrize(
        "code,status",
        [
            ("model_refused", 502),
            ("rate_limited", 429),
            ("model_timeout", 504),
            ("response_truncated", 502),
        ],
    )
    def test_model_errors_map_to_their_status(
        self, saddle_body, monkeypatch, code, status
    ):
        def raise_error(_):
            raise ModelError(code, "something went wrong", status=status)

        monkeypatch.setattr(app, "generate_explanation", raise_error)

        got_status, body = invoke(saddle_body)

        assert got_status == status
        assert body["errorCode"] == code
        assert body["errorMessage"] == "something went wrong"
        # The explanation fields stay empty rather than absent, so the C# DTO still parses.
        assert body["surfaceType"] == ""
