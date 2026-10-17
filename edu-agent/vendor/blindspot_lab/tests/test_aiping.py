import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from blindspot_lab import LabError
from blindspot_lab.lab.aiping import AipingClient, NoRedirect, load_api_key


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self.value).encode()


class AipingTests(unittest.TestCase):
    def test_explicit_environment_key_only(self):
        with patch.dict(os.environ, {"AIPING_API_KEY": "unit-test-key"}):
            self.assertEqual(load_api_key(), "unit-test-key")

    def test_transport_sends_max_effort_and_keeps_only_final_content(self):
        client = AipingClient(api_key="unit-test-key", model="GLM-5.3")
        value = {"id": "test_id", "model": "GLM-5.3", "usage": {"total_tokens": 12},
                 "choices": [{"finish_reason": "stop", "message": {
                     "content": '{"ok":true}', "reasoning_content": "private-model-reasoning"}}]}
        with patch.object(client.opener, "open", return_value=Response(value)) as call:
            self.assertEqual(client.generate_json([], {"type": "object"}, 7), '{"ok":true}')
            request = call.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body["reasoning_effort"], "max")
            self.assertEqual(body["thinking"], {"type": "enabled"})
            self.assertEqual(call.call_args.kwargs["timeout"], 7)
            self.assertEqual(request.full_url, "https://aiping.cn/api/v1/chat/completions")
        log = json.dumps(client.calls)
        self.assertNotIn("unit-test-key", log)
        self.assertNotIn("private-model-reasoning", log)

    def test_provider_error_and_redirect_never_echo_key(self):
        client = AipingClient(api_key="unit-test-key")
        err = HTTPError("https://aiping.cn/api/v1/chat/completions", 401,
                        "unit-test-key", {}, io.BytesIO(b"unit-test-key"))
        with patch.object(client.opener, "open", side_effect=err):
            with self.assertRaises(LabError) as exc:
                client.complete([])
        self.assertNotIn("unit-test-key", json.dumps(exc.exception.to_dict()))
        self.assertNotIn("unit-test-key", json.dumps(client.calls))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_unauthorized_model_and_truncated_output_are_rejected(self):
        with self.assertRaises(ValueError):
            AipingClient(api_key="unit-test-key", model="a-different-model")
        client = AipingClient(api_key="unit-test-key")
        value = {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
        with patch.object(client.opener, "open", return_value=Response(value)):
            with self.assertRaises(LabError) as exc:
                client.complete([])
        self.assertEqual(exc.exception.code, "MODEL_OUTPUT_INVALID")
