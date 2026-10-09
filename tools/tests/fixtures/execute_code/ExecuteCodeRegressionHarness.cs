// Managed process only: actual ExecuteCode, responses, assembly helper, converters and Unity math structs.
using System;
using System.CodeDom.Compiler;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Microsoft.CSharp;
using Newtonsoft.Json.Linq;

public static class ExecuteCodeRegressionState
{
    public static int Executions;
}

internal static class ExecuteCodeRegressionHarness
{
    private static int cases,
        failures;
    private static string work,
        filter;

    private static int Main(string[] args)
    {
        work = args[0];
        filter = args.Length > 1 ? args[1] : null;
        Console.WriteLine("RUNTIME: " + typeof(object).Assembly.FullName + "; Unity=" + typeof(UnityEngine.Vector3).Assembly.FullName);
        if (args.Length > 2 && args[2] == "isolated-cold")
        {
            Check("cold auto discovers Roslyn bootstrap references and stabilizes cache", ColdAuto);
            Console.WriteLine("ISOLATED_RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
            return failures == 0 ? 0 : 1;
        }
        if (args.Length > 2 && args[2].StartsWith("isolated-recovery", StringComparison.Ordinal))
        {
            Check("Roslyn followed by CodeDom isolated recovery", () => CompilerRecovery(args[2] == "isolated-recovery-error"));
            Console.WriteLine("ISOLATED_RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
            return failures == 0 ? 0 : 1;
        }
        Check(
            "cold auto discovers Roslyn bootstrap references and stabilizes cache",
            () =>
            {
                Isolated("isolated-cold", "cold auto");
            }
        );
        Check("roslyn compilation failure followed by codedom succeeds", () => Isolated("isolated-recovery-error", "isolated recovery"));
        Check("roslyn success followed by codedom succeeds", () => Isolated("isolated-recovery-success", "isolated recovery"));
        // Other cases deliberately start with both compiler assemblies already present.
        Assembly.Load("Microsoft.CodeAnalysis");
        Assembly.Load("Microsoft.CodeAnalysis.CSharp");
#if UNITY_7000_0_OR_NEWER
        Check("Unity7 simulated tracked path refreshes warmed references", Unity7LateReference);
        Check("Unity7 simulated fallback preserves distinct snippet results and reuse", Unity7FallbackResults);
        Check("Unity7 simulated snippets reuse modules and compile distinct source", Unity7SnippetCache);
        Check("Unity7 simulated dynamic and pathless loads preserve caches", Unity7ExcludedLoads);
#endif
        foreach (var backend in new[] { "codedom", "roslyn" })
        {
            Check(backend + ": basic return", () => Require(Result(Execute("return 42;", backend)).Value<int>() == 42, "Wrong result"));
            Check(
                backend + ": statements without return",
                () =>
                {
                    var response = Execute("int x = 1;", backend);
                    Require(response is SuccessResponse, Describe(response));
                    Require(Data(response)["result"] == null, "Expected no result");
                }
            );
            Check(
                backend + ": conditional return and final comment fall through",
                () =>
                {
                    var response = Execute("int x = 1; if (x == 2) return x; // final comment", backend);
                    Require(response is SuccessResponse && Data(response)["result"] == null, Describe(response));
                }
            );
            Check(
                backend + ": compilation diagnostics retain user line",
                () =>
                {
                    var response = Execute("int x = 1;\nint y = \"bad\";\nreturn y;", backend) as ErrorResponse;
                    Require(response != null, "Invalid C# accepted");
                    var details = JObject.FromObject(response.Data);
                    Require(details["errors"].Values<string>().Any(error => error.StartsWith("Line 2:", StringComparison.Ordinal)), details.ToString());
                }
            );
            Check(
                backend + ": runtime exception retains cause",
                () =>
                {
                    var response = Execute("throw new InvalidOperationException(\"fixture boom\");", backend) as ErrorResponse;
                    Require(response != null && response.Error.Contains("fixture boom"), "Runtime failure was lost");
                    Require(JObject.FromObject(response.Data)["exceptionType"].Value<string>() == "InvalidOperationException", "Wrong exception type");
                }
            );
            foreach (var invalid in new JToken[] { new JValue(0.5), new JValue(-0.5), new JValue(true), new JObject(), new JArray() })
                Check(
                    backend + ": replay rejects " + invalid.Type + " " + invalid.ToString(Newtonsoft.Json.Formatting.None),
                    () =>
                    {
                        Require(Execute("return ++ExecuteCodeRegressionState.Executions;", backend) is SuccessResponse, "Seed failed");
                        Require(Execute("return ++ExecuteCodeRegressionState.Executions;", backend) is SuccessResponse, "Second seed failed");
                        int before = HistoryCount();
                        var response = Command(new JObject { ["action"] = "replay", ["index"] = invalid });
                        Require(
                            response is ErrorResponse && ExecuteCodeRegressionState.Executions == 2 && HistoryCount() == before,
                            Describe(response) + "; executions=" + ExecuteCodeRegressionState.Executions + "; history=" + HistoryCount()
                        );
                    }
                );
            foreach (var invalid in new JToken[] { new JValue(0.5), new JValue(true), new JArray(), new JObject(), new JValue("bad") })
                Check(
                    backend + ": limit rejects " + invalid.Type + " " + invalid.ToString(Newtonsoft.Json.Formatting.None),
                    () => Require(Command(new JObject { ["action"] = "get_history", ["limit"] = invalid }) is ErrorResponse, "Invalid limit accepted")
                );
            foreach (var invalid in new JToken[] { new JValue(0), new JValue(1.5), new JArray(), new JObject(), new JValue("bad") })
                Check(
                    backend + ": safety rejects " + invalid.Type + " " + invalid.ToString(Newtonsoft.Json.Formatting.None),
                    () =>
                    {
                        var response = Command(
                            new JObject
                            {
                                ["action"] = "execute",
                                ["code"] = "return ++ExecuteCodeRegressionState.Executions;",
                                ["compiler"] = backend,
                                ["safety_checks"] = invalid,
                            }
                        );
                        Require(
                            response is ErrorResponse && ExecuteCodeRegressionState.Executions == 0 && HistoryCount() == 0,
                            Describe(response) + "; executions=" + ExecuteCodeRegressionState.Executions
                        );
                    }
                );
            Check(
                backend + ": string integer/boolean compatibility",
                () =>
                {
                    Require(
                        Command(
                            new JObject
                            {
                                ["action"] = "execute",
                                ["code"] = "return ++ExecuteCodeRegressionState.Executions;",
                                ["compiler"] = backend,
                                ["safety_checks"] = "false",
                            }
                        ) is SuccessResponse,
                        "Boolean string rejected"
                    );
                    Require(
                        Command(new JObject { ["action"] = "replay", ["index"] = "0" }) is SuccessResponse && ExecuteCodeRegressionState.Executions == 2,
                        "Integer string rejected"
                    );
                }
            );
            Check(
                backend + ": newly loaded assembly is referenceable",
                () =>
                {
                    Require(Execute("return 1;", backend) is SuccessResponse, "Seed failed");
                    string type = "LateType" + Guid.NewGuid().ToString("N");
                    string path = Path.Combine(work, type + ".dll");
                    using (var provider = new CSharpCodeProvider())
                    {
                        var parameters = new CompilerParameters { GenerateExecutable = false, OutputAssembly = path };
                        var compiled = provider.CompileAssemblyFromSource(parameters, "public static class " + type + " { public static int Value = 73; }");
                        var errors = compiled
                            .Errors.Cast<CompilerError>()
                            .Where(e => !e.IsWarning && !(string.IsNullOrEmpty(e.ErrorNumber) && string.IsNullOrWhiteSpace(e.ErrorText?.Replace("\uFEFF", ""))))
                            .ToArray();
                        Require(errors.Length == 0 && File.Exists(path), string.Join(";", errors.Select(e => e.ToString())));
                    }
                    Assembly.LoadFrom(path);
                    Require(Result(Execute("return " + type + ".Value;", backend)).Value<int>() == 73, "New reference unavailable");
                }
            );
            Check(
                backend + ": identical source reuses assembly",
                () =>
                {
                    const string snippet = "return ++ExecuteCodeRegressionState.Executions;";
                    Require(Execute(snippet, backend) is SuccessResponse, "Seed failed");
                    int before = SnippetAssemblies();
                    Require(Execute(snippet, backend) is SuccessResponse, "Repeat failed");
                    Require(SnippetAssemblies() == before && ExecuteCodeRegressionState.Executions == 2, "Repeated compilation or skipped execution");
                }
            );
            Check(
                backend + ": hot snippet survives 70 unique fillers",
                () =>
                {
                    const string hot = "return ++ExecuteCodeRegressionState.Executions;";
                    Require(Execute(hot, backend) is SuccessResponse, "Seed failed");
                    int unnecessary = 0;
                    var watch = Stopwatch.StartNew();
                    for (int i = 0; i < 70; i++)
                    {
                        Require(Execute("return " + (1000 + i) + ";", backend) is SuccessResponse, "Filler failed: " + i);
                        int before = SnippetAssemblies();
                        Require(Execute(hot, backend) is SuccessResponse, "Hot execution failed");
                        unnecessary += SnippetAssemblies() - before;
                    }
                    Console.WriteLine(
                        "OBSERVATION: "
                            + backend
                            + ": 70 cold fillers + 70 hot calls: "
                            + watch.Elapsed.TotalMilliseconds.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)
                            + " ms; hot recompilations="
                            + unnecessary
                    );
                    Require(
                        unnecessary == 0 && ExecuteCodeRegressionState.Executions == 71,
                        "Hot recompilations=" + unnecessary + "; executions=" + ExecuteCodeRegressionState.Executions
                    );
                }
            );
            Check(backend + ": Vector3 structured components", () => Components(Result(Execute("return new Vector3(1, 2, 3);", backend)), "x", "y", "z"));
            Check(
                backend + ": Quaternion structured components",
                () => Components(Result(Execute("return new Quaternion(1, 2, 3, 4);", backend)), "x", "y", "z", "w")
            );
            Check(
                backend + ": nested anonymous Unity result",
                () =>
                {
                    var result = Result(
                        Execute("return new { vector = new Vector3(1, 2, 3), nested = new { rotation = new Quaternion(1, 2, 3, 4) } };", backend)
                    );
                    Require(result is JObject, "Expected object, got " + result);
                    Components(result["vector"], "x", "y", "z");
                    Components(result["nested"]["rotation"], "x", "y", "z", "w");
                }
            );
        }
        Check(
            "auto and explicit roslyn reuse actual backend assembly",
            () =>
            {
                const string snippet = "return Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString();";
                string first = Result(Execute(snippet, "auto")).Value<string>();
                string second = Result(Execute(snippet, "roslyn")).Value<string>();
                Require(first == second, "Different compiled module identities: " + first + "; " + second);
            }
        );
        Check(
            "roslyn: distinct compilation performance observation",
            () =>
            {
                Require(Execute("return 0;", "roslyn") is SuccessResponse, "Warmup failed");
                var allocationMethod = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Static | BindingFlags.Public);
                long before = allocationMethod == null ? 0 : (long)allocationMethod.Invoke(null, null);
                var watch = Stopwatch.StartNew();
                for (int i = 0; i < 20; i++)
                    Require(Result(Execute("return " + (2000 + i) + ";", "roslyn")).Value<int>() == 2000 + i, "Wrong observed result");
                watch.Stop();
                long allocated = allocationMethod == null ? 0 : (long)allocationMethod.Invoke(null, null) - before;
                Console.WriteLine(
                    "OBSERVATION: roslyn: 20 distinct compilations after warmup: "
                        + watch.Elapsed.TotalMilliseconds.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)
                        + " ms; thread allocations="
                        + (allocationMethod == null ? "unavailable" : allocated.ToString(System.Globalization.CultureInfo.InvariantCulture))
                );
            }
        );
        Check(
            "invalid compiler rejected without execution/history",
            () =>
            {
                var response = Execute("return ++ExecuteCodeRegressionState.Executions;", "typo");
                Require(
                    response is ErrorResponse && ExecuteCodeRegressionState.Executions == 0 && HistoryCount() == 0,
                    Describe(response) + "; executions=" + ExecuteCodeRegressionState.Executions
                );
            }
        );
        Console.WriteLine("RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
        return cases == 0 ? 2
            : failures == 0 ? 0
            : 1;
    }

#if UNITY_7000_0_OR_NEWER
    private static void Unity7LateReference()
    {
        string type = "TrackedLateType" + Guid.NewGuid().ToString("N");
        string path = Path.Combine(work, type + ".dll");
        using (var provider = new CSharpCodeProvider())
        {
            var compiled = provider.CompileAssemblyFromSource(
                new CompilerParameters { GenerateExecutable = false, OutputAssembly = path },
                "public static class " + type + " { public static int Value = 73; }"
            );
            var errors = compiled
                .Errors.Cast<CompilerError>()
                .Where(error =>
                    !error.IsWarning && !(string.IsNullOrEmpty(error.ErrorNumber) && string.IsNullOrWhiteSpace(error.ErrorText?.Replace("\uFEFF", "")))
                )
                .ToArray();
            Require(errors.Length == 0 && File.Exists(path), string.Join(";", errors.Select(error => error.ToString())));
        }
        var inner = Assembly.LoadFrom(path);
        UnityEngine.Assemblies.CurrentAssemblies.Hidden.Add(inner);
        var wrapped = new SimulatedUnity7Assembly(inner, path);
        Require(
            wrapped.Location == "" && MCPForUnity.Runtime.Helpers.UnityAssembliesCompat.GetAssemblyPath(wrapped) == path,
            "Fixture path contract incorrect"
        );
        const string warm = "return Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString();";
        Result(Execute(warm, "roslyn"));
        string settled = Result(Execute(warm, "roslyn")).Value<string>();
        Require(Result(Execute(warm, "roslyn")).Value<string>() == settled, "Warm cache did not settle");
        var paths = AssemblyPaths();
        Require(!paths.Contains(path), "Hidden assembly entered warm references");
        UnityEngine.Assemblies.CurrentAssemblies.Published.Add(wrapped);
        NotifyAssemblyLoaded(wrapped);
        Require(AssemblyPaths().Contains(path), "Tracked Unity path missing after late load notification");
        Require(Result(Execute("return " + type + ".Value;", "roslyn")).Value<int>() == 73, "Late reference did not compile/execute");
    }

