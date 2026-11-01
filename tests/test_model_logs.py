"""日志应区分选择的策略和真正调用的供应商，且不能泄漏请求内容。"""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from studio import cli, llm, pipeline
from tests.test_llm import response, MESSAGES, IMAGE_MESSAGES, SCHEMA


class ModelLogTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"url": "http://localhost:1234/v1", "model": "local-text",
                    "cloud": {"enabled": True, "url": "https://cloud.example/v1", "model": "cloud-text",
                              "vision_model": "cloud-vision", "api_key_env": "LOG_TEST_KEY"}}
        self.env = patch.dict(os.environ, {"LOG_TEST_KEY": "secret-log-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_success_logs_local_model_and_completion(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen", return_value=response()):
            self.assertEqual(llm.chat(self.cfg, MESSAGES, model="local-override"), "ok")
        logs = output.getvalue()
        self.assertIn("模型调用开始", logs)
        self.assertIn("本地 LM Studio", logs)
        self.assertIn("local-override", logs)
        self.assertIn("模型调用成功", logs)
        self.assertIn("耗时", logs)
        self.assertNotIn("云端", logs)

    def test_fallback_logs_actual_order_and_vision_model(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen",
                side_effect=[TimeoutError(), response('{"score": 2}')]):
            self.assertEqual(llm.chat(self.cfg, IMAGE_MESSAGES, SCHEMA, model="local-vision"), {"score": 2})
        logs = output.getvalue()
        self.assertIn("看图质检", logs)
        self.assertIn("local-vision", logs)
        self.assertIn("cloud-vision", logs)
        self.assertLess(logs.index("模型调用失败"), logs.index("切换云端兜底"))
        self.assertLess(logs.index("切换云端兜底"), logs.index("cloud-vision"))
        self.assertIn("模型调用成功", logs)
        self.assertNotIn("secret-log-key", logs)
        self.assertNotIn("base64", logs)

    def test_cloud_only_logs_cloud_without_claiming_local_request(self):
        self.cfg["routing_mode"] = "cloud"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen", return_value=response()):
            llm.chat(self.cfg, MESSAGES)
        logs = output.getvalue()
        self.assertIn("云端", logs)
        self.assertIn("cloud-text", logs)
        self.assertNotIn("本地", logs)

    def test_invalid_output_is_logged_as_failure_not_success(self):
        self.cfg["routing_mode"] = "local"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen", return_value=response('{}')):
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES, SCHEMA)
        self.assertIn("模型调用失败", output.getvalue())
        self.assertNotIn("模型调用成功", output.getvalue())

    def test_unconfigured_cloud_logs_not_called(self):
        self.cfg["routing_mode"] = "cloud"
        self.cfg["cloud"]["model"] = ""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen") as http:
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES)
        self.assertIn("模型未调用", output.getvalue())
        self.assertNotIn("模型调用开始", output.getvalue())
        self.assertEqual(http.call_count, 0)

    def test_disabled_cloud_only_does_not_claim_local_usage(self):
        self.cfg["routing_mode"] = "cloud"
        self.cfg["cloud"]["enabled"] = False
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(llm.urllib.request, "urlopen") as http:
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, MESSAGES)
        self.assertIn("模型未调用", output.getvalue())
        self.assertNotIn("本地", output.getvalue())
        self.assertEqual(http.call_count, 0)

    def test_job_start_explains_mode_and_separate_image_engine(self):
        output = io.StringIO()
        cfg = {"lmstudio": self.cfg}
        with contextlib.redirect_stdout(output), patch.object(cli, "load_config", return_value=cfg), patch.object(
                cli, "cmd_run", return_value=0):
            self.assertEqual(cli.main(["--llm-mode", "auto", "run", "test.json", "--only", "images"]), 0)
        logs = output.getvalue()
        self.assertIn("模型路由", logs)
        self.assertIn("本地优先", logs)
        self.assertIn("云端兜底", logs)
        self.assertIn("ComfyUI", logs)

    def test_invalid_policy_still_uses_readable_cli_error_handling(self):
        self.cfg["routing_mode"] = "invalid"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch.object(
                cli, "load_config", return_value={"lmstudio": self.cfg}), patch.object(
                cli, "cmd_plan", side_effect=llm.LLMError("未知模型模式")):
            self.assertEqual(cli.main(["plan", "test.txt"]), 1)

    def test_image_stage_reports_engine_and_profile_before_generation(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(pipeline, "ComfyClient") as client, patch(
                "studio.comfy.make_editor", return_value=None), patch("studio.qa.produce_images"):
            client.return_value.url = "http://127.0.0.1:8188"
            client.return_value.profile = "t2i_klein"
            pipeline._images({}, None, {"comfy": {}}, False, None, False)
        logs = output.getvalue()
        self.assertIn("本地 ComfyUI", logs)
        self.assertIn("t2i_klein", logs)
