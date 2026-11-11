using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Xml;
using System.Xml.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.SourceFreshness;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.Compilation;
using UnityEditor.PackageManager;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    internal static class ScriptCompilationContext
    {
        private sealed class AssemblyContext
        {
            internal string Name;
            internal string[] Sources,
                References,
                ProjectReferences,
                Defines;
            internal JObject Options;
            internal readonly List<string> Gaps = new();
        }

        internal static async Task<JObject> CaptureAsync(ScriptSelection selection, SourceSnapshot snapshot, bool suppressed, CancellationToken cancellation)
        {
            string root = ScriptAnalysisHost.ProjectRoot;
            var packages = PackageInfo
                .GetAllRegisteredPackages()
                .Where(p => p.source != PackageSource.BuiltIn)
                .ToDictionary(p => p.assetPath, p => p.resolvedPath, StringComparer.Ordinal);
            string Logical(string input)
            {
                string path = input.Replace('\\', '/');
                if (!Path.IsPathRooted(path))
                    return ScriptSelection.Canonical(path);
                string full = Path.GetFullPath(path).Replace('\\', '/');
                foreach (var package in packages)
                {
                    string physical = Path.GetFullPath(package.Value).Replace('\\', '/').TrimEnd('/') + "/";
                    if (full.StartsWith(physical, StringComparison.OrdinalIgnoreCase))
                        return ScriptSelection.Canonical(package.Key + "/" + full.Substring(physical.Length));
                }
                string prefix = root.Replace('\\', '/').TrimEnd('/') + "/";
                if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("source_outside_admitted_roots");
                return ScriptSelection.Canonical(full.Substring(prefix.Length));
            }
            string Physical(string path)
            {
                foreach (var package in packages)
                {
                    if (path == package.Key)
                    {
                        string physicalRoot = Path.GetFullPath(package.Value);
                        return SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(physicalRoot), physicalRoot);
                    }
                    if (path.StartsWith(package.Key + "/", StringComparison.Ordinal))
                        return AssetPathUtility.ResolvePackageFilePath(path, package.Key, package.Value);
                }
                return SafePathUtility.ResolveWithinRoot(root, path);
            }
            var native = CompilationPipeline.GetAssemblies(AssembliesType.Editor);
            if (native.Length > 512 || packages.Count > 4096)
                throw new InvalidDataException("assembly_context_limit");
            var known = new Dictionary<string, AssemblyContext>(StringComparer.Ordinal);
            var allSources = new HashSet<string>(StringComparer.Ordinal);
            foreach (var assembly in native)
            {
                var context = new AssemblyContext { Name = assembly.name };
                context.Sources = assembly.sourceFiles.Select(Logical).Distinct().OrderBy(p => p, StringComparer.Ordinal).ToArray();
                allSources.UnionWith(context.Sources);
                if (allSources.Count > 100000 || assembly.compiledAssemblyReferences.Length > 4096 || assembly.defines.Length > 4096)
                    throw new InvalidDataException("assembly_context_limit");
                context.References = assembly.compiledAssemblyReferences.OrderBy(p => p, StringComparer.Ordinal).ToArray();
                context.ProjectReferences = assembly.assemblyReferences.Select(a => a.name).OrderBy(n => n, StringComparer.Ordinal).ToArray();
                context.Defines = assembly.defines.OrderBy(d => d, StringComparer.Ordinal).ToArray();
                context.Options = SourceFreshnessService.CaptureOptions(assembly.compilerOptions, context.Gaps);
                known.Add(context.Name, context);
            }
            var closure = new HashSet<string>(StringComparer.Ordinal);
            void Include(string name)
            {
                if (!closure.Add(name))
                    return;
                if (!known.TryGetValue(name, out var assembly))
                    throw new InvalidDataException("source_dependency_unavailable");
                foreach (string dependency in assembly.ProjectReferences)
                    Include(dependency);
            }
            foreach (string path in selection.Paths)
            {
                var owners = known.Values.Where(a => a.Sources.Contains(path)).ToArray();
                if (owners.Length != 1)
                    throw new InvalidDataException("source_not_in_imported_assembly_inventory: " + path);
                Include(owners[0].Name);
            }
            // Main-thread Unity metadata is detached before bounded file IO on a worker.
            var selected = selection.Files.ToDictionary(f => f.Path, StringComparer.Ordinal);
            var unimported = new List<string>();
            var request = await Task.Run(
                () =>
                {
                    long total = 0;
                    int count = 0;
                    var content = new Dictionary<string, byte[]>(StringComparer.Ordinal);
                    byte[] Read(string physical, int maximum = 16 * 1024 * 1024)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        physical = SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(physical), physical);
                        if (content.TryGetValue(physical, out var cached))
                            return cached;
                        byte[] bytes = ScriptSelection.Read(physical, maximum);
                        total += bytes.Length;
                        if (++count > 100000 || total > 256L * 1024 * 1024)
                            throw new InvalidDataException("analysis_context_limit");
                        content.Add(physical, bytes);
                        return bytes;
                    }
                    JObject Document(string logical)
                    {
                        byte[] bytes = Read(Physical(logical));
                        if (selected.TryGetValue(logical, out var file) && ScriptSelection.Digest(bytes) != file.Hash)
                            throw new InvalidDataException("selected_source_changed");
                        return new JObject { ["path"] = logical, ["text"] = ScriptSelection.Text(bytes) };
                    }
                    // Configs supplied by Unity are read as data; no project build, generators, or analyzer DLLs execute.
                    string ConfigPath(string input)
                    {
                        string full = Path.GetFullPath(Path.IsPathRooted(input) ? input : Path.Combine(root, input));
                        string prefix = root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                        string logical = full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) ? full.Substring(prefix.Length).Replace('\\', '/') : null;
                        foreach (var package in packages)
                        {
                            string start = Path.GetFullPath(package.Value).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                            if (full.StartsWith(start, StringComparison.OrdinalIgnoreCase))
                                logical = package.Key + "/" + full.Substring(start.Length).Replace('\\', '/');
                        }
                        if (
                            logical == null
                            || logical.IndexOf("/Resources/GameData/", StringComparison.OrdinalIgnoreCase) >= 0
                            || logical.Split('/').Any(part => part.StartsWith(".env", StringComparison.OrdinalIgnoreCase))
                        )
                            throw new InvalidDataException("compiler_config_outside_admitted_roots");
                        return logical;
                    }
                    var inputs = new JArray();
                    foreach (string name in closure.OrderBy(n => n, StringComparer.Ordinal))
                    {
                        var assembly = known[name];
                        var gaps = new List<string>(assembly.Gaps);
                        var configs = new SortedSet<string>(StringComparer.Ordinal);
                        var sourceDocs = new JArray();
                        foreach (string path in assembly.Sources)
                        {
                            sourceDocs.Add(Document(path));
                            string directory = Path.GetDirectoryName(path)?.Replace('\\', '/');
                            while (directory != null)
                            {
                                string config = directory.Length == 0 ? ".editorconfig" : directory + "/.editorconfig";
                                if (File.Exists(Physical(config)))
                                    configs.Add(config);
                                directory = directory.Length == 0 ? null : (Path.GetDirectoryName(directory)?.Replace('\\', '/') ?? "");
                            }
                        }
                        string global = (string)assembly.Options["AnalyzerConfigPath"];
                        if (!string.IsNullOrEmpty(global))
                            configs.Add(ConfigPath(global));
                        var rules = new JObject();
                        string ruleset = (string)assembly.Options["RoslynAnalyzerRulesetPath"];
                        if (!string.IsNullOrEmpty(ruleset))
                        {
                            using var stream = new MemoryStream(Read(Physical(ConfigPath(ruleset)), 1024 * 1024));
                            using var reader = XmlReader.Create(stream, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null });
                            var document = XDocument.Load(reader);
                            if (document.Descendants("Include").Any() || document.Descendants("IncludeAll").Any())
                                gaps.Add("ruleset_include_not_supported");
                            foreach (var rule in document.Descendants("Rule"))
                            {
                                string id = (string)rule.Attribute("Id"),
                                    action = (string)rule.Attribute("Action");
                                if (!string.IsNullOrEmpty(id) && action != null)
                                    rules[id] = action.ToLowerInvariant();
                            }
                        }
                        string language = (string)assembly.Options["LanguageVersion"];
                        string nullable = "disable";
                        bool unsafeCode = (bool?)assembly.Options["AllowUnsafeCode"] ?? false;
                        var defines = new SortedSet<string>(assembly.Defines, StringComparer.Ordinal);
                        var arguments = new List<string>(assembly.Options["AdditionalCompilerArguments"]?.Values<string>() ?? Array.Empty<string>());
                        foreach (string response in assembly.Options["ResponseFiles"]?.Values<string>() ?? Array.Empty<string>())
                        {
                            string contents = ScriptSelection.Text(Read(Physical(ConfigPath(response)), 1024 * 1024));
                            foreach (string line in contents.Split('\n'))
                                if (!line.TrimStart().StartsWith("#", StringComparison.Ordinal))
                                    arguments.AddRange(
                                        Regex
                                            .Matches(line, "(?:[^\\s\"]|\"[^\"]*\")+", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100))
                                            .Cast<Match>()
                                            .Select(m => m.Value)
                                    );
                        }
                        foreach (string argument in arguments)
                        {
                            string option = argument.Trim().TrimStart('-', '/');
                            int colon = option.IndexOf(':');
                            string key = (colon < 0 ? option : option.Substring(0, colon)).ToLowerInvariant();
                            string value = colon < 0 ? "" : option.Substring(colon + 1).Trim('"');
                            switch (key)
                            {
                                case "nullable":
                                    nullable = value;
                                    break;
                                case "langversion":
                                    language = value;
                                    break;
                                case "unsafe":
                                case "unsafe+":
                                    unsafeCode = true;
                                    break;
                                case "unsafe-":
                                    unsafeCode = false;
                                    break;
                                case "define":
                                case "d":
                                    foreach (string define in value.Split(';', ','))
                                        defines.Add(define);
                                    break;
                                case "nowarn":
                                    foreach (string id in value.Split(';', ','))
                                        rules[RuleId(id)] = "suppress";
                                    break;
                                case "warnaserror":
                                case "warnaserror+":
                                    if (value.Length == 0)
                                        gaps.Add("global_warnaserror_not_supported");
                                    else
                                        foreach (string id in value.Split(';', ','))
                                            rules[RuleId(id)] = "error";
                                    break;
                                case "nologo":
                                case "debug":
                                case "debug+":
                                case "optimize":
                                case "optimize+":
                                case "optimize-":
                                case "deterministic":
                                case "deterministic+":
                                    break;
                                default:
                                    gaps.Add("compiler_option_not_supported:" + key);
                                    break;
                            }
                        }
                        foreach (string reference in assembly.References)
                        {
                            string full = Path.GetFullPath(reference);
                            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(full), full);
                            if (!File.Exists(full))
                                gaps.Add("metadata_reference_unavailable");
                        }
                        if (assembly.Options["RoslynAnalyzerDllPaths"] is JArray analyzers && analyzers.Count != 0)
                            gaps.Add("project_analyzers_and_generators_not_executed");
                        var additional = new JArray();
                        foreach (string file in assembly.Options["RoslynAdditionalFilePaths"]?.Values<string>() ?? Array.Empty<string>())
                            additional.Add(Document(ConfigPath(file)));
                        inputs.Add(
                            new JObject
                            {
                                ["name"] = name,
                                ["language_version"] = language,
                                ["defines"] = new JArray(defines),
                                ["references"] = new JArray(assembly.References.Select(Path.GetFullPath)),
                                ["project_references"] = new JArray(assembly.ProjectReferences),
                                ["sources"] = sourceDocs,
                                ["configs"] = new JArray(configs.Select(Document)),
                                ["additional_files"] = additional,
                                ["allow_unsafe"] = unsafeCode,
                                ["nullable"] = nullable,
                                ["rule_severities"] = rules,
                                ["coverage_gaps"] = new JArray(gaps.Distinct()),
                            }
                        );
                    }
                    return new JObject
                    {
                        ["protocol"] = 1,
                        ["project_root"] = root,
                        ["source_token"] = snapshot.Token,
                        ["selected_paths"] = new JArray(selection.Paths),
                        ["assemblies"] = inputs,
                        ["include_suppressed"] = suppressed,
                    };
                },
                cancellation
            );
            // Inventory only names, excluding Unity-ignored and prohibited directories before entry.
            // New context files must not silently disappear from partial types or dependencies.
            await Task.Run(
                () =>
                {
                    int entries = 0;
                    void Walk(string logical)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        string physical = Physical(logical);
                        if (!Directory.Exists(physical))
                            return;
                        foreach (string child in Directory.EnumerateFileSystemEntries(physical))
                        {
                            if (++entries > 400000)
                                throw new InvalidDataException("analysis_inventory_limit");
                            string name = Path.GetFileName(child),
                                path = logical + "/" + name;
                            if (
                                name.StartsWith(".", StringComparison.Ordinal)
                                || name.EndsWith("~", StringComparison.Ordinal)
                                || name == "bin"
                                || name == "obj"
                                || name == "node_modules"
                                || path.IndexOf("/Resources/GameData", StringComparison.OrdinalIgnoreCase) >= 0
                            )
                                continue;
                            if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0)
                                throw new InvalidDataException("linked_source_inventory");
                            if (Directory.Exists(child))
                                Walk(path);
                            else if (path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase) && !allSources.Contains(path))
                            {
                                if (unimported.Count >= 100000)
                                    throw new InvalidDataException("analysis_inventory_limit");
                                unimported.Add(path);
                            }
                        }
                    }
                    Walk("Assets");
                    foreach (var package in packages)
                        if (closure.Any(name => known[name].Sources.Any(path => path.StartsWith(package.Key + "/", StringComparison.Ordinal))))
                            Walk(package.Key);
                },
                cancellation
            );
            var membershipGaps = new List<string>();
            foreach (string path in unimported)
            {
                cancellation.ThrowIfCancellationRequested();
                string owner = CompilationPipeline.GetAssemblyNameFromScriptPath(path);
                if (owner != null && owner.EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
                    owner = owner.Substring(0, owner.Length - 4);
                if (string.IsNullOrEmpty(owner) || closure.Contains(owner))
                    membershipGaps.Add("unimported_context_source:" + path);
                if (membershipGaps.Count >= 256)
                {
                    membershipGaps.Add("unimported_context_source_limit");
                    break;
                }
            }
            foreach (var assembly in request["assemblies"])
            foreach (string gap in membershipGaps)
                ((JArray)assembly["coverage_gaps"]).Add(gap);
            return request;
        }

        private static string RuleId(string value) => int.TryParse(value, out int number) ? "CS" + number.ToString("D4") : value;
    }
}