    private static void Unity7FallbackResults()
    {
        const string firstSource = "return new { value = 21, module = Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString() };";
        const string secondSource = "return new { value = 22, module = Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString() };";
        var first = Result(Execute(firstSource, "roslyn"));
        var second = Result(Execute(secondSource, "roslyn"));
        Require(first["value"].Value<int>() == 21 && second["value"].Value<int>() == 22, "Distinct snippets returned " + first + " and " + second);
        Require(first["module"].Value<string>() != second["module"].Value<string>(), "Distinct snippets reused one module");
        int loads = UnityEngine.Assemblies.CurrentAssemblies.ByteLoads;
        var repeated = Result(Execute(firstSource, "roslyn"));
        Require(
            repeated["value"].Value<int>() == 21 && repeated["module"].Value<string>() == first["module"].Value<string>(),
            "Repeated snippet lost its original result/module"
        );
        Require(UnityEngine.Assemblies.CurrentAssemblies.ByteLoads == loads, "Repeated snippet called byte loader again");
    }

    private static void Unity7SnippetCache()
    {
        const string source = "return Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString();";
        Result(Execute(source, "roslyn"));
        string first = Result(Execute(source, "roslyn")).Value<string>();
        int before = UnityEngine.Assemblies.CurrentAssemblies.ByteLoads;
        Require(Result(Execute(source, "roslyn")).Value<string>() == first, "Identical snippet changed module");
        Require(UnityEngine.Assemblies.CurrentAssemblies.ByteLoads == before, "Identical snippet loaded another assembly");
        string distinct = Result(Execute(source + " // distinct source", "roslyn")).Value<string>();
        Require(distinct != first && UnityEngine.Assemblies.CurrentAssemblies.ByteLoads == before + 1, "Distinct source did not compile independently");
        Require(Result(Execute(source, "roslyn")).Value<string>() == first, "Snippet load invalidated prior compiled cache");
    }

