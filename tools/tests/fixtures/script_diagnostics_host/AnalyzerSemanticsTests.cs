using System.Collections.Immutable;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.Diagnostics;
using NUnit.Framework;
using UnityMcp.ScriptDiagnostics;

namespace ScriptDiagnosticsHost.Tests;

public class AnalyzerSemanticsTests
{
    private static readonly string Root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "unity-mcp-analysis-fixture"));
    private const string UnityStub = """
        namespace UnityEngine {
            public class Object {
                public string name => "";
                public static bool operator ==(Object a, Object b) => false;
                public static bool operator !=(Object a, Object b) => true;
                public override bool Equals(object other) => false;
                public override int GetHashCode() => 0;
            }
            public class MonoBehaviour : Object { }
            [System.AttributeUsage(System.AttributeTargets.Field)] public class SerializeField : System.Attribute { }
        }
        """;

    private static AnalysisRequest Request(string code, string config = "") =>
        new()
        {
            ProjectRoot = Root,
            SourceToken = new string('a', 64),
            SelectedPaths = ["Assets/Changed.cs"],
            IncludeSuppressed = true,
            Assemblies =
            [
                new AssemblyInput
                {
                    Name = "Game",
                    LanguageVersion = "9.0",
                    References = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator),
                    Sources = [new SourceInput("Assets/UnityStub.cs", UnityStub), new SourceInput("Assets/Changed.cs", code)],
                    Configs =
                    [
                        new SourceInput(
                            ".editorconfig",
                            "root = true\n[*.cs]\ncsharp_style_prefer_null_propagation = true:suggestion\ndotnet_diagnostic.IDE0031.severity = suggestion\n"
                                + config
                        ),
                    ],
                },
            ],
        };

    [Test]
    public async Task DeclarationSummaryUsesSemanticSerializationAttributesAndSignatures()
    {
        var request = Request(
            """
            namespace UnityEngine.Serialization {
                public class FormerlySerializedAsAttribute : System.Attribute {
                    public FormerlySerializedAsAttribute(string name) {} }
            }
            class C : UnityEngine.MonoBehaviour {
                [UnityEngine.SerializeField, UnityEngine.Serialization.FormerlySerializedAs("oldCount")]
                private int count;
                [System.NonSerialized] public int transient;
                public static int shared;
                public void Click(int value) {}
            }
            """
        );
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        var field = result.Declarations.Single(d => d.Kind == "field" && d.Name == "count");
        Assert.That(field.Serialized, Is.True);
        Assert.That(field.FormerNames, Is.EqualTo(new[] { "oldCount" }));
        Assert.That(field.Type, Is.EqualTo("int"));
        Assert.That(result.Declarations.Any(d => d.Kind == "field" && d.Name == "transient" && d.Serialized), Is.False);
        Assert.That(result.Declarations.Any(d => d.Kind == "field" && d.Name == "shared" && d.Serialized), Is.False);
        Assert.That(result.Declarations.Single(d => d.Name == "Click").Signature, Does.Contain("int"));
    }

    [Test]
    public async Task UnityNullPropagationUsesUnt0008()
    {
        var result = await AnalyzerRunner.AnalyzeAsync(Request("class C { string Read(UnityEngine.Object item) => item?.name; }"));
        Assert.That(result.Complete, Is.True, System.Text.Json.JsonSerializer.Serialize(result));
        Assert.That(result.Diagnostics.Any(d => d.Id == "UNT0008" && !d.Suppressed), Is.True);
        Assert.That(result.Complete, Is.True, string.Join(",", result.CoverageGaps));
    }

    [Test]
    public async Task LineOnlyMovementPreservesDiagnosticAnchors()
    {
        const string code = "class C { string Read(UnityEngine.Object item) => item?.name; }";
        var before = await AnalyzerRunner.AnalyzeAsync(Request(code));
        var after = await AnalyzerRunner.AnalyzeAsync(Request("// inserted line\n\n" + code));
        var left = before.Diagnostics.Single(d => d.Id == "UNT0008");
        var right = after.Diagnostics.Single(d => d.Id == "UNT0008");
        Assert.That(right.Anchor, Is.EqualTo(left.Anchor));
        Assert.That(right.Line, Is.Not.EqualTo(left.Line));
        Assert.That(after.EnvironmentSignature, Is.EqualTo(before.EnvironmentSignature));
    }

    [Test]
    public async Task EnvironmentSignatureIncludesUnselectedSourceContext()
    {
        var before = Request("class C { string Read(UnityEngine.Object item) => item?.name; }");
        var changed = before.Assemblies[0] with
        {
            Sources = before
                .Assemblies[0]
                .Sources.Select(s => s.Path.EndsWith("UnityStub.cs") ? s with { Text = s.Text + "\nclass NewContext {}" } : s)
                .ToArray(),
        };
        var left = await AnalyzerRunner.AnalyzeAsync(before);
        var right = await AnalyzerRunner.AnalyzeAsync(before with { Assemblies = [changed] });
        Assert.That(right.EnvironmentSignature, Is.Not.EqualTo(left.EnvironmentSignature));
    }

    [Test]
    public async Task SyntaxAnchorExposesRelocationWithoutCollapsingDuplicateDiagnostics()
    {
        var original = Request("class C { string Read(UnityEngine.Object item) => item?.name + item?.name; }");
        var renamed = Request("class C { string Renamed(UnityEngine.Object item) => item?.name + item?.name; }");
        var before = await AnalyzerRunner.AnalyzeAsync(original);
        var after = await AnalyzerRunner.AnalyzeAsync(renamed);
        var left = before.Diagnostics.Where(d => d.Id == "UNT0008").ToArray();
        var right = after.Diagnostics.Where(d => d.Id == "UNT0008").ToArray();
        Assert.That(left.Length, Is.EqualTo(2));
        Assert.That(right.Length, Is.EqualTo(2));
        Assert.That(left[0].Anchor, Is.EqualTo(left[1].Anchor));
        Assert.That(right[0].Anchor, Is.Not.EqualTo(left[0].Anchor));
        Assert.That(right[0].SyntaxAnchor, Is.EqualTo(left[0].SyntaxAnchor).And.Not.Empty);
    }

    [Test]
    public async Task PlainObjectNullPropagationSuggestsIde0031()
    {
        var result = await AnalyzerRunner.AnalyzeAsync(Request("class C { string Read(string item) { return item == null ? null : item.ToString(); } }"));
        Assert.That(result.Diagnostics.Any(d => d.Id == "IDE0031" && !d.Suppressed), Is.True);
    }

    [Test]
    public async Task UnitySuppressorPreventsConflictingNullPropagationAdvice()
    {
        var result = await AnalyzerRunner.AnalyzeAsync(Request("class C { string Read(UnityEngine.Object item) { return item == null ? null : item.name; } }"));
        Assert.That(result.Diagnostics.Any(d => d.Id == "IDE0031" && !d.Suppressed), Is.False);
        Assert.That(result.Diagnostics.Any(d => d.Id == "IDE0031" && d.Suppressed), Is.True);
    }

    [Test]
    public async Task ProjectSeverityPromotesAnAdvisoryRule()
    {
        var result = await AnalyzerRunner.AnalyzeAsync(
            Request("class C { string Read(UnityEngine.Object item) => item?.name; }", "dotnet_diagnostic.UNT0008.severity = error\n")
        );
        Assert.That(result.Diagnostics.Any(d => d.Id == "UNT0008" && d.Severity == "error"), Is.True);
    }

    [Test]
    public async Task ContextDiagnosticsStaySeparateFromSelectedFiles()
    {
        var request = Request("class C { string Read(UnityEngine.Object item) => item?.name; }");
        request = request with { SelectedPaths = ["Assets/UnityStub.cs"] };
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        Assert.That(result.Diagnostics.Any(d => d.Path == "Assets/Changed.cs"), Is.False);
        Assert.That(result.ContextDiagnostics.Any(d => d.Id == "UNT0008"), Is.True);
    }

    [Test]
    public async Task NestedConfigurationOverridesParent()
    {
        var request = Request("class C { string Read(UnityEngine.Object item) => item?.name; }", "dotnet_diagnostic.UNT0008.severity = error\n");
        var input = request.Assemblies[0];
        request = request with
        {
            Assemblies = [input with { Configs = [.. input.Configs, new("Assets/.editorconfig", "[*.cs]\ndotnet_diagnostic.UNT0008.severity = none\n")] }],
        };
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        Assert.That(result.Complete, Is.True);
        Assert.That(result.Diagnostics.Any(d => d.Id == "UNT0008" && !d.Suppressed), Is.False);
    }

    [Test]
    public async Task SerializedPrivateFieldAndUnityMessageAreSuppressed()
    {
        var result = await AnalyzerRunner.AnalyzeAsync(
            Request(
                "class C : UnityEngine.MonoBehaviour { [UnityEngine.SerializeField] private int count; private void Update() { System.Console.WriteLine(count); } }",
                "dotnet_diagnostic.IDE0051.severity = warning\n"
            )
        );
        Assert.That(result.Complete, Is.True, string.Join(",", result.AnalyzerErrors));
        Assert.That(result.Diagnostics.Any(d => d.Id == "CS0649" && d.Suppressed), Is.True);
        Assert.That(result.Diagnostics.Any(d => d.Id == "IDE0051" && d.Suppressed), Is.True);
        Assert.That(result.Diagnostics.Any(d => (d.Id == "CS0649" || d.Id == "IDE0051") && !d.Suppressed), Is.False);
    }

    [Test]
    public void DiscoveryDoesNotDuplicateAnalyzerTypes()
    {
        var analyzers = AnalyzerRunner.LoadPinnedAnalyzers();
        Assert.That(analyzers.Length, Is.GreaterThan(50));
        Assert.That(analyzers.Select(a => a.GetType()).Distinct().Count(), Is.EqualTo(analyzers.Length));
    }

    [Test]
    public async Task AnalyzerCrashCannotProduceACompleteResult()
    {
        var result = await AnalyzerRunner.AnalyzeWithAsync(Request("class C {}"), ImmutableArray.Create<DiagnosticAnalyzer>(new BrokenAnalyzer()));
        Assert.That(result.Complete, Is.False);
        Assert.That(result.AnalyzerErrors, Is.Not.Empty);
    }

    private sealed class BrokenAnalyzer : DiagnosticAnalyzer
    {
        public override ImmutableArray<DiagnosticDescriptor> SupportedDiagnostics =>
            [new("QA0001", "Fixture", "Fixture", "QA", DiagnosticSeverity.Warning, true)];

        public override void Initialize(AnalysisContext context) => throw new InvalidOperationException("fixture failure");
    }

    [Test]
    public async Task DefaultStyleHintsRemainAvailableWithoutProjectConfiguration()
    {
        var request = Request("class C { string Read(string item) => item == null ? null : item.ToString(); }");
        request = request with { Assemblies = [request.Assemblies[0] with { Configs = [] }] };
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        Assert.That(result.Diagnostics.Any(d => d.Id == "IDE0031"), Is.True);
    }

    [Test]
    public async Task PartialDeclarationsAndInactiveBranchesUseTheCapturedContext()
    {
        var request = Request("partial class C { string Read() => value?.name; }\n#if OMITTED\nthis does not compile\n#endif");
        request = request with
        {
            Assemblies =
            [
                request.Assemblies[0] with
                {
                    Sources = [.. request.Assemblies[0].Sources, new SourceInput("Assets/Partial.cs", "partial class C { UnityEngine.Object value; }")],
                },
            ],
        };
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        Assert.That(result.Complete, Is.True);
        Assert.That(result.Diagnostics.Any(d => d.Id == "UNT0008"), Is.True);
        Assert.That(result.Diagnostics.Any(d => d.Provider == "compiler" && d.Severity == "error"), Is.False);
    }

    [Test]
    public async Task ReferencedProjectUsesCurrentSourceInsteadOfAnOldCompiledDll()
    {
        var request = Request("class C { string Read(CurrentDependency item) => item?.name; }");
        var input = request.Assemblies[0];
        request = request with
        {
            Assemblies =
            [
                input with
                {
                    Sources = [input.Sources[1]],
                    ProjectReferences = ["Dependency"],
                },
                input with
                {
                    Name = "Dependency",
                    Sources = [input.Sources[0], new("Assets/Dependency.cs", "public class CurrentDependency : UnityEngine.Object {}")],
                    Configs = [],
                },
            ],
        };
        var result = await AnalyzerRunner.AnalyzeAsync(request);
        Assert.That(result.Complete, Is.True, string.Join(",", result.CoverageGaps));
        Assert.That(result.Diagnostics.Any(d => d.Id == "UNT0008"), Is.True);
        Assert.That(result.Diagnostics.Any(d => d.Provider == "compiler" && d.Severity == "error"), Is.False);
    }
}
