# Strict tool scalar regression fixture

This standalone runner compiles actual `ParamCoercion.cs`, `ToolParams.cs`, the shared runtime `JsonScalarConversion.cs`, `StringCaseUtility.cs`, responses and helper tests with installed Mono, Newtonsoft.Json and NUnit. It launches no Unity Editor/server and downloads no packages.

```powershell
& tools/tests/fixtures/tool_scalars/RunToolScalarRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401'
```

`-SourceRootPath '<directory containing baseline ParamCoercion.cs and ToolParams.cs>' -SkipNUnit` compares the same exposed legacy helper calls against unchanged source. Baseline snapshots remain caller-owned. Source hashes, each case and the aggregate result are printed. Failure exits 1; compilation errors throw. A unique runner-owned temporary binary/reference directory is removed in `finally`.

The direct cases verify invalid Boolean/numeric conversions and explicit-invalid defaults are rejected. Valid zero/false values, missing/null defaults, invariant strings, exact alias precedence and long precision remain covered. The full run also invokes every `[Test]` and `[TestCase]` in actual `ToolParamsTests`, `ToolParamsCultureTests` and `ParamCoercionTests`, respecting setup/teardown and using real NUnit assertions. This reflection runner supports these focused fixtures; it is not a replacement for NUnit engine discovery or native Unity integration tests.

The intended operational contract replaces earlier permissive compatibility: booleans accept Boolean tokens and canonical true/false strings; integers accept Integer tokens and exact invariant integer strings, rejecting all floating tokens and fractional/scientific strings; floating types accept finite Integer/Float tokens and invariant numeric strings. Numeric 0/1 and yes/no/on/off cannot control booleans. Only missing/null optional values use defaults. Explicit invalid values throw `ArgumentException` naming the token path, allowing the tool/dispatcher to return a validation error before mutation.

Dry-run numeric validators also reject NaN and float overflow. Curve tangent/slope fields preserve explicit numeric signed infinity for stepped keys, while ordinary fields remain finite; this convention is confirmed by [Unity's issue tracker](https://issuetracker.unity3d.com/issues/fbx-custom-property-keyframe-values-show-as-infinity-when-importing). Enum readers preserve names, flags and checked underlying integers; Boolean and floating representations are rejected.
