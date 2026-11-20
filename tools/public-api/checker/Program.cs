using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.DotNet.ApiCompatibility;
using Microsoft.DotNet.ApiCompatibility.Logging;
using Microsoft.DotNet.ApiCompatibility.Rules;
using Microsoft.DotNet.ApiSymbolExtensions.Filtering;
using Microsoft.DotNet.ApiSymbolExtensions.Logging;

try
{
    return Check(args);
}
catch (Exception error)
{
    Console.Error.WriteLine($"Public API verification failed: {error.GetBaseException().Message}");
    return 2;
}

static int Check(string[] args)
{
    if (args is ["--run-consumer", var packagePath, var clientPath])
    {
        // Resolve the old client's dependencies from the candidate compilation only.
        var paths = References(packagePath).ToLookup(path => Path.GetFileNameWithoutExtension(path), StringComparer.OrdinalIgnoreCase);
        AssemblyLoadContext.Default.Resolving += (context, name) =>
            paths[name.Name!].FirstOrDefault() is string match ? context.LoadFromAssemblyPath(match) : null;
        bool editor = Assembly.LoadFrom(packagePath).GetName().Name == "MCPForUnity.Editor";
        var client = Assembly
            .LoadFrom(clientPath)
            .GetType(editor ? "IndependentExtension.EchoTool" : "IndependentExtension.ScenarioParticipant", throwOnError: true)!;
        string method = editor ? "ExerciseHelpers" : "ExerciseGameState";
        if (!Equals(true, client.GetMethod(method)!.Invoke(null, null)))
            throw new InvalidOperationException($"The old consumer failed {method}.");
        Console.WriteLine($"Executed the baseline-compiled {method} consumer against the candidate.");
        return 0;
    }

    if (args.Length is not (3 or 5))
        throw new ArgumentException("Expected surface.txt, baseline and candidate assemblies, and optionally their two Unity asmdefs.");

    var baseline = ReadAssembly(args[1]);
    var candidate = ReadAssembly(args[2]);
    if (args.Length == 5)
        ValidateAssemblyDefinitions(baseline.Name, args[3], args[4]);
    var surface = new ExtensionSurface(args[0], baseline);
    var log = new CompatibilityLog();
    var comparer = new ApiComparer(
        new RuleFactory(log, enableRuleCannotChangeParameterName: true),
        new ApiComparerSettings(symbolFilter: surface, withReferences: true)
    );
    var differences = comparer.GetDifferences(baseline, candidate).ToList();
    foreach (var difference in differences)
        Console.Error.WriteLine($"{difference.DiagnosticId}: {difference.Message}");
    Console.WriteLine($"{baseline.Name}: checked {surface.TypeCount} extension types, {differences.Count} differences");
    return differences.Count == 0 && !log.HasLoggedErrors ? 0 : 1;
}

static void ValidateAssemblyDefinitions(string assembly, string baselinePath, string candidatePath)
{
    using var previous = JsonDocument.Parse(File.ReadAllBytes(baselinePath));
    using var current = JsonDocument.Parse(File.ReadAllBytes(candidatePath));
    var baseline = previous.RootElement;
    var candidate = current.RootElement;
    if (baseline.GetProperty("name").GetString() != assembly || candidate.GetProperty("name").GetString() != assembly)
        throw new InvalidDataException($"Unity asmdef name differs from {assembly}");
    static bool AutoReferenced(JsonElement definition) => !definition.TryGetProperty("autoReferenced", out var flag) || flag.GetBoolean();
    static HashSet<string> Values(JsonElement definition, string key) =>
        definition.TryGetProperty(key, out var list)
            ? list.EnumerateArray().Select(item => item.GetString() ?? throw new InvalidDataException(key)).ToHashSet(StringComparer.Ordinal)
            : new(StringComparer.Ordinal);
    if (AutoReferenced(baseline) && !AutoReferenced(candidate))
        throw new InvalidDataException($"{assembly}: autoReferenced must preserve access from predefined Unity assemblies.");
    var wasIncluded = Values(baseline, "includePlatforms");
    var wasExcluded = Values(baseline, "excludePlatforms");
    var included = Values(candidate, "includePlatforms");
    var excluded = Values(candidate, "excludePlatforms");
    bool platformLost =
        wasIncluded.Count == 0
            ? included.Count != 0 || !excluded.IsSubsetOf(wasExcluded)
            : (included.Count != 0 && !wasIncluded.IsSubsetOf(included)) || wasIncluded.Overlaps(excluded);
    if (platformLost)
        throw new InvalidDataException($"{assembly}: includePlatforms/excludePlatforms narrowed Unity assembly availability.");
    if (!Values(candidate, "defineConstraints").IsSubsetOf(Values(baseline, "defineConstraints")))
        throw new InvalidDataException($"{assembly}: defineConstraints introduced a new or changed compilation requirement.");
    if (included.Count > 0 && excluded.Count > 0)
        throw new InvalidDataException($"{assembly}: Unity cannot combine includePlatforms and excludePlatforms.");
    if (GuidOf(baselinePath + ".meta") != GuidOf(candidatePath + ".meta"))
        throw new InvalidDataException($"{assembly}: asmdef GUID changed, breaking GUID-based references.");
    if (assembly == "MCPForUnity.Editor")
    {
        foreach (var file in new[] { "Helpers/McpJobStateStore.cs.meta", "Services/Server/ProcessTerminator.cs.meta" })
        {
            string original = GuidOf(Path.Combine(Path.GetDirectoryName(baselinePath)!, file));
            string root = Path.GetDirectoryName(candidatePath)!;
            string corresponding = Path.Combine(root, file);
            if (File.Exists(corresponding) && GuidOf(corresponding) == original)
                continue;
            if (!Directory.EnumerateFiles(root, "*.cs.meta", SearchOption.AllDirectories).Any(path => GuidOf(path) == original))
                throw new InvalidDataException($"{assembly}: legacy script GUID missing for {file}");
        }
    }
}

