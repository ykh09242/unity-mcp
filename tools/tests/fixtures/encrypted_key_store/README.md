# Encrypted fallback store regression

Build and run with the existing .NET 9 SDK from the repository root, keeping all
build output and synthetic stores in an owned temporary directory:

```powershell
& tools/tests/fixtures/encrypted_key_store/RunRegression.ps1
```

The harness compiles production `EncryptedFileKeyStore` and `ISecureKeyStore`
sources directly, with synthetic Unity platform and machine-identity stubs. It
never uses a default store, OS credential service or real credentials. It checks
eight concurrent first-use processes, immutable malformed derivation material,
read-only failure when derivation material is missing, and complete ciphertext
snapshots during repeated publication. It reopens the store after concurrent work.
`-OutputRoot` optionally selects the build/probe root. `-KeyStoreSourceRoot`
optionally selects a task-local historical source copy for
red/green comparison. No optional dependencies are required.

These are host filesystem/process checks, not Unity Editor execution. Publication
uses a same-directory temporary file and filesystem move/replace; unsupported
replacement filesystems or I/O failures fail without an in-place ciphertext write.
This does not promise filesystem durability after a machine/power failure.
