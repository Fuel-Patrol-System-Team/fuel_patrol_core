import json
import math

import pytest
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.helpers.renderers import RoundedJSONRenderer, round_floats


def test_rounds_to_hundredths():
    assert round_floats(0.2567) == 0.26
    assert round_floats(1.0) == 1.0
    assert round_floats(12.3456789) == 12.35


def test_half_case_is_python_round_not_half_up():
    # решение: обычное питоновское округление, 0.125 -> 0.12
    assert round_floats(0.125) == 0.12


def test_no_negative_zero():
    out = round_floats(-0.001)
    assert out == 0.0 and math.copysign(1, out) == 1.0


def test_non_finite_become_none():
    assert round_floats({"a": float("nan"), "b": float("inf"), "c": float("-inf")}) == {"a": None, "b": None, "c": None}


def test_ints_bools_strings_none_untouched():
    data = {"i": 5, "b": True, "s": "0.12345", "n": None, "big": 10**20}
    assert round_floats(data) == data
    assert round_floats(True) is True


def test_nested_structures_and_tuples():
    data = {"data": [{"v": 1.23456, "xs": [0.111, 0.999, (2.345,)]}], "count": 3}
    assert round_floats(data) == {"data": [{"v": 1.23, "xs": [0.11, 1.0, [2.35]]}], "count": 3}


def test_skip_keys_are_untouched_with_subtree_and_case_insensitive():
    data = {
        "latitude": 48.7114512,
        "Longitude": 44.4968812,
        "grades": [{"input": 1.23456, "output": 63.98765}],
        "metadata": {"median_degree": 15.123456, "expr": "a>b"},
        "spent_fuel": 1.23456,
    }
    out = round_floats(data)
    assert out["latitude"] == 48.7114512
    assert out["Longitude"] == 44.4968812
    assert out["grades"] == data["grades"]
    assert out["metadata"] == data["metadata"]
    assert out["spent_fuel"] == 1.23


def test_input_is_not_mutated():
    data = {"a": [1.23456], "b": {"c": 2.34567}}
    snapshot = json.loads(json.dumps(data))
    round_floats(data)
    assert data == snapshot


def test_numpy_float_is_rounded_to_plain_float():
    np = pytest.importorskip("numpy")
    out = round_floats(np.float64(0.2567))
    assert out == 0.26 and type(out) is float


def test_renderer_output_json():
    body = RoundedJSONRenderer().render({"data": {"x": 0.2567, "n": 3, "lat": 1, "latitude": 48.71145}})
    assert json.loads(body) == {"data": {"x": 0.26, "n": 3, "lat": 1, "latitude": 48.71145}}


def test_renderer_view_opt_out_and_custom_digits():
    class Raw:
        float_round_digits = None

    class Three:
        float_round_digits = 3

    class WithSkip:
        float_round_skip_keys = {"Coef"}

    payload = {"x": 0.123456, "coef": 0.123456}
    r = RoundedJSONRenderer()
    assert json.loads(r.render(payload, renderer_context={"view": Raw()})) == payload
    assert json.loads(r.render(payload, renderer_context={"view": Three()})) == {"x": 0.123, "coef": 0.123}
    assert json.loads(r.render(payload, renderer_context={"view": WithSkip()})) == {"x": 0.12, "coef": 0.123456}


def test_renderer_handles_none_and_empty():
    assert RoundedJSONRenderer().render(None) == b""
    assert json.loads(RoundedJSONRenderer().render([])) == []


# --- сквозная проверка через настоящий DRF view с настройками проекта (app.settings) ---

class _FloatsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"data": {"fuel": 0.2567, "latitude": 48.7114512, "nan": float("nan")}})


class _RawView(_FloatsView):
    float_round_digits = None

    def get(self, request):
        return Response({"data": {"fuel": 0.2567}})


def test_project_default_renderer_is_rounded_json():
    request = APIRequestFactory().get("/x", HTTP_ACCEPT="application/json")
    response = _FloatsView.as_view()(request)
    response.render()
    assert response["Content-Type"].startswith("application/json")
    assert json.loads(response.content) == {"data": {"fuel": 0.26, "latitude": 48.7114512, "nan": None}}


def test_project_view_opt_out_end_to_end():
    request = APIRequestFactory().get("/x", HTTP_ACCEPT="application/json")
    response = _RawView.as_view()(request)
    response.render()
    assert json.loads(response.content)["data"]["fuel"] == 0.2567


def test_raw_sensor_view_is_opted_out():
    from core.views import CarSensorsRawDataAPIView

    assert CarSensorsRawDataAPIView.float_round_digits is None
