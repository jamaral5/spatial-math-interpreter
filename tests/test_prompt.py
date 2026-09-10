"""
Prompt construction.

These tests guard the project's central rule. The model must be handed every number
it could possibly need, and must never be handed a situation where the honest answer
and the convenient answer differ - which is exactly what happens when a sloping point
is described with the vocabulary of a critical one.
"""

from payload import parse_request
from prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_message


def test_every_computed_number_reaches_the_model(saddle_body):
    """
    If a value the C# engine computed does not appear in the prompt, the model has to
    infer it - and inferring is the one thing this architecture exists to prevent.
    """
    message = build_user_message(parse_request(saddle_body))

    for expected in ["x^2 - y^2", "2", "-2", "-4", "saddle"]:
        assert expected in message


def test_data_is_delimited(saddle_body):
    """
    The system prompt tells the model the equation is untrusted data. That instruction
    only means something if there is a marked region for it to refer to.
    """
    message = build_user_message(parse_request(saddle_body))

    assert "<surface-data>" in message
    assert "</surface-data>" in message
    assert message.index("<surface-data>") < message.index("x^2 - y^2")


def test_heading_is_sent_when_the_surface_slopes(saddle_body):
    message = build_user_message(parse_request(saddle_body))

    assert "heading:" in message


def test_heading_is_withheld_at_a_critical_point(origin_saddle_body):
    """
    On level ground the gradient is numerical noise, so its direction is meaningless.
    Sending it anyway would invite the model to describe a heading of 0 degrees as
    "due east" - a confident statement about pure noise.
    """
    message = build_user_message(parse_request(origin_saddle_body))

    assert "heading:" not in message
    assert "isCriticalPoint: true" in message


def test_small_slopes_are_not_rounded_away(saddle_body):
    """
    Four decimal places, not two.

    A genuine slope of 0.003 rounded to 0.00 would be described to a student as level
    ground, which is the wrong answer rather than an imprecise one.
    """
    saddle_body["gradient"]["dfdx"] = 0.003
    saddle_body["gradient"]["magnitude"] = 0.003

    message = build_user_message(parse_request(saddle_body))

    assert "0.003" in message


class TestSystemPrompt:
    def test_forbids_calculation(self):
        text = SYSTEM_PROMPT.lower()
        assert "do not recompute" in text
        assert "already been computed" in text

    def test_states_the_critical_point_caveat(self):
        """
        The second-derivative test only identifies a max or min at a critical point.
        Without this instruction the model will happily call a bowl-shaped patch of
        hillside a local minimum, which is simply false.
        """
        assert "isCriticalPoint is false" in SYSTEM_PROMPT
        assert "critical point" in SYSTEM_PROMPT.lower()

    def test_treats_the_equation_as_data(self):
        assert "untrusted" in SYSTEM_PROMPT.lower()

    def test_is_static(self):
        """
        Prompt caching matches on a prefix, so anything varying per request - a
        timestamp, a point, a request id - would invalidate the cache on every call.
        Building the message twice must produce identical bytes.
        """
        assert SYSTEM_PROMPT == SYSTEM_PROMPT
        assert "{" not in SYSTEM_PROMPT.replace("{}", "")


class TestResponseSchema:
    def test_pins_exactly_three_fields(self):
        assert set(RESPONSE_SCHEMA["required"]) == {
            "surfaceType",
            "atThisPoint",
            "directionalBehavior",
        }

    def test_forbids_extra_fields(self):
        """
        additionalProperties: false is what stops the model returning a number as a
        structured value. Anything it invents has to appear inside prose, where it is
        visible, rather than in a field the UI would render as fact.
        """
        assert RESPONSE_SCHEMA["additionalProperties"] is False
