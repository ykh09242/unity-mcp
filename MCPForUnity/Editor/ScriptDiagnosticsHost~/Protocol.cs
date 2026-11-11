namespace UnityMcp.ScriptDiagnostics;

public sealed record SourceInput(string Path, string Text);

public sealed record AssemblyInput
{
    public string Name { get; init; } = "";
    public string LanguageVersion { get; init; } = "";
    public string[] Defines { get; init; } = [];
    public string[] References { get; init; } = [];
    public string[] ProjectReferences { get; init; } = [];
    public SourceInput[] Sources { get; init; } = [];
    public SourceInput[] Configs { get; init; } = [];
    public SourceInput[] AdditionalFiles { get; init; } = [];
    public bool AllowUnsafe { get; init; }
    public string Nullable { get; init; } = "disable";
    public Dictionary<string, string> RuleSeverities { get; init; } = [];
    public string[] CoverageGaps { get; init; } = [];
}

public sealed record AnalysisRequest
{
    public int Protocol { get; init; } = 1;
    public string ProjectRoot { get; init; } = "";
    public string SourceToken { get; init; } = "";
    public string[] SelectedPaths { get; init; } = [];
    public AssemblyInput[] Assemblies { get; init; } = [];
    public bool IncludeSuppressed { get; init; }
}

public sealed record DiagnosticRow
{
    public string Id { get; init; } = "";
    public string Provider { get; init; } = "";
    public string ProviderVersion { get; init; } = "";
    public string Category { get; init; } = "";
    public string Severity { get; init; } = "";
    public string Message { get; init; } = "";
    public string HelpUri { get; init; } = "";
    public string Remediation { get; init; } = "";
    public string? Path { get; init; }
    public int? Line { get; init; }
    public int? Column { get; init; }
    public bool Suppressed { get; init; }
    public string Anchor { get; init; } = "";
    public string? Symbol { get; init; }
    public string Assembly { get; init; } = "";
}

public sealed record AnalysisResult
{
    public int Protocol { get; init; } = 1;
    public bool Complete { get; init; }
    public string SourceToken { get; init; } = "";
    public string EnvironmentSignature { get; init; } = "";
    public DiagnosticRow[] Diagnostics { get; init; } = [];
    public DiagnosticRow[] ContextDiagnostics { get; init; } = [];
    public string[] CoverageGaps { get; init; } = [];
    public string[] AnalyzerErrors { get; init; } = [];
    public Dictionary<string, string> Providers { get; init; } = [];
}
