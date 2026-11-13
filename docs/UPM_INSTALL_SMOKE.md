# Untouched UPM installation smoke

`unity-upm-install.yml` owns the reusable `untouchedUpmInstall` job, called by
`unity-tests.yml` and exact release qualification. It runs for the `floor` and
`current` roles in `tools/unity-versions.json`. Those roles currently select Unity **2021.3.45f2** and
**6000.6.4f1**. The selector fails if either role is missing or ambiguous; the
workflow does not maintain a second set of Editor pins.

The job creates a fresh project and copies `MCPForUnity` without changing any
`package.json` bytes. The project manifest contains only the local package
reference. It supplies no direct dependency overrides, embedded dependency
closure, copied test-project settings, existing package lock or Library cache.
Unity Package Manager must import the package and resolve its declared
dependencies itself. Local folder references follow Unity's
[UPM path rules](https://docs.unity.com/en-us/engine/6000.6/manual/packages-list/dependencies-lp/upm-localpath).

The native EditMode fixture checks the registered local package path, the original
manifest SHA256, all declared dependency registrations, and loading the package's
Editor assembly and types. The result gate requires the exact fixture body to
pass; missing XML, zero tests, skipped tests, failed runner execution and a missing
required body fail the job. Reservation/observation records and package manifests
are uploaded for review. This is an installation/import smoke; the existing curated
EditMode, PlayMode, optional integration and compilation matrix remain separate.

The reusable workflow preserves the existing `license` policy and credential gate.
Without enabled licensed execution and available credentials, it reports **Skipped**, which is not native execution
evidence. Neither staging nor license-free compilation certifies the installation.

To prepare a disposable local fixture without starting Unity:

```sh
python tools/unity_upm_smoke.py matrix
python tools/unity_upm_smoke.py prepare --unity-version 2021.3.45f2 --output .unity-ci/upm-floor
```

Use a new output directory for each preparation. Licensed native execution is
performed by the trusted workflow or an explicitly authorized owned test fixture.

Manual release qualification requires both floor/current native installation results
at the exact receiving commit, in addition to the curated bridge and other evidence.
The reusable contract exports `checked_sha` only after the role selector, license
gate and complete native matrix succeed. Qualification rejects skipped, missing,
failed or cancelled results and a missing or different `checked_sha`.

On licensed fork beta pushes selected for qualification, `fork-beta-tools.yml`
delegates these same two bodies to qualification instead of importing twice.
Other PR, manual and reusable Unity test calls retain their installation smoke.
License-free pushes continue to skip native qualification and claim no native pass.