static string GuidOf(string metaPath)
{
    string? line = File.ReadLines(metaPath).SingleOrDefault(line => line.StartsWith("guid: ", StringComparison.Ordinal));
    if (line is null || !Guid.TryParseExact(line[6..], "N", out var guid))
        throw new InvalidDataException($"Invalid Unity GUID in {metaPath}");
    return guid.ToString("N");
}

static IAssemblySymbol ReadAssembly(string path)
{
    // These response files were written for Unity's own successful compilation.
    var dependencies = References(path).Select(reference => MetadataReference.CreateFromFile(reference));
    var assembly = MetadataReference.CreateFromFile(path);
    var compilation = CSharpCompilation.Create("ExtensionApiInspection", references: dependencies.Append(assembly));
    return (IAssemblySymbol)(compilation.GetAssemblyOrModuleSymbol(assembly) ?? throw new InvalidDataException(path));
}

static IEnumerable<string> References(string assembly) =>
    File.ReadLines(Path.ChangeExtension(assembly, ".rsp")).Where(line => line.StartsWith("-r:", StringComparison.Ordinal)).Select(line => line[3..].Trim('"'));

sealed class ExtensionSurface : ISymbolFilter
{
    private readonly Dictionary<string, HashSet<string>> types = new(StringComparer.Ordinal);
    private readonly AccessibilitySymbolFilter accessible = new(includeInternalSymbols: false);
    private readonly string assemblyName;
    public int TypeCount => types.Count;

    public ExtensionSurface(string file, IAssemblySymbol baseline)
    {
        assemblyName = baseline.Name;
        foreach (var line in File.ReadLines(file))
        {
            string[] parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2)
                throw new InvalidDataException($"Invalid public API selection: {line}");
            if (parts[0] != assemblyName)
                continue;
            var members = parts.Skip(2).ToHashSet(StringComparer.Ordinal);
            var type = baseline.GetTypeByMetadataName(parts[1]);
            if (type == null || !accessible.Include(type) || members.Any(member => !type.GetMembers(member).Any(accessible.Include)))
                throw new InvalidDataException($"Public baseline API missing: {line}");
            types.Add(parts[1], members);
        }
        if (types.Count == 0)
            throw new InvalidDataException($"No supported types selected for {assemblyName}");
    }

    public bool Include(ISymbol symbol)
    {
        if (!accessible.Include(symbol))
            return false;
        if (symbol is INamespaceSymbol or IAssemblySymbol || symbol.ContainingAssembly?.Name != assemblyName)
            return true;
        var owner = symbol as INamedTypeSymbol ?? symbol.ContainingType;
        return owner != null
            && types.TryGetValue(owner.ToDisplayString(), out var members)
            && (symbol is INamedTypeSymbol || members.Count == 0 || members.Contains((symbol as IMethodSymbol)?.AssociatedSymbol?.Name ?? symbol.Name));
    }
}

sealed class CompatibilityLog : ISuppressibleLog
{
    public bool HasLoggedErrors { get; private set; }
    public bool HasLoggedErrorSuppressions => false;

    public void LogError(string message)
    {
        HasLoggedErrors = true;
        Console.Error.WriteLine(message);
    }

    public void LogError(string code, string message) => LogError($"{code}: {message}");

    public bool LogError(Suppression suppression, string code, string message)
    {
        LogError(code, message);
        return true;
    }

    public void LogWarning(string message) => Console.Error.WriteLine(message);

    public void LogWarning(string code, string message) => LogWarning($"{code}: {message}");

    public bool LogWarning(Suppression suppression, string code, string message)
    {
        LogWarning(code, message);
        return true;
    }

    public void LogMessage(string message) => Console.WriteLine(message);

    public void LogMessage(MessageImportance importance, string message) => LogMessage(message);
}
