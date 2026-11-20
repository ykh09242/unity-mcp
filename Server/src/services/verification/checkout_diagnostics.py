"""Classify exact-byte divergence from reviewed Git inputs."""

from collections.abc import Iterable, Iterator
from hashlib import sha256
from pathlib import Path
import re
from typing import Literal, TypedDict

from services.verification.checkout_paths import CheckoutPathAlias, CheckoutPaths
from services.verification.git_draft import _BLOB_LIMIT, _SOURCE_BYTES
from services.verification.input_chunks import SourceSample


_LFS_POINTER = re.compile(
    rb"version https://git-lfs\.github\.com/spec/v1\n"
    rb"(?:ext-[0-9]+-[^\n]+\n)*oid sha256:(?P<oid>[a-f0-9]{64})\nsize [0-9]+\n?"
)

_Kind = Literal[
    "line_endings",
    "unhydrated_lfs_pointer",
    "missing",
    "path_alias",
    "content_mismatch",
    "committed_mismatch",
]

_HINTS: dict[_Kind, str] = {
    "line_endings": (
        "Check .gitattributes and core.autocrlf. Use a fresh checkout with core.autocrlf=false "
        "and the same byte-preserving attributes as the build producer."
    ),
    "unhydrated_lfs_pointer": (
        "Provision the reviewed LFS object through your trusted Git LFS workflow, then verify "
        "the proof was made from the exact required bytes."
    ),
    "missing": "Restore the reviewed input in a complete checkout and retry.",
    "path_alias": (
        "Use a checkout whose filesystem preserves the reviewed path spelling and can "
        "represent every selected path separately."
    ),
    "content_mismatch": "Compare the reported hashes with the reviewed build input and checkout filters.",
    "committed_mismatch": "Verify the proof against the recorded commit and its exact Git blob bytes.",
}


class CheckoutFinding(TypedDict):
    path: str
    kind: _Kind
    expected_sha256: str
    committed_sha256: str | None
    checkout_sha256: str | None
    hint: str


class CheckoutDivergence(ValueError):
    def __init__(self, findings: list[CheckoutFinding]):
        self.findings = findings
        first = findings[0]
        super().__init__(
            f"reproduction_checked_out_inputs_mismatch: {first['path']} "
            f"({first['kind']}). {first['hint']}"
        )


class CheckoutInputs:
    """Keep the per-side byte budget and findings while Git streams the committed blobs."""

    def __init__(self, project: Path, expected: dict[str, str], sample: SourceSample):
        self.paths = CheckoutPaths(project, expected)
        self.expected = expected
        self.sample = sample
        self.remaining = _SOURCE_BYTES
        self.findings: list[CheckoutFinding] = []

    def observe(self, path: str, committed: bytes | None) -> None:
        alias = False
        try:
            physical = self.paths.resolve(path)
            working = self.sample.read(physical, min(_BLOB_LIMIT, self.remaining))
        except CheckoutPathAlias:
            alias, working = True, None
        except FileNotFoundError:
            working = None
        if working is not None:
            self.remaining -= len(working)
        expected = self.expected[path]
        committed_digest = sha256(committed).hexdigest() if committed is not None else None
        working_digest = sha256(working).hexdigest() if working is not None else None
        kind: _Kind
        if alias:
            kind = "path_alias"
        elif (
            working is not None
            and committed is not None
            and len(working) <= 1024
            and len(committed) <= 1024
            and (pointer := _LFS_POINTER.fullmatch(committed.replace(b"\r\n", b"\n")))
            and pointer["oid"].decode("ascii") == expected
            and working.replace(b"\r\n", b"\n") == pointer[0]
        ):
            kind = "unhydrated_lfs_pointer"
        elif committed_digest != expected:
            kind = "committed_mismatch"
        elif working is None:
            kind = "missing"
        elif working_digest == expected:
            return
        elif (
            committed is not None
            and b"\0" not in working
            and working.replace(b"\r\n", b"\n") == committed.replace(b"\r\n", b"\n")
        ):
            kind = "line_endings"
        else:
            kind = "content_mismatch"
        self.findings.append(
            {
                "path": path,
                "kind": kind,
                "expected_sha256": expected,
                "committed_sha256": committed_digest,
                "checkout_sha256": working_digest,
                "hint": _HINTS[kind],
            }
        )

    def check(self) -> None:
        self.paths.finish()
        if self.findings:
            raise CheckoutDivergence(sorted(self.findings, key=lambda finding: finding["path"]))

    def compare(self, files: Iterable[tuple[str, bytes]]) -> Iterator[tuple[str, bytes]]:
        seen = set()
        for path, data in files:
            seen.add(path)
            self.observe(path, data)
            yield path, data
        for path in sorted(self.expected.keys() - seen):
            self.observe(path, None)
