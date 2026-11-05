"""MCP Server 的单元测试：用 httpx.MockTransport 假装 studio web，不需要真的开服务。

运行：uv run python -m unittest tests.test_mcp_server -v
"""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from studio import mcp_server as m


def _client_for(handler):
    return lambda: httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))


class ApiTest(unittest.TestCase):
    def test_error_message_passthrough(self):
        h = lambda req: httpx.Response(404, json={"error": "分镜表不存在：x.json"})  # noqa: E731
        with patch.object(m, "_client", _client_for(h)):
            with self.assertRaisesRegex(m.StudioError, "分镜表不存在"):
                m.get_storyboard("x.json")

    def test_connect_error_hint(self):
        def h(req):
            raise httpx.ConnectError("refused")
        with patch.object(m, "_client", _client_for(h)):
            with self.assertRaisesRegex(m.StudioError, "studio web"):
                m.check_env()

    def test_start_job_posts_whitelisted_body(self):
        seen = {}

        def h(req):
            if req.method == "POST":
                seen["body"] = req.read().decode()
                return httpx.Response(201, json={"id": "j1", "title": "t", "status": "queued", "created": 10})
            return httpx.Response(200, json=[{"id": "j1", "status": "queued", "created": 10}])
        with patch.object(m, "_client", _client_for(h)):
            v = m.start_job("run", "example.json", {"only": "images"})
        self.assertEqual(v["id"], "j1")
        self.assertEqual(v["queued_ahead"], 0)
        self.assertIn('"only":"images"', seen["body"].replace(" ", ""))

    def test_queued_ahead_counts_running_and_earlier(self):
        jobs = [{"id": "new", "status": "queued", "created": 30},
                {"id": "b", "status": "queued", "created": 20},
                {"id": "a", "status": "running", "created": 10},
                {"id": "old", "status": "succeeded", "created": 5}]

        def h(req):
            if req.method == "POST":
                return httpx.Response(201, json=jobs[0])
            return httpx.Response(200, json=jobs)
        with patch.object(m, "_client", _client_for(h)):
            self.assertEqual(m.start_job("qa", "example.json")["queued_ahead"], 2)

    def test_queued_ahead_never_negative_when_started_immediately(self):
        def h(req):
            if req.method == "POST":
                return httpx.Response(201, json={"id": "j1", "status": "queued", "created": 10})
            return httpx.Response(200, json=[{"id": "j1", "status": "running", "created": 10}])
        with patch.object(m, "_client", _client_for(h)):
            self.assertEqual(m.start_job("qa", "example.json")["queued_ahead"], 0)

    def test_save_storyboard_wraps_data(self):
        seen = []

        def h(req):
            body = json.loads(req.read() or b"{}")
            seen.append((req.method, req.url.path, body))
            if req.method == "PUT":
                return httpx.Response(200, json={"file": "x.json", "data": body["data"]})
            return httpx.Response(201, json={"file": "x.json"})
        data = {"title": "新剧", "characters": {}, "shots": [{"id": 1, "image": "a"}]}
        with patch.object(m, "_client", _client_for(h)):
            r = m.save_storyboard("x.json", data, create=True)
        self.assertEqual(r, {"file": "x.json", "shots": 1})
        self.assertEqual(seen[0][:2], ("POST", "/api/storyboards"))
        self.assertEqual(seen[1], ("PUT", "/api/storyboards/x.json", {"data": data}))


class JobViewTest(unittest.TestCase):
    def test_trims_log_and_computes_elapsed(self):
        j = {"id": "1", "status": "running", "lines": [str(i) for i in range(100)], "started": 0, "finished": 5}
        v = m._job_view(j, tail=3)
        self.assertEqual(v["log_tail"], ["97", "98", "99"])
        self.assertEqual(v["elapsed_sec"], 5)


class WaitJobTest(unittest.TestCase):
    def test_returns_when_done_and_reports_progress(self):
        states = iter(["running", "succeeded"])
        h = lambda req: httpx.Response(200, json={"id": "1", "status": next(states),  # noqa: E731
                                                 "progress": [1, 2], "lines": ["ok"]})

        class Ctx:
            calls = []

            async def report_progress(self, *a, **k):
                self.calls.append((a, k))

        async def no_sleep(_):
            return None

        ctx = Ctx()
        with patch.object(m, "_client", _client_for(h)), patch.object(m, "_sleep", no_sleep):
            v = asyncio.run(m.wait_job("1", ctx))  # @mcp.tool 返回原函数，可以直接调
        self.assertEqual(v["status"], "succeeded")
        self.assertEqual(len(ctx.calls), 1)


class RegistrationTest(unittest.TestCase):
    def test_all_tools_registered(self):
        names = {t.name for t in asyncio.run(m.mcp.list_tools())}
        self.assertTrue({"check_env", "start_job", "wait_job", "view_shot_image", "redo_shot"} <= names)


if __name__ == "__main__":
    unittest.main()
