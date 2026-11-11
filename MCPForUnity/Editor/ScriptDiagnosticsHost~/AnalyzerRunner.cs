using System.Collections.Concurrent;
using System.Collections.Immutable;
using System.Reflection;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.Diagnostics;

namespace UnityMcp.ScriptDiagnostics;

public static class AnalyzerRunner
{
    private static readonly string[] Allowlist = ["Microsoft.CodeAnalysis.CodeStyle", "Microsoft.CodeAnalysis.CSharp.CodeStyle", "Microsoft.Unity.Analyzers"];
    private static readonly Lazy<ImmutableArray<DiagnosticAnalyzer>> PinnedAnalyzers = new(LoadPinnedAnalyzers);
    private static readonly Dictionary<string, string> Providers = new()
    {
        ["compiler"] = "5.9.0",
        ["Microsoft.CodeAnalysis.CSharp.CodeStyle"] = "5.9.0",
        ["Microsoft.Unity.Analyzers"] = "1.28.0",
    };

    internal static ImmutableArray<DiagnosticAnalyzer> LoadPinnedAnalyzers()
    {
        var result = new Dictionary<string, DiagnosticAnalyzer>(StringComparer.Ordinal);
        foreach (string name in Allowlist)
        {
            var assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(AppContext.BaseDirectory, "analyzers", name + ".dll"));
            if (assembly.GetName().Name != name)
                throw new InvalidDataException("analyzer_identity_mismatch");
            foreach (var type in assembly.GetTypes().OrderBy(type => type.FullName, StringComparer.Ordinal))
            {
                if (type.IsAbstract || !typeof(DiagnosticAnalyzer).IsAssignableFrom(type))
                    continue;
                var attribute = type.GetCustomAttribute<DiagnosticAnalyzerAttribute>();
                if (attribute == null || !attribute.Languages.Contains(LanguageNames.CSharp))
                    continue;
                string key = name + ":" + type.FullName;
                result.TryAdd(key, (DiagnosticAnalyzer)Activator.CreateInstance(type, nonPublic: true)!);
            }
        }
        if (!result.Values.Any(a => a is DiagnosticSuppressor))
            throw new InvalidDataException("unity_suppressors_unavailable");
        return result.Values.ToImmutableArray();
    }

    public static async Task<AnalysisResult> AnalyzeAsync(AnalysisRequest request, CancellationToken cancellation = default)
    {
        try
        {
            return await AnalyzeWithAsync(request, PinnedAnalyzers.Value, cancellation);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception error)
        {
            return new AnalysisResult
            {
                SourceToken = request.SourceToken,
                CoverageGaps = ["analyzer_unavailable"],
                AnalyzerErrors = [error.Message],
            };
        }
    }

    internal static async Task<AnalysisResult> AnalyzeWithAsync(
        AnalysisRequest request,
        ImmutableArray<DiagnosticAnalyzer> analyzers,
        CancellationToken cancellation = default
    )
    {
        WireProtocol.Validate(request);
        var gaps = new List<string>();
        var failures = new ConcurrentQueue<string>();
        var primary = new List<DiagnosticRow>();
        var context = new List<DiagnosticRow>();
        string root = Path.GetFullPath(request.ProjectRoot);
        var selected = request.SelectedPaths.Select(path => AnalyzerConfiguration.FullPath(root, path)).ToHashSet(PathComparer);
        using var workspace = new AdhocWorkspace();
        var solution = AnalyzerConfiguration.CreateSolution(workspace, root, request.Assemblies);
        var projects = solution.Projects.ToDictionary(project => project.Name, StringComparer.Ordinal);
        long bytes = 0;
        foreach (var input in request.Assemblies)
        {
            cancellation.ThrowIfCancellationRequested();
            gaps.AddRange(input.CoverageGaps);
            try
            {
                var project = projects[input.Name];
                var compilation = await project.GetCompilationAsync(cancellation) ?? throw new InvalidDataException("compilation_context_unavailable");
                var analysisOptions = new CompilationWithAnalyzersOptions(
                    project.AnalyzerOptions,
                    (exception, analyzer, _) => failures.Enqueue(analyzer.GetType().FullName + ": " + exception.Message),
                    concurrentAnalysis: false,
                    logAnalyzerExecutionTime: false,
                    reportSuppressedDiagnostics: true
                );
                // Unity suppressors and C# style analyzers must see the very same diagnostics.
                var diagnostics = await compilation.WithAnalyzers(analyzers, analysisOptions).GetAllDiagnosticsAsync(cancellation);
                foreach (var diagnostic in diagnostics)
                {
                    if (diagnostic.IsSuppressed && !request.IncludeSuppressed)
                        continue;
                    if (diagnostic.Severity == DiagnosticSeverity.Hidden && !diagnostic.IsSuppressed)
                        continue;
                    var row = Row(diagnostic, compilation, root, input.Name, cancellation);
                    bytes += Encoding.UTF8.GetByteCount(JsonSerializer.Serialize(row));
                    if (primary.Count + context.Count >= 10000 || bytes > 8 * 1024 * 1024)
                    {
                        gaps.Add("diagnostic_limit");
                        break;
                    }
                    bool isSelected = diagnostic.Location.IsInSource && selected.Contains(diagnostic.Location.SourceTree!.FilePath);
                    (isSelected ? primary : context).Add(row);
                }
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception error)
            {
                gaps.Add(input.Name + ": " + error.Message);
            }
            if (gaps.Contains("diagnostic_limit"))
                break;
        }
        if (request.Assemblies.Length == 0)
            gaps.Add("assembly_context_unavailable");
        string environment = JsonSerializer.Serialize(
            new
            {
                Providers,
                inputs = request.Assemblies.Select(a => new
                {
                    a.Name,
                    a.LanguageVersion,
                    a.Defines,
                    a.AllowUnsafe,
                    a.Nullable,
                    a.RuleSeverities,
                    a.Configs,
                }),
            }
        );
        return new AnalysisResult
        {
            Complete = gaps.Count == 0 && failures.IsEmpty,
            SourceToken = request.SourceToken,
            EnvironmentSignature = Hash(environment),
            Diagnostics = primary.ToArray(),
            ContextDiagnostics = context.ToArray(),
            CoverageGaps = gaps.Distinct().ToArray(),
            AnalyzerErrors = failures.ToArray(),
            Providers = Providers,
        };
    }

    private static StringComparer PathComparer => OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal;

    internal static string Hash(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant();

    private static DiagnosticRow Row(Diagnostic diagnostic, Compilation compilation, string root, string assembly, CancellationToken cancellation)
    {
        string provider =
            diagnostic.Id.StartsWith("CS", StringComparison.Ordinal) ? "compiler"
            : diagnostic.Id.StartsWith("UNT", StringComparison.Ordinal) ? "Microsoft.Unity.Analyzers"
            : "Microsoft.CodeAnalysis.CSharp.CodeStyle";
        string? path = null,
            symbol = null;
        int? line = null,
            column = null;
        string anchor = diagnostic.Id + "\n" + diagnostic.GetMessage();
        if (diagnostic.Location.IsInSource)
        {
            var tree = diagnostic.Location.SourceTree!;
            var span = diagnostic.Location.GetLineSpan();
            path = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/');
            line = span.StartLinePosition.Line + 1;
            column = span.StartLinePosition.Character + 1;
            var semantic = compilation.GetSemanticModel(tree);
            symbol = semantic.GetEnclosingSymbol(diagnostic.Location.SourceSpan.Start, cancellation)?.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat);
            var node = tree.GetRoot(cancellation).FindNode(diagnostic.Location.SourceSpan, getInnermostNodeForTie: true);
            anchor += "\n" + symbol + "\n" + string.Join(" ", node.DescendantTokens().Select(t => t.Text));
        }
        return new DiagnosticRow
        {
            Id = diagnostic.Id,
            Provider = provider,
            ProviderVersion = Providers[provider],
            Category = diagnostic.Descriptor.Category,
            Severity = diagnostic.Severity.ToString().ToLowerInvariant(),
            Message = diagnostic.GetMessage(),
            HelpUri = diagnostic.Descriptor.HelpLinkUri,
            Path = path,
            Line = line,
            Column = column,
            Suppressed = diagnostic.IsSuppressed,
            Symbol = symbol,
            Anchor = Hash(anchor),
            Assembly = assembly,
            Remediation =
                diagnostic.Id == "UNT0008" ? "Use an explicit Unity null check before accessing the object; ?. bypasses UnityEngine.Object null semantics."
                : diagnostic.Id == "IDE0031"
                    ? "Consider null propagation when it preserves the type's null semantics. This suggestion does not apply to UnityEngine.Object."
                : "Review the rule documentation and preserve project semantics before changing the code.",
        };
    }
}
