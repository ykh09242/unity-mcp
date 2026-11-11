# Packaging input pins

The Docker image and MCPB generator retain these reviewed top-level inputs:

| Input | Pin | Evidence |
| --- | --- | --- |
| Python base image | `python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f` | Official Docker Hub tag metadata matched the byte-hashed OCI index on 2026-11-11. |
| Docker uv installer | `uv==0.13.7` | PyPI listed ten available Linux wheels on 2026-11-05. CI selects the same version. |
| MCPB executable | `@anthropic-ai/mcpb@2.1.2` | Exact npm release metadata and the upstream CLI supported `pack [directory] [output]` on 2026-11-04. |

The Python digest selects a multiarchitecture OCI index, preserving platform
selection. Its Linux amd64 child digest is
`sha256:ac25e5afbfdb4a94ff99cc24b0345719210133144481480bcba6085efa36a341`;
its Linux arm64/v8 child is
`sha256:973d41424c3fd81029ac9230bcdec2580fb67b49af9004279001c301182ae868`.
Both child manifests and configuration digests were byte-hashed and declare Python
3.14.10. This verifies registry metadata; Docker execution was not performed.
The server's Python dependencies are declared separately in `Server/pyproject.toml`.

Sources: [official Python tag metadata](https://hub.docker.com/v2/repositories/library/python/tags/3.14-slim),
[official Python Dockerfile](https://github.com/docker-library/python/blob/01dcc3d31fcd879f9af36e097bd96328a457f4bd/3.14/slim-trixie/Dockerfile),
[uv 0.13.7 release metadata](https://pypi.org/pypi/uv/0.13.7/json),
[uv Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/),
[MCPB 2.1.2 metadata](https://registry.npmjs.org/@anthropic-ai%2fmcpb/2.1.2),
and [MCPB CLI at the release commit](https://github.com/anthropics/mcpb/blob/2a788100a60db19a6b1c018fb1cf84ae85de9537/src/cli/cli.ts).

`generate_mcpb.py` uses `npx --yes` with the exact MCPB version, avoiding a
floating package selection or interactive installation prompt. The existing
Windows command-shim handling, staging and atomic final replacement are retained.
On Windows, uv 0.13.7 produced the server wheel and source distribution. The
MCPB 2.1.2 bundle was previously generated with Node 26.13.0, now also selected
by CI. Docker base verification covers the registry index and platform digests.

These pins prevent top-level drift, but do not promise reproducible artifact bytes.
The Dockerfile still consumes apt repositories and unpinned apt package versions;
MCPB's transitive npm dependencies retain upstream ranges. Registries must remain
available. Upgrade pins deliberately, recheck official metadata and CLI contracts,
run packaging regression tests, and keep these dates and validation limits current.
