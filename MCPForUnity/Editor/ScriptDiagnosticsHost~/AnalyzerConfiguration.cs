using System.Collections.Immutable;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.Text;

namespace UnityMcp.ScriptDiagnostics;

internal static class AnalyzerConfiguration
{
    internal static string FullPath(string root, string path)
    {
        string full = Path.GetFullPath(path, root);
        string relative = Path.GetRelativePath(root, full);
        if (relative == ".." || relative.StartsWith(".." + Path.DirectorySeparatorChar) || Path.IsPathRooted(relative))
            throw new InvalidDataException("source_path_outside_context");
        return full;
    }

    internal static Solution CreateSolution(AdhocWorkspace workspace, string root, AssemblyInput[] inputs)
    {
        var solution = workspace.CurrentSolution;
        var ids = new Dictionary<string, ProjectId>(StringComparer.Ordinal);
        foreach (var input in inputs)
        {
            var project = CreateProject(solution, root, input);
            ids.Add(input.Name, project.Id);
            solution = project.Solution;
        }
        foreach (var input in inputs)
        {
            if (input.ProjectReferences.Any(name => !ids.ContainsKey(name)))
                throw new InvalidDataException("source_dependency_unavailable");
            solution = solution.WithProjectReferences(ids[input.Name], input.ProjectReferences.Select(name => new ProjectReference(ids[name])));
        }
        return solution;
    }

    private static Project CreateProject(Solution solution, string root, AssemblyInput input)
    {
        if (
            !LanguageVersionFacts.TryParse(input.LanguageVersion, out var language)
            || language is LanguageVersion.Default or LanguageVersion.Latest or LanguageVersion.Preview
        )
            throw new InvalidDataException("explicit_language_version_required");
        if (!Enum.TryParse<NullableContextOptions>(input.Nullable, true, out var nullable))
            throw new InvalidDataException("nullable_option_unavailable");
        var severities = input.RuleSeverities.ToImmutableDictionary(pair => pair.Key, pair => Severity(pair.Value));
        var options = new CSharpCompilationOptions(
            OutputKind.DynamicallyLinkedLibrary,
            allowUnsafe: input.AllowUnsafe,
            nullableContextOptions: nullable,
            specificDiagnosticOptions: severities,
            reportSuppressedDiagnostics: true
        );
        var id = ProjectId.CreateNewId();
        solution = solution.AddProject(
            ProjectInfo.Create(
                id,
                VersionStamp.Create(),
                input.Name,
                input.Name,
                LanguageNames.CSharp,
                compilationOptions: options,
                parseOptions: new CSharpParseOptions(language, preprocessorSymbols: input.Defines)
            )
        );
        // References are PE metadata. Never load project assemblies or execute their initialization.
        solution = solution.WithProjectMetadataReferences(id, input.References.Select(path => MetadataReference.CreateFromFile(path)));
        foreach (var source in input.Sources)
            solution = solution.AddDocument(
                DocumentId.CreateNewId(id),
                Path.GetFileName(source.Path),
                SourceText.From(source.Text),
                filePath: FullPath(root, source.Path)
            );
        foreach (var config in input.Configs)
            solution = solution.AddAnalyzerConfigDocument(
                DocumentId.CreateNewId(id),
                Path.GetFileName(config.Path),
                SourceText.From(config.Text),
                filePath: FullPath(root, config.Path)
            );
        foreach (var additional in input.AdditionalFiles)
            solution = solution.AddAdditionalDocument(
                DocumentId.CreateNewId(id),
                Path.GetFileName(additional.Path),
                SourceText.From(additional.Text),
                filePath: FullPath(root, additional.Path)
            );
        return solution.GetProject(id)!;
    }

    private static ReportDiagnostic Severity(string severity) =>
        severity.ToLowerInvariant() switch
        {
            "error" => ReportDiagnostic.Error,
            "warning" or "warn" => ReportDiagnostic.Warn,
            "suggestion" or "info" => ReportDiagnostic.Info,
            "silent" or "hidden" => ReportDiagnostic.Hidden,
            "none" or "suppress" => ReportDiagnostic.Suppress,
            "default" => ReportDiagnostic.Default,
            _ => throw new InvalidDataException("unknown_rule_severity"),
        };
}
