# Verify the same Player inputs in another checkout

Start with a local registration under the operator's absolute `UNITY_MCP_VERIFICATION_ROOT`. Export from the checkout and build that produced the reviewed candidate.

```sh
unity-mcp --no-project-context verification-candidate export --project . --candidate-id reviewed --output transfer/proof.json
```

The exporter reads the registered source files, checks their exact SHA-256 digests and verifies the inventory of the locally installed v2 Player bundle. It writes a new proof containing the project-relative registration data and the digest of the exact bundle manifest. A chunked registration also writes its digest-named `input-chunks/` files beside the proof. The JSON receipt includes `proof_sha256`. Retain that digest in a trusted review record. Review project-authored observation values before sharing them.

Transfer the proof directory and make the selected source files available in the destination checkout. Use the expected digest from the review record in each command.

```sh
unity-mcp --no-project-context verification-candidate verify --project . --proof transfer/proof.json --sha256 <reviewed-sha256>
```

Verification reads the live project-relative inputs under the explicitly selected root. A missing or mismatched file identifies its local relative path. A matching checkout produces a JSON receipt with `registered=false`. This step needs only the proof, its chunks where applicable, and the selected project.

To admit a Player on this host, have the local operator independently provision a trusted bundle under `<UNITY_MCP_VERIFICATION_ROOT>/<new-id>/build/`. Choose an ID without an existing registration. Then register the local checkout explicitly.

```sh
unity-mcp --no-project-context verification-candidate register --project . --candidate-id local-copy --proof transfer/proof.json --sha256 <reviewed-sha256>
```

Registration rechecks the proof and local source bytes. It compares the installed manifest digest, build ID, source revision and scenario and hashes the installed payload files. Immediately before publishing, it samples the file list again and compares each file's identity, size and timestamps with the hashed file. A detected change stops publication and allows a retry with the restored build. A successful registration publishes a local `candidate.json` bound to the selected checkout, along with any validated source inventory chunks. Existing registrations take precedence. The destination filesystem must support creating hard links for atomic publication. The operator controls the build location and keeps it stable during admission. The ordinary Player launcher checks the actual payload again before execution.

Use the returned ID with the ordinary [verification prepare, run and report workflow](verification-profiles.md#verify-a-registered-standalone-player). Each invocation of the portable commands operates offline and leaves execution to that workflow.
