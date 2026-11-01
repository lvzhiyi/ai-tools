import io
import os
import unittest
from unittest.mock import patch

from studio import llm
from studio.qa import Inspector


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"comfy": {}, "lmstudio": {"url": "http://localhost:1234/v1", "model": "local",
                    "cloud": {"enabled": True, "url": "https://cloud.example/v1", "model": "text",
                              "vision_model": "vision", "api_key_env": "STUDIO_TEST_KEY"}}}

    def test_inspector_keeps_vision_if_only_cloud_is_ready(self):
        with patch.dict(os.environ, {"STUDIO_TEST_KEY": "fake-key"}), patch.object(
                llm.urllib.request, "urlopen", side_effect=TimeoutError()):
            inspector = Inspector(self.cfg, {}, {}, None, use_vision=True)
            self.assertTrue(inspector.use_vision)
            self.cfg["lmstudio"]["cloud"]["vision_model"] = ""
            inspector = Inspector(self.cfg, {}, {}, None, use_vision=True)
            self.assertFalse(inspector.use_vision)

    def test_environment_check_reports_cloud_configuration_without_cloud_request(self):
        from studio.checks import run
        from studio.comfy import ComfyError
        requested = []
        def offline(req, **kwargs):
            requested.append(req)
            raise TimeoutError()
        with patch.dict(os.environ, {"STUDIO_TEST_KEY": "fake-key"}), patch.object(
                llm.urllib.request, "urlopen", side_effect=offline), patch(
                "studio.comfy.ComfyClient", side_effect=ComfyError("offline")), patch(
                "studio.face.FaceMatcher"), patch("studio.video.has_ffmpeg", return_value=True), patch(
                "studio.i2v.make_engine") as engine:
            engine.return_value.check.return_value = []
            items = run(self.cfg)
        cloud = next(item for item in items if item["name"] == "云端模型兜底")
        self.assertEqual(cloud["status"], "ok")
        self.assertTrue(all("cloud.example" not in str(req) for req in requested))


if __name__ == "__main__":
    unittest.main()
