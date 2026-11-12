# Reviewed Unity registry artifacts

`tools/unity-registry-pins.json` records SHA256 digests of the exact registry
tarball bytes used by base and optional CI profiles. The initial 16 entries were
downloaded on 2026-11-12 from the official Unity registry, with each tarball's SHA1
verified against its official registry metadata before calculating SHA256. No
hashes are inferred from package names, extracted files, or hypothetical archives.

Fresh downloads require a valid committed identity/digest before network access.
The downloader verifies both the publisher's SHA1 and the committed SHA256 before
extracting anything. An unknown identity or mismatched digest fails closed even
when registry metadata and tarball bytes are replaced together. The lock is also
part of optional dependency cache provenance and Library cache keys, so accepting
new pins invalidates the corresponding cached inputs. The official registry and
archive host allowlist remains enforced.

## Reviewing an update

1. Review the intended exact versions in `tools/unity-ci-packages.json`, their
   compatibility and upstream release information. Keep unrelated pins unchanged.
2. Generate a separate proposal using actual official bytes:

   ```sh
   python tools/review_unity_registry_pins.py --output .work/registry-pins-proposal.json
   git diff --no-index tools/unity-registry-pins.json .work/registry-pins-proposal.json
   ```

   The command downloads artifacts and checks publisher SHA1; it installs nothing.
   Its stderr records identity, byte count and SHA256 for review. It refuses to
   write directly to the accepted lock or overwrite an existing proposal. A failed
   download leaves no completed proposal. Downloading a proposal alone does not
   authorize accepting the bytes.
3. Review changed identities, byte sizes, hashes and upstream provenance. A digest
   change without a version change needs explicit investigation; do not update a
   pin merely to make a mismatch disappear. Retain the review evidence outside the
   tracked source tree, then copy accepted entries into the committed lock.
4. Run `tools/tests/test_unity_registry_pins.py`,
   `tools/tests/test_unity_ci_package_downloads.py` and the package staging/cache
   regressions. Submit the profile and reviewed lock changes together for review.

The proposal utility uses metadata at `https://packages.unity.com/<package>` and
accepts archive URLs only on `packages.unity.com` or
`download.packages.unity.com`. It never automatically changes accepted pins.
