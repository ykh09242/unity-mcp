# Property scalar regression fixture

Run with the installed Unity data directory and .NET SDK, for example:

```powershell
./tools/tests/fixtures/tool_property_scalars/RunPropertyScalarRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401'
```

The runner compiles the actual production converters and helpers against Unity's
managed math structs and Newtonsoft.Json, then executes 731 assertions in a
separate Mono process. It verifies scalar, array, list, dictionary and DTO input,
enum and nullable values, finite numbers, nullable Unity array shorthand,
byte-array encoding, parser error propagation, stepped curve tangents and
renderer setter callbacks. It prints production source hashes.

Asset, material, Undo and dirty-state API stubs throw if called. Logging is a
no-op. This fixture creates no Unity objects, runs no Editor, and performs no
scene or asset writes. It does not execute ComponentOps or MaterialOps native
Editor paths; the normal Unity compile/test checks cover those separately.

The compiler and executable output stay in a new temporary directory. The runner
checks its resolved path against the temporary root before cleanup.
