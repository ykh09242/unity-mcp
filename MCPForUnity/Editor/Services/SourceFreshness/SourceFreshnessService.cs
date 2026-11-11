using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.PackageManager;

namespace MCPForUnity.Editor.Services.SourceFreshness
{
    // Demand-driven observations only. Editor state reads expose the last observation and its age.
    internal static class SourceFreshnessService
    {
        private const string CheckpointKey = "MCPForUnity.SourceFreshness.V1";
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static readonly string ProjectRoot = Path.GetFullPath(UnityEngine.Application.dataPath + "/..");
        private static readonly SourceFreshnessLedger Ledger = new(
            SourceSnapshot.Hash(ProjectRoot.Replace('\\', '/')),
            CompilationJobTracker.Ledger.SessionId,
            SessionState.GetString(CheckpointKey, "")
        );
        private static Task<SourceSnapshot> _capture;
        private static readonly SemaphoreSlim CaptureGate = new(1, 1);

        internal static JObject Cached()
        {
            var value = Ledger.Observation.ToJson(Ledger.ProjectId, Ledger.SessionId);
            value["process_id"] = Process.GetCurrentProcess().Id;
            value["observation_in_progress"] = _capture != null && !_capture.IsCompleted;
            return value;
        }

        internal static void CompilationStarted()
        {
            Ledger.Invalidate("compilation_started");
            Save();
        }

        private static void Save()
        {
            try
            {
                string encoded = Ledger.Encode();
                SessionState.SetString(CheckpointKey, encoded);
                if (SessionState.GetString(CheckpointKey, "") != encoded)
                    Ledger.Invalidate("persistence_unavailable");
            }
            catch (Exception)
            {
                Ledger.Invalidate("persistence_unavailable");
            }
        }

        internal static async Task PrepareAsync(string compileId, CancellationToken cancellation)
        {
            var snapshot = await CaptureAsync(cancellation);
            cancellation.ThrowIfCancellationRequested();
            Ledger.BeginCompilation(compileId, snapshot, Domain);
            Save();
        }

        internal static async Task<SourceSnapshot> CaptureAsync(CancellationToken cancellation)
        {
            // Serialize actual work, including callers queued behind the same prior observation.
            await CaptureGate.WaitAsync(cancellation);
            try
            {
                cancellation.ThrowIfCancellationRequested();
                var scope = CaptureScope();
                _capture = SourceSnapshotCollector.CaptureAsync(scope, cancellation);
                var snapshot = await _capture;
                var after = CaptureScope();
                if (scope.Context != after.Context)
                    snapshot = new SourceSnapshot(
                        snapshot.ProjectId,
                        snapshot.SessionId,
                        snapshot.SourceDigest,
                        snapshot.ContextDigest,
                        false,
                        snapshot.Reasons.Concat(new[] { "context_changed_during_scan" }).ToArray(),
                        snapshot.FileCount,
                        snapshot.InputBytes,
                        unityVersion: snapshot.UnityVersion
                    );
                return snapshot;
            }
            finally
            {
                _capture = null;
                CaptureGate.Release();
            }
        }

