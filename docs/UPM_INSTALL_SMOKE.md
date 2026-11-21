# Untouched UPM installation smoke

`unity-upm-install.yml` owns the reusable `untouchedUpmInstall` job, called by
`unity-tests.yml` and exact release qualification. It runs for the `floor` and
`current` roles in `tools/unity-versions.json`. Those roles currently select Unity **2021.3.45f2** and
**6000.6.10f1**. The selector fails if either role is missing or ambiguous; the
workflow does not maintain a second set of Editor pins.

Exact release qualification installs from a read-only local Git source. The job
clones the checked-out commit into a disposable bare repository and gives a fresh
project a single dependency of the form
`file:///.../source.git?path=/MCPForUnity#<full-commit>`. The bare repository
is visible inside the Editor container. Unity resolves the same package subfolder
and revision syntax used in the README, with no credentials or remote Git host.
Ordinary integration runs use the original local folder installation. Each project
starts without dependency overrides, a package lock or a Library cache.
[Unity Git dependency syntax](https://docs.unity3d.com/2021.3/Documentation/Manual/upm-git.html)

The native EditMode fixture checks `PackageSource.Git` and the resolved commit
in release qualification, or the registered local path in integration. In both
cases, it checks the original manifest SHA256, all declared dependency registrations,
and loading the package's Editor assembly and types. Git qualification also checks
the direct entry in Unity's generated `Packages/packages-lock.json` against the
requested URL, subfolder and commit. The result gate requires the exact fixture body to
pass; missing XML, zero tests, skipped tests, failed runner execution and a missing
required body fail the job. Evidence includes the byte-exact original
`source-package.json` and the imported `.upm-imported-package.json`. Some Editors
append a 40-hex `_fingerprint` property to cached Git manifests. When the raw
hash differs, the fixture removes only that trailing generated field and requires
the remaining bytes to match the original SHA256. It also tests a changed version
with a valid fingerprint and requires rejection. This is an installation/import smoke; the existing curated
EditMode, PlayMode, optional integration and compilation matrix remain separate.

The reusable workflow preserves the existing `license` policy and credential gate.
Without enabled licensed execution and available credentials, it reports **Skipped**, which is not native execution
evidence. Neither staging nor license-free compilation certifies the installation.

To prepare a disposable local fixture without starting Unity:

```sh
python tools/unity_upm_smoke.py matrix
python tools/unity_upm_smoke.py prepare --unity-version 2021.3.45f2 --output .unity-ci/upm-floor
python tools/unity_upm_smoke.py prepare --unity-version 2021.3.45f2 --output .unity-ci/upm-git --git-revision "$(git rev-parse HEAD)"
```

Use a new output directory for each preparation. Licensed native execution is
performed by the trusted workflow or an explicitly authorized owned test fixture.

Release qualification requires both floor/current native Git installation results
at the exact receiving commit, in addition to the curated bridge and other evidence.
The reusable contract exports `checked_sha` only after the role selector, license
gate and complete native matrix succeed. Qualification rejects skipped, missing,
failed or cancelled results and a missing or different `checked_sha`.

On licensed fork beta pushes selected for qualification, `fork-beta-tools.yml`
delegates these same two bodies to qualification instead of importing twice.
Other PR, manual and reusable Unity test calls retain their installation smoke.
License-free pushes continue to skip native qualification and claim no native pass.
