"""进程级可观测性指标（G2.2）：LLM 管道调用计数、token 累计、失败率、延迟直方图。

零新依赖原则：不引 prometheus_client / opentelemetry。手写进程级计数器 +
Prometheus 文本格式 exposition（/metrics 端点直接渲染本模块输出）。

此前 LLM 管道是盲区：token 统计是 stdout 临时 dict、延迟是 tlog 字符串、失败率无聚合。
本模块把三者收口为可查询的进程级状态，/metrics 端点暴露。

线程安全：用 _LOCK 保护写。进程级单例 METRICS。
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List

# 延迟直方图 bucket 边界（秒）。覆盖 LLM 调用的典型区间。
# <0.5s（缓存/快速模型）/ 1s / 2s / 5s / 10s / 30s / +Inf
_LATENCY_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

_LOCK = threading.Lock()


def _new_pipeline_counters() -> Dict[str, object]:
    return {
        "calls": 0,
        "failures": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        # latency histogram: 每个 bucket 存累计计数（Prometheus 风格 cumulative）
        "latency_sum": 0.0,
        "latency_count": 0,
        "latency_buckets": {b: 0 for b in _LATENCY_BUCKETS},  # +Inf 由 count 体现
    }


METRICS: Dict[str, Dict[str, object]] = {}


def record_llm_call(
    pipeline_id: str,
    *,
    success: bool,
    duration_seconds: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """记录一次 LLM 管道调用。在 agents.run_agent_text 等热路径调用。

    pipeline_id 对应 pipeline_registry.PIPELINE_REGISTRY 的 id（如 'llm.dialogue_post_audit'）。
    """
    total = int(prompt_tokens or 0) + int(completion_tokens or 0)
    with _LOCK:
        counters = METRICS.setdefault(pipeline_id, _new_pipeline_counters())
        counters["calls"] = int(counters["calls"]) + 1  # type: ignore[operator]
        if not success:
            counters["failures"] = int(counters["failures"]) + 1  # type: ignore[operator]
        counters["prompt_tokens"] = int(counters["prompt_tokens"]) + int(prompt_tokens or 0)  # type: ignore[operator]
        counters["completion_tokens"] = int(counters["completion_tokens"]) + int(completion_tokens or 0)  # type: ignore[operator]
        counters["total_tokens"] = int(counters["total_tokens"]) + total  # type: ignore[operator]
        # 延迟直方图
        counters["latency_sum"] = float(counters["latency_sum"]) + float(duration_seconds or 0.0)  # type: ignore[operator]
        counters["latency_count"] = int(counters["latency_count"]) + 1  # type: ignore[operator]
        buckets = counters["latency_buckets"]  # type: ignore[index]
        d = float(duration_seconds or 0.0)
        # Prometheus histogram 是 cumulative：le=2.0 含所有 ≤2.0（含更小 bucket 的）。
        # 故对每个 edge：若 d ≤ edge，该 bucket +1（而非只入第一个命中 bucket）。
        for edge in _LATENCY_BUCKETS:
            if d <= edge:
                buckets[edge] = int(buckets[edge]) + 1  # type: ignore[index]


def render_prometheus() -> str:
    """渲染 Prometheus 文本 exposition 格式（/metrics 端点用）。

    零依赖手写：每条指标带 HELP/TYPE 头，值用标准格式。
    """
    with _LOCK:
        # 拷贝快照，避免渲染时被写
        snapshot: List[tuple] = []
        for pid, c in METRICS.items():
            snapshot.append((pid, dict(c)))
    if not snapshot:
        return "# ming_sim metrics: no LLM calls recorded yet\n"

    lines: List[str] = []
    # 调用计数
    lines.append("# HELP ming_llm_calls_total Total LLM pipeline calls by pipeline_id.")
    lines.append("# TYPE ming_llm_calls_total counter")
    for pid, c in snapshot:
        lines.append(f'ming_llm_calls_total{{pipeline="{pid}"}} {c["calls"]}')
    # 失败计数
    lines.append("# HELP ming_llm_failures_total Total LLM pipeline failures by pipeline_id.")
    lines.append("# TYPE ming_llm_failures_total counter")
    for pid, c in snapshot:
        lines.append(f'ming_llm_failures_total{{pipeline="{pid}"}} {c["failures"]}')
    # token 累计
    lines.append("# HELP ming_llm_tokens_total Total tokens by pipeline_id and kind.")
    lines.append("# TYPE ming_llm_tokens_total counter")
    for pid, c in snapshot:
        lines.append(f'ming_llm_tokens_total{{pipeline="{pid}",kind="prompt"}} {c["prompt_tokens"]}')
        lines.append(f'ming_llm_tokens_total{{pipeline="{pid}",kind="completion"}} {c["completion_tokens"]}')
        lines.append(f'ming_llm_tokens_total{{pipeline="{pid}",kind="total"}} {c["total_tokens"]}')
    # 延迟直方图
    lines.append("# HELP ming_llm_duration_seconds LLM pipeline call latency.")
    lines.append("# TYPE ming_llm_duration_seconds summary")
    for pid, c in snapshot:
        cnt = int(c["latency_count"])  # type: ignore[arg-type]
        if cnt > 0:
            avg = float(c["latency_sum"]) / cnt  # type: ignore[arg-type]
            lines.append(f'ming_llm_duration_seconds_avg{{pipeline="{pid}"}} {avg:.4f}')
    lines.append("# HELP ming_llm_latency_bucket LLM call latency histogram (cumulative).")
    lines.append("# TYPE ming_llm_latency_bucket counter")
    for pid, c in snapshot:
        buckets = c["latency_buckets"]  # type: ignore[index]
        for edge in _LATENCY_BUCKETS:
            lines.append(f'ming_llm_latency_bucket{{pipeline="{pid}",le="{edge}"}} {buckets[edge]}')  # type: ignore[index]
        lines.append(f'ming_llm_latency_bucket{{pipeline="{pid}",le="+Inf"}} {c["latency_count"]}')
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    """清空指标（测试用）。"""
    with _LOCK:
        METRICS.clear()


# 上下文管理器：方便在热路径记录一次调用的耗时 + 结果
class _LLMCallTimer:
    """with record_llm_call_timed('llm.foo') as timer: ...; timer.fail()/ok()

    用法：
        with record_llm_call_timed('llm.dialogue_post_audit') as t:
            ...do llm work...
        # 退出时自动记录 success。若块内抛异常，记录 failure 后 re-raise。
    """

    def __init__(self, pipeline_id: str):
        self.pipeline_id = pipeline_id
        self._start = 0.0
        self._success = True

    def __enter__(self) -> "_LLMCallTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.monotonic() - self._start
        success = exc_type is None and self._success
        record_llm_call(self.pipeline_id, success=success, duration_seconds=duration)
        # 不吞异常：返回 False 让其继续传播
        return False

    def fail(self) -> None:
        """显式标记本次调用失败（如解析失败/兜底路径）。"""
        self._success = False


def record_llm_call_timed(pipeline_id: str) -> _LLMCallTimer:
    """上下文管理器：自动计时 + 记录一次 LLM 调用。"""
    return _LLMCallTimer(pipeline_id)
