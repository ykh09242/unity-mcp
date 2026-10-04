# Releasing (Maintainers)

This fork distributes the Unity package and Python server from Git. Publish fork releases through [GitHub Releases](https://github.com/ykh09242/unity-mcp/releases). The `beta` branch is the moving source branch; the version suffix and GitHub prerelease flag determine a release's channel independently of the branch name. A GitHub release is separate from documentation deployment and does not imply PyPI, Asset Store, OpenUPM, pre-built Docker image, or MCPB publication.

## Fork versioning

The fork owns its SemVer series independently of the original project's `10.3.x` versions. Unity-package and Python-server versions describe their respective fork components and can evolve independently; the Unity package's immutable `mcpServerSource` selects the matching server commit.

The first stable fork release is tagged `ykh09242-v1.0.0`, with both Unity and Python component versions initially `1.0.0`. Fork tags use the `ykh09242-v` prefix to avoid collisions with inherited upstream tags such as `v1.0.0`; never replace those historical tags. Publish the fork tag as a stable GitHub release rather than a prerelease after the release checks pass. Later component versions can be maintained independently.

Record the original project's version and commit as the upstream baseline, not as the fork's release version. A `-beta.N` suffix identifies a preview version, which should also be marked as a prerelease on GitHub. Do not imply a stable release until the maintainer selects and publishes one.

## Update component versions

Run the version helper from the repository root, replacing the version placeholders with the desired versions independently:

```bash
python tools/update_versions.py --component unity --version X.Y.Z
python tools/update_versions.py --component server --version A.B.C
```

`unity` is the default component and updates `MCPForUnity/package.json` and the root `manifest.json`. `server` requires an explicit `--version` and updates only `Server/pyproject.toml` and its own distribution entry in `Server/uv.lock`. Neither mode changes the immutable server pin or couples the component versions.

After a server version change, test and commit the server files, then manually repin the Unity package and root manifest to that verified server commit using the procedure below. `--component all` is the legacy synchronized upstream mode, explicitly used by the upstream release workflow; use the independent modes for this fork.

## Prepare a pinned Git version

1. Verify the server changes and commit them in the fork.
2. Record the full immutable server commit in `MCPForUnity/package.json` as `mcpServerSource`, using the fork Git URL and `#subdirectory=Server`. Keep the root `manifest.json` server invocation's `--from` argument identical; the metadata contract test enforces this alignment.
3. Verify the Unity package uses that source by default and that `Server/pyproject.toml` names the distribution `ykh09242-unity-mcp-server`.
4. Run the relevant Python, Unity compile, metadata, and documentation checks.
5. Commit the Unity package and documentation changes. Users can replace the preview URL's `#beta` with that Unity-package commit SHA for reproducibility.

A server commit and a Unity-package commit need not be the same: the latter records the former's immutable source.

## Publish a fork GitHub release

1. Select the fork version and whether it is a prerelease or stable release. Complete the relevant checks for the prepared Git version.
2. Create a fork release tag on the verified commit, then publish its GitHub release with the selected prerelease state and any explicitly prepared assets. Publication is a manual maintainer operation; it does not use the upstream release workflow.
3. Explain the fork changes, identify the Unity and Python component versions and immutable server commit, and credit the original MIT authorship. Link the upstream version and commit as the baseline.
4. Verify the published tag, target commit, release notes, prerelease state, and attached assets. Users can pin the Unity Git installation URL to the verified fork tag or commit.

Only tags and releases actually created for this fork belong on [Fork Releases](https://github.com/ykh09242/unity-mcp/releases). Do not relabel inherited upstream tags or release bodies as fork releases. The [Upstream Release History](/releases) page and `release-metadata.json` intentionally retain CoplayDev history.

## Legacy upstream automation

The inherited release and release-note synchronization workflows are restricted to the upstream repository. They are not a fork publication procedure. Their historical PyPI, Docker, and MCPB steps must not be used as evidence that those fork channels exist.
