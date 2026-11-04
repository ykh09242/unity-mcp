# Encrypted fallback store regression

Build and run with the existing .NET 9 SDK from the repository root, keeping all
build output and synthetic stores in an owned temporary directory:

```powershell
& tools/tests/fixtures/encrypted_key_store/RunRegression.ps1
```

The harness compiles production `EncryptedFileKeyStore` and `ISecureKeyStore`
sources directly, with synthetic Unity platform and machine-identity stubs. It
maps the Unity platform to the actual Windows, Linux or macOS host and prints
that identity with the applicable case count. Windows-only cases are registered
only on Windows; other hosts report them as not applicable, without counting
them as passed. Linux and macOS execute the production permission-hardening path
on the explicitly supplied synthetic store directories and temporary files.
The harness
never uses a default store, OS credential service or real credentials. It checks
eight concurrent first-use processes, immutable malformed derivation material,
read-only failure when derivation material is missing, and complete ciphertext
snapshots during repeated publication. It reopens the store after concurrent work.
Concurrent snapshots must have both complete length and a valid HMAC. On Windows,
the harness checks recovery after a released read lock, bounded refusal while a
lock remains held, and access-denied publication preserving the existing winner.
Missing master or salt files are never recreated during reads, and partial
derivation material cannot replace an existing ciphertext.
`-OutputRoot` optionally selects the build/probe root. `-KeyStoreSourceRoot`
optionally selects a task-local historical source copy for
red/green comparison. No optional dependencies are required.

These are host filesystem/process checks, not Unity Editor execution. Publication
uses a same-directory temporary file and filesystem move/replace; unsupported
replacement filesystems or I/O failures fail without an in-place ciphertext write.
This does not promise filesystem durability after a machine/power failure.
The dedicated `credential-assurance.yml` workflow runs this and the process
harness on Windows, Linux and macOS for credential-source and fixture changes.
The ciphertext open retry is Windows-only, limited to native errors 32 and 2
after an existing ciphertext has been observed, and bounded to 250 ms plus the
final scheduling/open attempt. Content parsing and derivation-material reads do
not use that retry. A concurrent explicit deletion can therefore delay a failed
read briefly, but never creates new material or ciphertext.
