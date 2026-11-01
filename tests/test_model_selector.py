import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import cli, llm
from studio.web import app as webapp
from tests.test_llm import response, MESSAGES


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"url": "http://localhost:1234/v1", "model": "local", "routing_mode": "local",
                    "cloud": {"enabled": True, "url": "https://cloud.example/v1", "model": "cloud",
                              "vision_model": "vision", "api_key_env": "SELECTOR_TEST_KEY"}}
        self.env = patch.dict(os.environ, {"SELECTOR_TEST_KEY": "fake-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_local_failure_never_calls_cloud(self):
        with patch.object(llm.urllib.request, "urlopen", side_effect=[TimeoutError(), response()]) as http:
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES)
            self.assertEqual(http.call_count, 1)

    def test_cloud_skips_local_and_local_model_override(self):
        self.cfg["routing_mode"] = "cloud"
        with patch.object(llm.urllib.request, "urlopen", return_value=response()) as http:
            self.assertEqual(llm.chat(self.cfg, MESSAGES, model="local-override"), "ok")
            req = http.call_args.args[0]
            self.assertEqual(req.full_url, "https://cloud.example/v1/chat/completions")
            self.assertEqual(json.loads(req.data)["model"], "cloud")

    def test_cloud_failure_never_falls_back_to_local(self):
        self.cfg["routing_mode"] = "cloud"
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()) as http:
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES)
            self.assertEqual(http.call_count, 1)
            self.assertIn("cloud.example", http.call_args.args[0].full_url)

    def test_availability_obeys_mode(self):
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError()) as http:
            self.assertFalse(llm.available(self.cfg, vision=True))
            self.cfg["routing_mode"] = "cloud"
            count = http.call_count
            self.assertTrue(llm.available(self.cfg, vision=True))
            self.assertEqual(http.call_count, count)

    def test_api_key_env_mistake_does_not_echo_secret(self):
        self.cfg["cloud"]["api_key_env"] = "sk-private-secret"
        ready, reason = llm.cloud_status(self.cfg)
        self.assertFalse(ready)
        self.assertNotIn("sk-private-secret", reason)

    def test_cli_applies_explicit_mode_without_editing_config(self):
        cfg = {"lmstudio": self.cfg}
        cfg["lmstudio"]["cloud"]["enabled"] = False
        with patch.object(cli, "load_config", return_value=cfg), patch.object(cli, "cmd_plan", return_value=0) as plan:
            self.assertEqual(cli.main(["--llm-mode", "cloud", "plan", "dummy.txt"]), 0)
        self.assertEqual(plan.call_args.args[0]["lmstudio"]["routing_mode"], "cloud")
        self.assertTrue(plan.call_args.args[0]["lmstudio"]["cloud"]["enabled"])


class SettingsTests(unittest.TestCase):
    def test_persistence_validation_and_job_snapshot(self):
        from studio.web import model_routing as routing
        from studio.web import jobs
        with tempfile.TemporaryDirectory() as directory, patch.object(routing, "SETTINGS_FILE", Path(directory)/"mode.json"), patch.object(
                routing, "load_config", return_value={"lmstudio": {"cloud": {"enabled": False}}}), patch.object(
                jobs, "JOBS_DIR", Path(directory)/"jobs"), patch.object(jobs.threading.Thread, "start"):
            self.assertEqual(routing.get_mode(), "local")
            routing.set_mode("auto")
            manager = jobs.JobManager()
            first = manager.submit("plan", ["plan", "test.txt"], "test")
            routing.set_mode("local")
            second = manager.submit("plan", ["plan", "test.txt"], "test")
            self.assertEqual(first.argv[:2], ["--llm-mode", "auto"])
            self.assertEqual(second.argv[:2], ["--llm-mode", "local"])
            with self.assertRaises(ValueError):
                routing.set_mode("bad-mode")
            self.assertEqual(routing.get_mode(), "local")
            restored = jobs.JobManager()
            self.assertEqual(restored.get(first.id).argv[:2], ["--llm-mode", "auto"])

    def test_mode_api_round_trip_and_invalid_input(self):
        from studio.web import model_routing as routing
        async def request(method, body=None):
            result = []
            async def receive():
                return {"type": "http.request", "body": json.dumps(body or {}).encode()}
            async def send(message):
                result.append(message)
            app = webapp.create_app()
            await app({"type": "http", "method": method, "path": "/api/model-routing", "raw_path": b"/api/model-routing",
                       "query_string": b"", "headers": [], "scheme": "http", "server": ("test", 80),
                       "client": ("127.0.0.1", 1), "root_path": "", "http_version": "1.1"}, receive, send)
            return result[0]["status"], json.loads(result[-1]["body"])
        with tempfile.TemporaryDirectory() as directory, patch.object(routing, "SETTINGS_FILE", Path(directory)/"mode.json"), patch.object(
                routing, "load_config", return_value={"lmstudio": {"model": "local", "cloud": {"enabled": False}}}):
            status, data = asyncio.run(request("GET"))
            self.assertEqual(status, 200)
            self.assertEqual(data["mode"], "local")
            status, data = asyncio.run(request("PUT", {"mode": "auto"}))
            self.assertEqual(status, 200)
            self.assertEqual(data["mode"], "auto")
            status, _ = asyncio.run(request("PUT", {"mode": "invalid"}))
            self.assertEqual(status, 400)
            self.assertEqual(routing.get_mode(), "auto")

    def test_config_api_masks_invalid_key_env(self):
        from starlette.requests import Request
        cfg = {"lmstudio": {"cloud": {"api_key_env": "sk-private-secret"}}}
        with patch.object(webapp, "load_config", return_value=cfg):
            response = asyncio.run(webapp.get_config(Request({"type": "http"})))
        self.assertNotIn(b"sk-private-secret", response.body)
        self.assertEqual(cfg["lmstudio"]["cloud"]["api_key_env"], "sk-private-secret")
