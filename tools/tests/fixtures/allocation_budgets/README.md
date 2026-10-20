# Managed allocation budgets

This harness compiles the actual log buffer, compilation ledger, transport helpers and snapshot comparison with minimal
Unity/data boundary stubs. It requires .NET 9 and an existing Newtonsoft.Json DLL.
It does not launch Unity. Native Editor tests separately cover behavior and
serialization; these CLR allocation numbers are not Unity heap or RSS measurements.

```powershell
dotnet run --project tools/tests/fixtures/allocation_budgets/AllocationHarness.csproj `
  -p:JsonAssembly="C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data/Managed/Newtonsoft.Json.dll"
```

The probe first checks that its allocation counter observes a known allocation.
Each case warms once before checking a generous upper bound. Inputs are created
outside the measured call. A nonzero exit reports a budget regression.

The transport probes check early oversized serialization refusal and reused small-message assembly. The socket fixture only supplies deterministic bytes; the production reader owns fragmentation, decoding, cancellation and buffer lifetime.

The snapshot probe compares two 200,000-object selections while returning eight
ordinally sorted details per delta. It checks complete counts and byte totals and
measures only aggregation, excluding the input dictionaries and native snapshot loading.
The bounded accumulator allocates memory for types and returned details, not a second
array of every selected object. Native retained-snapshot tests separately verify the
supported reader and file-handle release.
