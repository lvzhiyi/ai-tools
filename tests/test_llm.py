"""路由的可观察 HTTP 边界；不访问真实模型。"""
import copy
import io
import http.client
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from studio import llm
from studio.plan import SCHEMA as PLAN_SCHEMA

SCHEMA = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 1}},
          "required": ["score"], "additionalProperties": False}
MESSAGES = [{"role": "user", "content": "hello"}]
IMAGE_MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "review"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}},
]}]


def response(content="ok", **message_fields):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content, **message_fields},
                                              "finish_reason": "stop"}]}).encode())


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"url": "http://localhost:1234/v1", "model": "local-text", "ttl_sec": 300,
                    "timeout_sec": 12, "cloud": {"enabled": True, "url": "https://cloud.example/v1",
                    "model": "cloud-text", "vision_model": "cloud-vision",
                    "api_key_env": "STUDIO_TEST_KEY", "timeout_sec": 25}}
        self.env = patch.dict(os.environ, {"STUDIO_TEST_KEY": "secret-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_local_success_never_calls_cloud(self):
        original = copy.deepcopy(self.cfg)
        with patch.object(llm.urllib.request, "urlopen", return_value=response()) as http:
            self.assertEqual(llm.chat(self.cfg, MESSAGES), "ok")
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[0].full_url, "http://localhost:1234/v1/chat/completions")
        self.assertEqual(json.loads(http.call_args.args[0].data)["ttl"], 300)
        self.assertEqual(self.cfg, original)

    def test_transport_failures_fall_back_and_separate_provider_payloads(self):
        errors = [urllib.error.URLError("offline"), TimeoutError(), ConnectionResetError(),
                  urllib.error.HTTPError("local", 503, "bad", {}, io.BytesIO(b"private script"))]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(
                    llm.urllib.request, "urlopen", side_effect=[error, response("cloud result")]) as http:
                self.assertEqual(llm.chat(self.cfg, MESSAGES, temperature=0.4, timeout=40), "cloud result")
                first, second = http.call_args_list
                self.assertEqual(first.kwargs["timeout"], 12)
                self.assertEqual(second.kwargs["timeout"], 25)
                req = second.args[0]
                self.assertEqual(req.full_url, "https://cloud.example/v1/chat/completions")
                self.assertEqual(req.get_header("Authorization"), "Bearer secret-token")
                body = json.loads(req.data)
                self.assertEqual(body["model"], "cloud-text")
                self.assertEqual(body["messages"], MESSAGES)
                self.assertEqual(body["temperature"], 0.4)
                self.assertNotIn("ttl", body)

    def test_bad_responses_trigger_fallback(self):
        bad = [b"not-json", b"{}", b'{"choices": []}', b'null', b'{"choices": [null]}']
        for payload in bad:
            with self.subTest(payload=payload), patch.object(llm.urllib.request, "urlopen",
                    side_effect=[io.BytesIO(payload), response()]):
                self.assertEqual(llm.chat(self.cfg, MESSAGES), "ok")
        for content in [None, "", "   ", [], {"text": "unexpected"}]:
            with self.subTest(content=content), patch.object(llm.urllib.request, "urlopen",
                    side_effect=[response(content), response()]):
                self.assertEqual(llm.chat(self.cfg, MESSAGES), "ok")

    def test_invalid_structured_results_trigger_fallback(self):
        for content in ["garbage", "[]", "{}", '{"score": "1"}', '{"score": 0}']:
            with self.subTest(content=content), patch.object(llm.urllib.request, "urlopen",
                    side_effect=[response(content), response('```json\n{"score": 2}\n```')]):
                self.assertEqual(llm.chat(self.cfg, MESSAGES, SCHEMA), {"score": 2})

    def test_disabled_or_incomplete_cloud_does_not_break_local(self):
        for change in [{"enabled": False}, {"model": ""}, {"api_key_env": "MISSING_STUDIO_KEY"}]:
            cfg = copy.deepcopy(self.cfg)
            cfg["cloud"].update(change)
            with self.subTest(change=change), patch.object(llm.urllib.request, "urlopen",
                    return_value=response()) as http:
                self.assertEqual(llm.chat(cfg, MESSAGES), "ok")
                self.assertEqual(http.call_count, 1)
            with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()) as http:
                with self.assertRaises(llm.LLMError):
                    llm.chat(cfg, MESSAGES)
                self.assertEqual(http.call_count, 1)

    def test_legacy_config_and_timeout_still_work(self):
        cfg = {"url": "http://localhost:1234/v1", "model": "local"}
        with patch.object(llm.urllib.request, "urlopen", return_value=response()) as http:
            self.assertEqual(llm.chat(cfg, MESSAGES, timeout=42), "ok")
            self.assertEqual(http.call_args.kwargs["timeout"], 42)

    def test_vision_uses_cloud_vision_model_not_local_override(self):
        with patch.object(llm.urllib.request, "urlopen",
                          side_effect=[TimeoutError(), response()]) as http:
            self.assertEqual(llm.chat(self.cfg, IMAGE_MESSAGES, model="local-vision"), "ok")
            self.assertEqual(json.loads(http.call_args.args[0].data)["model"], "cloud-vision")
        self.cfg["cloud"]["vision_model"] = ""
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()) as http:
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, IMAGE_MESSAGES)
            self.assertEqual(http.call_count, 1)

    def test_combined_error_does_not_leak_provider_body_or_key(self):
        errors = [TimeoutError(), urllib.error.HTTPError("cloud", 401, "bad", {},
                  io.BytesIO(b"secret-token private script"))]
        with patch.object(llm.urllib.request, "urlopen", side_effect=errors):
            with self.assertRaises(llm.LLMError) as result:
                llm.chat(self.cfg, MESSAGES)
        self.assertIn("本地", str(result.exception))
        self.assertIn("云端", str(result.exception))
        self.assertIn("401", str(result.exception))
        self.assertNotIn("secret-token", str(result.exception))
        self.assertNotIn("private script", str(result.exception))

    def test_broken_response_transfer_falls_back(self):
        with patch.object(llm.urllib.request, "urlopen", side_effect=[
                http.client.IncompleteRead(b"partial"), response()]):
            self.assertEqual(llm.chat(self.cfg, MESSAGES), "ok")

    def test_protocol_errors_do_not_leak_response_text(self):
        secret = "private script secret-token"
        with patch.object(llm.urllib.request, "urlopen", side_effect=[
                TimeoutError(), http.client.BadStatusLine(secret)]):
            with self.assertRaises(llm.LLMError) as result:
                llm.chat(self.cfg, MESSAGES)
        self.assertNotIn(secret, str(result.exception))
        with patch.object(llm.urllib.request, "urlopen", side_effect=http.client.BadStatusLine(secret)):
            self.assertTrue(llm.available(self.cfg, vision=True))
            self.cfg["cloud"]["enabled"] = False
            self.assertFalse(llm.available(self.cfg, vision=True))

    def test_bad_cloud_url_still_reports_local_error(self):
        self.cfg["cloud"]["url"] = "http://[invalid"
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()):
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES)

    def test_local_structured_output_is_validated_and_returned(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=response('{"score": 3}')) as http:
            self.assertEqual(llm.chat(self.cfg, MESSAGES, SCHEMA), {"score": 3})
            self.assertEqual(http.call_count, 1)
            self.assertEqual(json.loads(http.call_args.args[0].data)["response_format"]["type"], "json_schema")

    def test_refusal_or_truncated_output_falls_back(self):
        for message in [response("refused", refusal="no"), io.BytesIO(json.dumps({
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}).encode())]:
            with patch.object(llm.urllib.request, "urlopen", side_effect=[message, response()]):
                self.assertEqual(llm.chat(self.cfg, MESSAGES), "ok")

    def test_cancel_does_not_trigger_cloud(self):
        with patch.object(llm.urllib.request, "urlopen", side_effect=KeyboardInterrupt()) as http:
            with self.assertRaises(KeyboardInterrupt):
                llm.chat(self.cfg, MESSAGES)
            self.assertEqual(http.call_count, 1)

    def test_strict_project_schemas_disallow_extra_properties(self):
        from studio.vision import SCHEMA as vision_schema
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        check(PLAN_SCHEMA)
        check(vision_schema)

    def test_available_keeps_cloud_vision_when_local_is_down(self):
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()):
            self.assertTrue(llm.available(self.cfg, vision=True))
            self.cfg["cloud"]["vision_model"] = ""
            self.assertFalse(llm.available(self.cfg, vision=True))


if __name__ == "__main__":
    unittest.main()
