# Technical Notes, Capacity Math, Reference Code & Benchmarks

## 1. KV-Cache Capacity Equations & Memory Modeling
For a decoder-only Transformer with $L$ layers, $H_{kv}$ key-value attention heads (GQA), head dimension $d_h$, precision bytes $b$ (2 bytes for FP16/BF16, 1 byte for FP8 KV), and active sequence length $S$:

$$\text{KV Bytes per Token} = 2 \times L \times H_{kv} \times d_h \times b$$

Example (70B-class GQA model: $L = 80$, $H_{kv} = 8$, $d_h = 128$):
- **FP16 KV ($b=2$):** $2 \times 80 \times 8 \times 128 \times 2 = 327{,}680\text{ bytes/token} \approx 320\text{ KiB/token}$.
- At 8,192 tokens per request, one concurrent request requires $8{,}192 \times 320\text{ KiB} = 2.5\text{ GiB}$ of KV cache.
- On 8x80GB H100 (640 GB total VRAM, ~140 GB model weights in FP16 TP=8, ~420 GB usable KV pool after activations/CUDA overhead): maximum theoretical concurrency at 8K context is $\approx 168$ active sequences before preemption/swapping occurs.
- With **FP8 KV quantization ($b=1$)**, per-token footprint halves to **160 KiB/token**, doubling concurrency headroom to **~336 active sequences**.

## 2. Benchmark Data (Controlled Load Test on 8x80GB H100 Cluster, Llama-3-70B-class Workload, Mean Input 1,024 tokens, Output 256 tokens)

### Table 1: Batching Strategy & Quantization Throughput vs. Tail Latency (Concurrency = 64)
| Serving Configuration | Throughput (tokens/s) | p50 TTFT (ms) | p95 TTFT (ms) | p99 TTFT (ms) | Mean TPOT (ms) | Peak KV Utilization (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Static Batching (FP16, max_batch=16) | 1,420 | 410 | 1,890 | 3,420 | 58.4 | 41.2 |
| Continuous Batching (FP16, Paged) | 3,860 | 185 | 490 | 820 | 31.2 | 88.5 |
| Continuous Batching + Chunked Prefill (FP16) | 4,110 | 162 | 340 | 540 | 26.8 | 91.0 |
| Continuous Batching + Chunked Prefill (FP8) | 6,480 | 118 | 245 | 390 | 18.9 | 89.4 |
| Continuous Batching + AWQ INT4 Weights | 5,920 | 132 | 290 | 465 | 21.4 | 86.7 |

### Table 2: Hybrid RAG + Semantic Cache End-to-End Gateway Latency Breakdown (500 req/s Steady State)
| Pipeline Mode | Cache Hit Rate (%) | p50 E2E TTFT (ms) | p99 E2E TTFT (ms) | Cost per 1M Output Tokens ($) |
| :--- | :---: | :---: | :---: | :---: |
| Direct Vector-Only RAG (No Cache) | 0.0 | 312 | 940 | 1.85 |
| Hybrid BM25 + Dense + RRF (No Cache) | 0.0 | 348 | 880 | 1.85 |
| Hybrid RAG + Redis Semantic Cache (cos >= 0.94) | 28.5 | 215 | 790 | 1.34 |
| Hybrid RAG + Tiered Cache + Adaptive Shedding | 31.2 | 198 | 510 | 1.28 |

## 3. Reference Implementation Specification: Bounded Admission Controller & Circuit Breaker
Key invariants required in production Python `asyncio` gateways:
1. Never leave `asyncio.Queue` unbounded (`maxsize` must reflect downstream GPU KV-block capacity).
2. Enforce explicit per-request deadline propagation (`asyncio.wait_for`) so stale client connections do not consume GPU decode cycles after client disconnect.
3. Implement a token-bucket rate limiter paired with a half-open circuit breaker on downstream retrieval stores to prevent retry storms.

```python
import asyncio
import time
from dataclasses import dataclass


@dataclass(slots=True)
class AdmissionConfig:
    max_queue_depth: int = 128
    max_wait_ms: int = 250
    tokens_per_sec: float = 50000.0
    bucket_capacity: float = 100000.0


class BoundedTokenAdmissionController:
    def __init__(self, config: AdmissionConfig) -> None:
        self._cfg = config
        self._queue: asyncio.Queue[tuple[float, asyncio.Future[bool]]] = asyncio.Queue(
            maxsize=config.max_queue_depth
        )
        self._tokens = config.bucket_capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._last_refill)
            self._tokens = min(
                self._cfg.bucket_capacity,
                self._tokens + elapsed * self._cfg.tokens_per_sec,
            )
            self._last_refill = now
            if self._tokens >= estimated_tokens and self._queue.empty():
                self._tokens -= estimated_tokens
                return True

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[bool] = loop.create_future()
        try:
            self._queue.put_nowait((time.monotonic(), waiter))
        except asyncio.QueueFull:
            return False

        try:
            return await asyncio.wait_for(
                waiter, timeout=self._cfg.max_wait_ms / 1000.0
            )
        except asyncio.TimeoutError:
            return False
```

## 4. Production Incident Case Studies (Post-Mortem Notes)
- **Incident PM-104 (Retry Storm & Cascading OOM):** A 40 ms network blip on the vector database triggered exponential-without-jitter retries across 40 gateway pods, tripling concurrent prefill payloads and pushing GPU KV cache to 100%, triggering pod OOM-kills and a 14-minute outage. Fix: circuit breaker + jittered single hedged retry + strict admission budget.
- **Incident PM-118 (Multi-Agent Infinite Tool Loop):** An unguarded tool-calling agent retried malformed SQL queries 38 times in a single session, exhausting 128K context capacity and blocking decode slots for 19 seconds. Fix: hard hop limit (`max_steps=8`), per-session token budget cap, and deterministic schema validator before tool dispatch.
