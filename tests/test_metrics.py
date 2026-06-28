"""metrics 模块 + /metrics 端点测试（G2.2 可观测性）。

验证：
- record_llm_call 累计调用/失败/token/延迟
- record_llm_call_timed 上下文管理器计时 + 异常记 failure
- render_prometheus 输出合法 Prometheus 文本格式
- /metrics 端点可访问

零 LLM，零真实 DB（端点测试用 TestClient）。
"""

from __future__ import annotations

import unittest


class MetricsCountersTests(unittest.TestCase):
    def setUp(self):
        from ming_sim import metrics
        metrics.reset_metrics()

    def test_record_call_accumulates_counters(self):
        from ming_sim import metrics
        metrics.record_llm_call("llm.test", success=True, duration_seconds=0.8,
                                prompt_tokens=100, completion_tokens=50)
        metrics.record_llm_call("llm.test", success=False, duration_seconds=2.0,
                                prompt_tokens=50, completion_tokens=30)
        c = metrics.METRICS["llm.test"]
        self.assertEqual(c["calls"], 2)
        self.assertEqual(c["failures"], 1)
        self.assertEqual(c["prompt_tokens"], 150)
        self.assertEqual(c["completion_tokens"], 80)
        self.assertEqual(c["total_tokens"], 230)
        self.assertEqual(c["latency_count"], 2)

    def test_latency_histogram_buckets_cumulative(self):
        from ming_sim import metrics
        # 0.3s → 落 le=0.5；1.5s → 落 le=2.0；6s → 落 le=10.0
        metrics.record_llm_call("llm.h", success=True, duration_seconds=0.3)
        metrics.record_llm_call("llm.h", success=True, duration_seconds=1.5)
        metrics.record_llm_call("llm.h", success=True, duration_seconds=6.0)
        buckets = metrics.METRICS["llm.h"]["latency_buckets"]
        self.assertEqual(buckets[0.5], 1, "0.3s 应只入 le=0.5")
        # cumulative：le=2.0 应含 0.3s + 1.5s = 2
        self.assertEqual(buckets[2.0], 2, "le=2.0 cumulative 应含 0.3+1.5")
        self.assertEqual(buckets[10.0], 3, "le=10.0 cumulative 应含全部 3")


class MetricsTimerTests(unittest.TestCase):
    def setUp(self):
        from ming_sim import metrics
        metrics.reset_metrics()

    def test_timer_records_success_on_clean_exit(self):
        from ming_sim.metrics import record_llm_call_timed
        with record_llm_call_timed("llm.ok"):
            x = 1 + 1  # noqa: F841
        c = __import__("ming_sim.metrics", fromlist=["METRICS"]).METRICS["llm.ok"]
        self.assertEqual(c["calls"], 1)
        self.assertEqual(c["failures"], 0)

    def test_timer_records_failure_on_exception(self):
        from ming_sim.metrics import record_llm_call_timed, METRICS
        with self.assertRaises(ValueError):
            with record_llm_call_timed("llm.bad"):
                raise ValueError("boom")
        c = METRICS["llm.bad"]
        self.assertEqual(c["calls"], 1)
        self.assertEqual(c["failures"], 1)


class PrometheusRenderTests(unittest.TestCase):
    def setUp(self):
        from ming_sim import metrics
        metrics.reset_metrics()

    def test_render_empty_state(self):
        from ming_sim.metrics import render_prometheus
        out = render_prometheus()
        self.assertIn("ming_sim metrics", out)

    def test_render_populated_state_has_required_lines(self):
        from ming_sim.metrics import render_prometheus, record_llm_call
        record_llm_call("llm.dialogue_post_audit", success=True,
                        duration_seconds=1.2, prompt_tokens=200, completion_tokens=80)
        out = render_prometheus()
        # 必须包含 HELP/TYPE 头 + 指标行
        self.assertIn("# HELP ming_llm_calls_total", out)
        self.assertIn("# TYPE ming_llm_calls_total counter", out)
        self.assertIn('ming_llm_calls_total{pipeline="llm.dialogue_post_audit"} 1', out)
        self.assertIn('ming_llm_tokens_total{pipeline="llm.dialogue_post_audit",kind="total"} 280', out)
        self.assertIn('ming_llm_latency_bucket{pipeline="llm.dialogue_post_audit",le="+Inf"} 1', out)


class MetricsEndpointTests(unittest.TestCase):
    def test_metrics_endpoint_returns_prometheus_text(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ming_sim import metrics
        metrics.reset_metrics()
        metrics.record_llm_call("llm.ep", success=True, duration_seconds=0.4)

        app = FastAPI()

        @app.get("/metrics")
        async def m():
            from ming_sim.metrics import render_prometheus
            from fastapi import Response
            return Response(content=render_prometheus(),
                            media_type="text/plain; version=0.0.4; charset=utf-8")

        client = TestClient(app)
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp.headers.get("content-type", ""))
        self.assertIn("ming_llm_calls_total", resp.text)


if __name__ == "__main__":
    unittest.main()