    private static void Unity7ExcludedLoads()
    {
        Result(Execute("return 5;", "roslyn"));
        var paths = AssemblyPaths();
        int generation = AssemblyGeneration();
        var inner = Assembly.GetExecutingAssembly();
        var pathless = new SimulatedUnity7Assembly(inner, "");
        string dynamicPath = Path.Combine(work, "SimulatedDynamic.dll");
        File.Copy(inner.Location, dynamicPath, true);
        var dynamic = new SimulatedUnity7Assembly(inner, dynamicPath, true);
        UnityEngine.Assemblies.CurrentAssemblies.Published.Add(pathless);
        UnityEngine.Assemblies.CurrentAssemblies.Published.Add(dynamic);
        NotifyAssemblyLoaded(pathless);
        NotifyAssemblyLoaded(dynamic);
        Require(AssemblyGeneration() == generation && ReferenceEquals(AssemblyPaths(), paths), "Excluded load invalidated cache");
        typeof(ExecuteCode).GetMethod("OnDomainReload", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        Require(!AssemblyPaths().Contains(dynamicPath), "Dynamic assembly path admitted as reference");
    }

    private static string[] AssemblyPaths() =>
        (string[])typeof(ExecuteCode).GetMethod("GetAssemblyPaths", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);

    private static int AssemblyGeneration() =>
        (int)typeof(ExecuteCode).GetField("_assemblyGeneration", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

    private static void NotifyAssemblyLoaded(Assembly assembly) =>
        typeof(ExecuteCode)
            .GetMethod("OnAssemblyLoaded", BindingFlags.Static | BindingFlags.NonPublic)
            .Invoke(null, new object[] { null, new AssemblyLoadEventArgs(assembly) });
#endif

    private static void ColdAuto()
    {
        Require(!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Microsoft.CodeAnalysis"), "Roslyn already loaded before cold test");
        const string snippet =
            "return new { compiler = typeof(Microsoft.CodeAnalysis.Compilation).Assembly.GetName().Name, module = Assembly.GetExecutingAssembly().ManifestModule.ModuleVersionId.ToString() };";
        var first = Execute(snippet, "auto");
        Require(Data(first)["compiler"].Value<string>() == "roslyn", "Auto did not select installed Roslyn");
        Require(Result(first)["compiler"].Value<string>() == "Microsoft.CodeAnalysis", "Bootstrap reference unavailable");
        var settled = Result(Execute(snippet, "auto"));
        int before = SnippetAssemblies();
        var repeated = Result(Execute(snippet, "auto"));
        Require(
            settled["module"].Value<string>() == repeated["module"].Value<string>() && SnippetAssemblies() == before,
            "Cache did not stabilize after bootstrap"
        );
    }

    private static void Isolated(string mode, string childFilter)
    {
        var domain = AppDomain.CreateDomain("ExecuteCodeRegression_" + mode);
        try
        {
            Require(
                domain.ExecuteAssembly(Assembly.GetExecutingAssembly().Location, new[] { work, childFilter, mode }) == 0,
                "Isolated case failed; see child result above"
            );
        }
        finally
        {
            AppDomain.Unload(domain);
        }
    }

    private static void CompilerRecovery(bool firstFails)
    {
        Require(!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Microsoft.CodeAnalysis"), "Roslyn already loaded before recovery");
        var first = Execute(firstFails ? "int x = \"bad\"; return x;" : "return 7;", "roslyn");
        Require(firstFails ? first is ErrorResponse : first is SuccessResponse, "Unexpected first result: " + Describe(first));
        var second = Execute("return 42;", "codedom");
        Require(Result(second).Value<int>() == 42, "CodeDom recovery result incorrect");
    }

    private static void Components(JToken result, params string[] names)
    {
        Require(result is JObject, "Expected structured components, got " + result);
        for (int i = 0; i < names.Length; i++)
            Require(result[names[i]] != null && result[names[i]].Value<double>() == i + 1, "Wrong component " + names[i]);
    }

    private static int SnippetAssemblies() => AppDomain.CurrentDomain.GetAssemblies().Count(a => a.GetType("MCPDynamicCode", false) != null);

    private static object Execute(string code, string compiler) =>
        Command(
            new JObject
            {
                ["action"] = "execute",
                ["code"] = code,
                ["compiler"] = compiler,
            }
        );

    private static object Command(JObject parameters) => ExecuteCode.HandleCommand(parameters);

    private static JObject Data(object response)
    {
        Require(response is SuccessResponse, Describe(response));
        return JObject.FromObject(((SuccessResponse)response).Data);
    }

    private static JToken Result(object response)
    {
        var data = Data(response);
        Require(data["result"] != null, "Missing result");
        return data["result"];
    }

    private static int HistoryCount() => Data(Command(new JObject { ["action"] = "get_history" }))["total"].Value<int>();

    private static string Describe(object response) =>
        response is ErrorResponse error
            ? error.Error + " " + (error.Data == null ? "" : JObject.FromObject(error.Data).ToString(Newtonsoft.Json.Formatting.None))
            : response.GetType().Name;

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    private static void Check(string name, Action test)
    {
        if (!string.IsNullOrEmpty(filter) && name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0)
            return;
        cases++;
        typeof(ExecuteCode).GetMethod("OnDomainReload", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        Command(new JObject { ["action"] = "clear_history" });
        ExecuteCodeRegressionState.Executions = 0;
        try
        {
            test();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception error)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + error.Message);
        }
    }
}

// Narrow seams: no Editor process, logging, registry initialization or asset access is required.
namespace UnityEditor
{
    public sealed class InitializeOnLoadMethodAttribute : Attribute { }
}

namespace MCPForUnity.Editor.Tools
{
    public sealed class McpForUnityToolAttribute : Attribute
    {
        public McpForUnityToolAttribute(string name) { }

        public bool AutoRegister { get; set; }
        public bool RequiresExplicitConsent { get; set; }
        public string Group { get; set; }
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public static class McpLog
    {
        public static void Warn(string text)
        {
            Console.WriteLine("WARN: " + text);
        }

        public static void Error(string text)
        {
            Console.WriteLine("ERROR: " + text);
        }
    }

    public sealed class ToolParams
    {
        private readonly JObject value;

        public ToolParams(JObject value)
        {
            this.value = value;
        }

        public RequiredString GetRequired(string key) => new RequiredString(value[key]?.ToString());
    }

    public sealed class RequiredString
    {
        public RequiredString(string value)
        {
            Value = value;
        }

        public string Value { get; }
        public bool IsSuccess => !string.IsNullOrEmpty(Value);
        public string ErrorMessage => "Required parameter missing.";
    }
}
#if EDITMODE_COMPILE
namespace MCPForUnityTests.Editor
{
    public static class TestUtilities
    {
        public static JObject ToJObject(object value) => JObject.FromObject(value);
    }
}
#endif

#if UNITY_7000_0_OR_NEWER
// Managed simulation of the Unity 7 API surface, not Unity's native implementation.
internal sealed class SimulatedUnity7Assembly : Assembly
{
    private readonly Assembly inner;
    private readonly bool dynamic;

    public SimulatedUnity7Assembly(Assembly inner, string trackedPath, bool dynamic = false)
    {
        this.inner = inner;
        TrackedPath = trackedPath;
        this.dynamic = dynamic;
    }

    public string TrackedPath { get; }
    public override string Location => "";
    public override bool IsDynamic => dynamic;
    public override string FullName => inner.FullName;

    public override AssemblyName GetName() => inner.GetName();

    public override AssemblyName GetName(bool copiedName) => inner.GetName(copiedName);

    public override AssemblyName[] GetReferencedAssemblies() => inner.GetReferencedAssemblies();

    public override Type GetType(string name, bool throwOnError, bool ignoreCase) => inner.GetType(name, throwOnError, ignoreCase);
}

namespace UnityEngine.Assemblies
{
    public static class CurrentAssemblies
    {
        internal static readonly System.Collections.Generic.HashSet<Assembly> Hidden = new System.Collections.Generic.HashSet<Assembly>();
        internal static readonly System.Collections.Generic.List<Assembly> Published = new System.Collections.Generic.List<Assembly>();
        internal static int ByteLoads;

        public static System.Collections.Generic.IReadOnlyList<Assembly> GetLoadedAssemblies() =>
            AppDomain.CurrentDomain.GetAssemblies().Where(assembly => !Hidden.Contains(assembly)).Concat(Published).ToArray();

        public static Assembly LoadFromBytes(byte[] bytes)
        {
            ByteLoads++;
            // Unity 7 a7's active-context and fallback loaders return an already
            // loaded assembly with the same simple name. Read metadata without loading
            // these bytes first so the simulation preserves that observable behavior.
            string metadataPath = Path.Combine(Path.GetTempPath(), "unity7-byte-metadata-" + Guid.NewGuid().ToString("N") + ".dll");
            string name;
            try
            {
                File.WriteAllBytes(metadataPath, bytes);
                name = AssemblyName.GetAssemblyName(metadataPath).Name;
            }
            finally
            {
                File.Delete(metadataPath);
            }
            var existing = GetLoadedAssemblies().FirstOrDefault(assembly => assembly.GetName().Name == name);
            return existing ?? Assembly.Load(bytes);
        }
    }
}

namespace UnityEngine
{
    public static class AssemblyExtension
    {
        public static string GetLoadedAssemblyPath(Assembly assembly) => assembly is SimulatedUnity7Assembly tracked ? tracked.TrackedPath : assembly.Location;
    }
}
#endif
