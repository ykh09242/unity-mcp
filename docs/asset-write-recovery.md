# Asset source publication and recovery

Shader and UI source creation refuses an existing destination. Texture generation retains its existing update behavior. Existing shader, UI, stylesheet-link and texture-pixel updates stage complete bytes beside the source, flush the staging file, and publish with a filesystem replacement. Asset metadata is not rewritten; Unity import starts only after publication is confirmed.

Script change-set compensation follows the same publication path. It compares the candidate and retained metadata, preserves the original preimage, records restoration intent, and publishes the complete original. A process interruption before publication leaves the complete candidate; after publication it leaves the complete restored source. Explicit recovery reconciles the retained change-set manifest with the observed bytes.

An update never falls back to truncating a file or deleting it before moving its replacement. An unsupported replacement, sharing violation, permission denial or staging failure returns an error. Unsupported filesystems therefore retain the source instead of weakening publication guarantees. This is process-interruption protection, not a filesystem-wide transaction or a guarantee against hardware/power failure.

## Concurrent editors and retained files

The writer compares expected bytes, holds a handle that excludes in-place writers where the runtime supports sharing enforcement, checks metadata again, and verifies the displaced preimage. Filesystem replacement is not compare-and-swap: an uncooperative program can rename a different file over the target during publication. A detected postcommit conflict retains the displaced `.mcp-<attempt>.bak` alongside the asset and reports its location. On Unix, termination after the atomic exchange and before backup naming can leave the displaced preimage in `.mcp-<attempt>.tmp`; uncertain staging files are preserved too. Script change-set recovery recognizes this boundary and verifies the retained bytes before restoring. Inspect that evidence before retrying; the tool does not blindly restore over another writer.

Normal completion removes owned staging and backup files. Process termination can leave `.mcp-<attempt>.tmp` or `.bak` files. They are hidden staging/recovery evidence, not Unity assets. Do not delete uncertain backups automatically. Change-set original/candidate blobs and manifests remain the recovery authority for interrupted script operations.

Windows uses the existing .NET APIs backed by Windows file operations. Linux uses `renameat2` with `RENAME_EXCHANGE` for updates and `RENAME_NOREPLACE` for creation; macOS uses `renamex_np` with `RENAME_SWAP` and `RENAME_EXCL`. These are OS functions and require no developer-installed library or driver. Unsupported functions or filesystems fail without a non-atomic fallback. Unity Mono's Unix `File.Replace` with a backup and `File.Move` are deliberately avoided here.

The standalone conformance harness compiles the actual publication helpers and exercises the OS operations, partial-stage faults, concurrent creation, post-admission replacement, and owned process termination before and after publication on Windows, Linux, and macOS. It uses the .NET runtime; native Unity execution is reported separately. The disk-full case injects an `IOException` after a partial write rather than exhausting the machine's disk.

Primary platform contracts: [Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html), [Apple rename flags](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/stdio.h), and [Windows File.Replace](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.replace).
