# Asset source publication and recovery

Shader and UI source creation refuses an existing destination. Texture generation retains its existing update behavior. Existing shader, UI, stylesheet-link and texture-pixel updates stage complete bytes beside the source, flush the staging file, and publish with a filesystem replacement. Asset metadata is not rewritten; Unity import starts only after publication is confirmed.

Script change-set compensation follows the same publication path. It compares the candidate and retained metadata, preserves the original preimage, records restoration intent, and publishes the complete original. A process interruption before publication leaves the complete candidate; after publication it leaves the complete restored source. Explicit recovery reconciles the retained change-set manifest with the observed bytes.

An update never falls back to truncating a file or deleting it before moving its replacement. An unsupported replacement, sharing violation, permission denial or staging failure returns an error. Unsupported filesystems therefore retain the source instead of weakening publication guarantees. This is process-interruption protection, not a filesystem-wide transaction or a guarantee against hardware/power failure.

## Concurrent editors and retained files

The writer compares expected bytes, holds a handle that excludes in-place writers where the runtime supports sharing enforcement, checks metadata again, and verifies the displaced preimage. Filesystem replacement is not compare-and-swap: an uncooperative program can rename a different file over the target during publication. A detected postcommit conflict retains the displaced `.mcp-<attempt>.bak` alongside the asset and reports its location. Inspect that evidence before retrying; the tool does not blindly restore over another writer.

Normal completion removes owned staging and backup files. Process termination can leave `.mcp-<attempt>.tmp` or `.bak` files. They are hidden staging/recovery evidence, not Unity assets. Do not delete uncertain backups automatically. Change-set original/candidate blobs and manifests remain the recovery authority for interrupted script operations.

The implementation uses the existing .NET APIs available to supported Unity versions, without added packages: [File.Replace](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.replace) and [FileShare](https://learn.microsoft.com/en-us/dotnet/api/system.io.fileshare). Cross-platform compile checks do not substitute for native execution on each OS.