        internal static async Task<JObject> ObserveAsync(CancellationToken cancellation)
        {
            if (EditorStateCache.GetActualIsCompiling() || EditorApplication.isUpdating || EditorStateCache.IsDomainReloadPending)
            {
                var busy = Cached();
                busy["status"] = "pending";
                busy["reason"] = "editor_transition_in_progress";
                return busy;
            }
            var snapshot = await CaptureAsync(cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (Ledger.CompileId == null)
                Ledger.ObserveSnapshot(snapshot);
            else
            {
                CompilationJobTracker.ObserveNativeCompletion();
                var query = CompilationJobTracker.Ledger.Query(Ledger.CompileId);
                var summary = CompilationJobTracker.Ledger.Summary();
                var observation = new CompileObservation
                {
                    CompileId = Ledger.CompileId,
                    Generation = (long?)query["generation"] ?? 0,
                    Outcome = (string)query["outcome"],
                    Reason = (string)query["reason"],
                    Complete = (bool?)query["collection_complete"] == true,
                    Attributed = (string)query["attribution"] == "observed_after_request",
                    Finished = (string)query["state"] == "finished",
                    AssemblyCount = (int?)query["assembly_count"] ?? 0,
                    ReloadObserved = Domain != Ledger.RequestDomain,
                    Assemblies = await CaptureIdentitiesAsync(cancellation),
                };
                if ((long?)summary["generation"] != observation.Generation)
                {
                    observation.Complete = false;
                    observation.Reason = "later_compilation_observed";
                    if ((string)summary["outcome"] == "failed")
                        observation.Outcome = "failed";
                }
                if (EditorStateCache.GetActualIsCompiling() || EditorApplication.isUpdating || EditorStateCache.IsDomainReloadPending)
                    observation.Finished = false;
                Ledger.ObserveCompilation(snapshot, observation);
            }
            Save();
            EditorStateCache.ForceUpdate("source_freshness_observed");
            return Cached();
        }

        internal static async Task<bool> CheckAdmissionAsync(string sourceToken, CancellationToken cancellation)
        {
            await ObserveAsync(cancellation);
            return !EditorStateCache.GetActualIsCompiling()
                && !EditorApplication.isUpdating
                && !EditorStateCache.IsDomainReloadPending
                && Ledger.CheckAdmission(sourceToken);
        }

        private static async Task<AssemblyIdentity[]> CaptureIdentitiesAsync(CancellationToken cancellation)
        {
            var assemblies = CompilationPipeline.GetAssemblies(AssembliesType.Editor);
            var loaded = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                try
                {
                    string name = assembly.GetName().Name;
                    // Multiple loaded identities with the same name are ambiguous.
                    loaded[name] = loaded.ContainsKey(name) ? null : assembly.ManifestModule.ModuleVersionId.ToString("N");
                }
                catch (Exception) { /* Dynamic or unloading assembly metadata is unavailable. */ }
            var inputs = assemblies.Select(a => (a.name, a.outputPath)).ToArray();
            return await Task.Run(
                () =>
                    inputs
                        .Take(513)
                        .Select(a =>
                        {
                            cancellation.ThrowIfCancellationRequested();
                            loaded.TryGetValue(a.name, out string mvid);
                            return new AssemblyIdentity(a.name, AssemblyIdentity.ReadModuleId(a.outputPath), mvid);
                        })
                        .ToArray(),
                cancellation
            );
        }

        internal static SourceScope CaptureScope()
        {
            var reasons = new List<string>();
            var assemblies = CompilationPipeline.GetAssemblies(AssembliesType.Editor).OrderBy(a => a.name, StringComparer.Ordinal).ToArray();
            var packages = UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages().OrderBy(p => p.name, StringComparer.Ordinal).ToArray();
            long entries = 0,
                characters = 0;
            bool WithinBudget(IEnumerable<string> values)
            {
                foreach (string value in values)
                {
                    entries++;
                    characters += value?.Length ?? 0;
                    if (entries > 100000 || characters > 4 * 1024 * 1024)
                        return false;
                }
                return true;
            }
            if (
                assemblies.Length > 512
                || packages.Length > 4096
                || assemblies.Any(a => !WithinBudget(a.sourceFiles.Concat(a.allReferences).Concat(a.defines)))
            )
            {
                var limited = new SourceScope(ProjectRoot, Ledger.SessionId, "metadata_limit");
                limited.Reasons.Add("metadata_limit");
                return limited;
            }
            var options = assemblies.ToDictionary(a => a.name, a => CaptureOptions(a.compilerOptions, reasons));
            var context = new JObject
            {
                ["unity"] = UnityEngine.Application.unityVersion,
                ["target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                ["packages"] = new JArray(
                    packages.Select(p => new JObject
                    {
                        ["name"] = p.name,
                        ["version"] = p.version,
                        ["source"] = p.source.ToString(),
                    })
                ),
                ["assemblies"] = new JArray(
                    assemblies.Select(a => new JObject
                    {
                        ["name"] = a.name,
                        ["sources"] = new JArray(a.sourceFiles.OrderBy(p => p, StringComparer.Ordinal)),
                        ["defines"] = new JArray(a.defines.OrderBy(p => p, StringComparer.Ordinal)),
                        ["references"] = new JArray(a.allReferences.OrderBy(p => p, StringComparer.Ordinal)),
                        ["options"] = options[a.name],
                        ["flags"] = a.flags.ToString(),
                    })
                ),
            };
            var scope = new SourceScope(ProjectRoot, Ledger.SessionId, context.ToString(Formatting.None))
            {
                UnityVersion = UnityEngine.Application.unityVersion,
            };
            scope.Reasons.AddRange(reasons.Distinct());
            foreach (var package in packages)
                if (package.source != PackageSource.BuiltIn && !string.IsNullOrEmpty(package.resolvedPath))
                    scope.Roots["Packages/" + package.name] = package.resolvedPath;
            foreach (var assembly in assemblies)
            {
                scope.Outputs[assembly.name] = assembly.outputPath;
                foreach (
                    string path in assembly
                        .sourceFiles.Concat(assembly.compiledAssemblyReferences)
                        .Concat(assembly.compilerOptions.ResponseFiles ?? Array.Empty<string>())
                )
                    scope.Inputs["compiler-input/" + path.Replace('\\', '/')] = path;
                // Reflect only Unity's known option DTO: properties differ across supported Editors.
                foreach (string name in new[] { "AnalyzerConfigPath", "RoslynAnalyzerRulesetPath", "RoslynAdditionalFilePaths", "RoslynAnalyzerDllPaths" })
                {
                    var option = options[assembly.name][name];
                    foreach (
                        string path in option?.Type == JTokenType.String ? new[] { (string)option }
                        : option is JArray list ? list.Values<string>()
                        : Array.Empty<string>()
                    )
                        if (!string.IsNullOrWhiteSpace(path))
                            scope.Inputs["compiler-option/" + path.Replace('\\', '/')] = path;
                }
            }
            if (assemblies.Length == 0)
                scope.Reasons.Add("assembly_context_unavailable");
            return scope;
        }

        internal static JObject CaptureOptions(ScriptCompilerOptions options, List<string> reasons)
        {
            var result = new JObject();
            foreach (
                string name in new[]
                {
                    "AllowUnsafeCode",
                    "ApiCompatibilityLevel",
                    "CodeOptimization",
                    "LanguageVersion",
                    "ResponseFiles",
                    "AdditionalCompilerArguments",
                    "AnalyzerConfigPath",
                    "RoslynAnalyzerRulesetPath",
                    "RoslynAdditionalFilePaths",
                    "RoslynAnalyzerDllPaths",
                }
            )
            {
                try
                {
                    var property = typeof(ScriptCompilerOptions).GetProperty(name);
                    if (property == null)
                    {
                        if (name == "LanguageVersion" || name == "AllowUnsafeCode")
                            reasons.Add("compiler_options_unavailable");
                        continue;
                    }
                    object value = property.GetValue(options);
                    result[name] = value == null ? JValue.CreateNull() : JToken.FromObject(value);
                }
                catch (Exception)
                {
                    reasons.Add("compiler_options_unavailable");
                }
            }
            return result;
        }
    }
}
