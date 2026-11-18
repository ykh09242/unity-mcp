"""Own one native log subscription for the lifetime of the test stage."""

import re

from services.verification.reports import console_delta


class ConsoleObservation:
    def __init__(self, native, until):
        self.native = native
        self.until = until
        self.before = {}
        self.window_id = None
        self.release_needed = False
        self.tail_options = {
            "types": ["error"],
            "format": "json",
            "pageSize": 200,
            "includeStacktrace": False,
            "includeMcpLogs": False,
        }

    async def request(self, options):
        try:
            response = await self.native("tail_console", options, self.until("console"))
        except (ConnectionError, TimeoutError):
            return {}
        data = response.get("data")
        return data if response.get("success") is True and isinstance(data, dict) else {}

    async def __aenter__(self):
        # Older peers ignore the optional extension and return an ordinary cursor.
        self.before = await self.request(
            {**self.tail_options, "tail_cursor": "begin", "verification_window": "begin"}
        )
        window = self.before.get("window_id")
        if isinstance(window, str) and re.fullmatch("[a-f0-9]{32}", window):
            self.window_id = window
            self.release_needed = True
        return self

    async def __aexit__(self, *_):
        if self.release_needed:
            try:
                await self.request({"verification_window": "discard", "window_id": self.window_id})
            except (PermissionError, ValueError, RuntimeError):
                # Revocation can also prevent cleanup. The native lease expires independently.
                pass

    async def read(self, job_id):
        if self.window_id is None:
            after = {}
            if cursor := self.before.get("next_cursor"):
                after = await self.request({**self.tail_options, "tail_cursor": cursor})
            evidence = console_delta(self.before, after)
            if evidence["state"] == "passed":
                evidence.update(
                    state="incomplete", coverage="unknown", reason="job_console_proof_unavailable"
                )
            return evidence

        after = await self.request(
            {"verification_window": "read", "window_id": self.window_id, "job_id": job_id}
        )
        generation = self.before.get("generation")
        unknown = {
            "state": "incomplete",
            "coverage": "unknown",
            "reason": "job_console_proof_unavailable",
            "scope": "job_application_log_callbacks",
            "job_id": job_id,
        }
        if (
            not isinstance(generation, str)
            or not re.fullmatch("[a-f0-9]{32}", generation)
            or after.get("generation") != generation
            or after.get("window_id") != self.window_id
            or after.get("job_id") != job_id
        ):
            return unknown
        count, findings = after.get("observed_error_count"), after.get("findings")
        if (
            type(count) is not int
            or not 0 <= count <= 2**31 - 1
            or not isinstance(findings, list)
            or len(findings) != min(count, 12)
            or type(after.get("messages_truncated")) is not bool
            or any(
                not isinstance(row, dict)
                or row.get("type") not in ("Error", "Assert", "Exception")
                or not isinstance(row.get("message"), str)
                or len(row["message"]) > 256
                for row in findings
            )
        ):
            return unknown
        settled = after.get("end") == "settled"
        truncated = count > len(findings)
        complete = settled and not truncated
        self.release_needed = not settled
        return {
            **unknown,
            "state": "failed" if count else "passed" if complete else "incomplete",
            "coverage": "complete" if complete else "unknown",
            "reason": "console_findings_overflow"
            if truncated
            else None
            if complete
            else "console_interval_interrupted",
            "observed_error_count": count,
            "findings": [{"type": row["type"], "message": row["message"]} for row in findings],
            "findings_truncated": truncated,
            "messages_truncated": after["messages_truncated"],
            "generation": generation,
        }
