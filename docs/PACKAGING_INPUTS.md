# Packaging input pins

The Docker image and MCPB generator retain these reviewed top-level inputs:

| Input | Pin | Evidence verified on 2026-11-04 |
| --- | --- | --- |
| Python base image | `python:3.14-slim@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966` | Official Docker Hub tag metadata and byte-hashed OCI index response agree. |
| Docker uv installer | `uv==0.13.5` | Exact PyPI release exists and inspected Linux wheels are not yanked; matches the CI setup-uv pin. |
| MCPB executable | `@anthropic-ai/mcpb@2.1.2` | Exact npm release metadata and commit-pinned upstream CLI support `pack [directory] [output]`. |

The Python digest selects a multiarchitecture OCI index, preserving platform
selection. Its Linux amd64 child digest is
`sha256:ec6a14df5b63e3fbb3498e3240f7f640c230f9dca67683d23265db3866697f2d`;
its Linux arm64/v8 child is
`sha256:29434a59c93070e470f84663e4289026e04644ea44b5220ef7e07d653318ca1f`.
The base remains Python 3.14 slim; application dependency requirements are unchanged.

Sources: [official Python tag metadata](https://hub.docker.com/v2/repositories/library/python/tags/3.14-slim),
[official Python Dockerfile](https://github.com/docker-library/python/blob/01dcc3d31fcd879f9af36e097bd96328a457f4bd/3.14/slim-trixie/Dockerfile),
[uv 0.13.5 release metadata](https://pypi.org/pypi/uv/0.13.5/json),
[uv Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/),
[MCPB 2.1.2 metadata](https://registry.npmjs.org/@anthropic-ai%2fmcpb/2.1.2),
and [MCPB CLI at the release commit](https://github.com/anthropics/mcpb/blob/2a788100a60db19a6b1c018fb1cf84ae85de9537/src/cli/cli.ts).

`generate_mcpb.py` uses `npx --yes` with the exact MCPB version, avoiding a
floating package selection or interactive installation prompt. The existing
Windows command-shim handling, staging and atomic final replacement are retained.
Validation executed uv 0.13.5 and MCPB 2.1.2, and generated a local owned bundle on
Windows with Node 26.13.0. CI's pinned Node 26.12.1 was not executed locally. No
container build or image-layer pull was performed for this change.

These pins prevent top-level drift, but do not promise reproducible artifact bytes.
The Dockerfile still consumes apt repositories and unpinned apt package versions;
MCPB's transitive npm dependencies retain upstream ranges. Registries must remain
available. Upgrade pins deliberately, recheck official metadata and CLI contracts,
run packaging regression tests, and keep these dates and validation limits current.
