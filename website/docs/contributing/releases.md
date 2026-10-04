# Releasing (Maintainers)

This fork currently distributes the Unity package and Python server from Git. The `beta` branch is a moving preview, not a stable release. No fork release tag, PyPI package publication, Asset Store listing, OpenUPM registration, pre-built Docker image, or MCPB publication is established by these docs.

## Prepare a pinned Git version

1. Verify the server changes and commit them in the fork.
2. Record the full immutable server commit in `MCPForUnity/package.json` as `mcpServerSource`, using the fork Git URL and `#subdirectory=Server`. Keep the root `manifest.json` server invocation's `--from` argument identical; the metadata contract test enforces this alignment.
3. Verify the Unity package uses that source by default and that `Server/pyproject.toml` names the distribution `ykh09242-unity-mcp-server`.
4. Run the relevant Python, Unity compile, metadata, and documentation checks.
5. Commit the Unity package and documentation changes. Users can replace the preview URL's `#beta` with that Unity-package commit SHA for reproducibility.

A server commit and a Unity-package commit need not be the same: the latter records the former's immutable source.

## Fork release tags

Only describe a tag as a fork release after the maintainer actually creates and verifies it. Preserve original MIT copyright and attribution. If publishing GitHub release notes later, explain the fork's changes and link the upstream baseline as history.

Do not relabel inherited upstream tags or release bodies as fork releases. The [Upstream Release History](/releases) page and `release-metadata.json` intentionally retain CoplayDev history.

## Legacy upstream automation

The inherited release and release-note synchronization workflows are restricted to the upstream repository. They are not a fork publication procedure. Their historical PyPI, Docker, and MCPB steps must not be used as evidence that those fork channels exist.
