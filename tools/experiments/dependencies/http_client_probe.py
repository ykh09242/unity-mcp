"""Exercise the real diagnostic HTTP factory with owned, network-free cookie fixtures.

Run with the server environment and Server/src on PYTHONPATH. Compare complete
JSON receipts across separately installed dependency versions; timings exclude
client construction and cookie population. No Unity or remote endpoint is used.
"""

import asyncio
import hashlib
import importlib.metadata
import json
import statistics
import time

import httpx2
from cli.utils.doctor_connection import DoctorHttpFactory


async def measure(count):
    """Time 200 requests after 20 warmups, checking bodies, cookies and cleanup."""
    body = json.dumps({"result": "owned-fixture", "payload": "x" * 2048}).encode()
    outbound = []

    async def handle(request):
        outbound.append(request.headers.get("cookie", ""))
        assert request.headers["x-probe"] == "owned-fixture"
        return httpx2.Response(
            200,
            headers=[("content-type", "application/json"), ("set-cookie", "updated=value; Path=/")],
            content=body,
        )

    factory = DoctorHttpFactory()
    durations = []
    async with factory(
        headers={"x-probe": "owned-fixture"}, transport=httpx2.MockTransport(handle)
    ) as client:
        for index in range(count):
            client.cookies.set(
                f"cookie{index:04d}", "fixture-value", domain="probe.invalid", path="/"
            )
        for sample in range(220):
            start = time.perf_counter_ns()
            response = await client.post(
                "https://probe.invalid/mcp", json={"method": "resources/read", "id": 1}
            )
            elapsed = (time.perf_counter_ns() - start) / 1e6
            assert response.content == body
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.headers["set-cookie"] == "updated=value; Path=/"
            assert client.cookies.get("updated") == "value"
            assert len(client.cookies) == count + 1
            if sample >= 20:
                durations.append(elapsed)
        assert client.follow_redirects is False
    assert factory.failure is None
    assert len(outbound) == 220
    assert all(value == outbound[-1] for value in outbound[1:])
    assert factory.budget.total == 220 * len(body)
    ordered = sorted(durations)
    return {
        "cookies": count,
        "samples": len(durations),
        "p50_ms": statistics.median(durations),
        "p95_ms": ordered[int(0.95 * len(ordered)) - 1],
        "request_cookie_sha256": hashlib.sha256(outbound[-1].encode()).hexdigest(),
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_bytes": len(body),
        "total_body_bytes": factory.budget.total,
        "closed": client.is_closed,
    }


async def main():
    """Emit installed versions, per-workload timings and semantic digests."""
    print(
        json.dumps(
            {
                "httpx2": importlib.metadata.version("httpx2"),
                "httpcore2": importlib.metadata.version("httpcore2"),
                "workloads": [await measure(count) for count in (0, 256, 2048)],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
