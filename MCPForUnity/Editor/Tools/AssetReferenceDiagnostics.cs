using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>AssetDatabase-only reference edges and bounded read-only move/delete impact plans.</summary>
    internal static class AssetReferenceDiagnostics
    {
        internal static bool IsAction(string action) =>
            action == "get_dependencies" || action == "get_reverse_references" || action == "preview_move" || action == "preview_delete";

        internal static bool HasOptions(JObject parameters) =>
            new[] { "scanRoot", "maxAssets", "maxWork", "referenceRevision" }.Any(name => parameters[name] != null);

        internal static object Handle(JObject parameters)
        {
            try
            {
                return new SuccessResponse("Observed bounded AssetDatabase references; no asset was changed.", Scan(parameters, false));
            }
            catch (ReferenceConflict failure)
            {
                return new ErrorResponse("reference_revision_conflict", new { reason = failure.Message });
            }
            catch (Exception failure)
            {
                return new ErrorResponse("reference_diagnostics_unavailable", new { reason = failure.Message });
            }
        }

        internal static async Task<object> HandleAsync(JObject parameters, CancellationToken cancellationToken)
        {
            try
            {
                return new SuccessResponse(
                    "Observed bounded AssetDatabase references; no asset was changed.",
                    await ScanCore(parameters, false, true, cancellationToken)
                );
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (ReferenceConflict failure)
            {
                return new ErrorResponse("reference_revision_conflict", new { reason = failure.Message });
            }
            catch (Exception failure)
            {
                return new ErrorResponse("reference_diagnostics_unavailable", new { reason = failure.Message });
            }
        }

        internal static ErrorResponse GuardMutation(JObject parameters)
        {
            try
            {
                Scan(parameters, true);
                return null;
            }
            catch (ReferenceConflict failure)
            {
                return new ErrorResponse("reference_revision_conflict", new { reason = failure.Message });
            }
            catch (Exception failure)
            {
                return new ErrorResponse("reference_diagnostics_unavailable", new { reason = failure.Message });
            }
        }

        private sealed class ReferenceConflict : Exception
        {
            internal ReferenceConflict(string message)
                : base(message) { }
        }

        private sealed class Node
        {
            internal string Path;
            internal Node Via;
            internal int Depth;
        }

        private sealed class Budget
        {
            internal int Maximum;
            internal int Used;
            internal bool Complete = true;
            internal string StopReason;

            internal bool Step()
            {
                if (Used >= Maximum)
                {
                    Stop("maxWork");
                    return false;
                }
                Used++;
                return true;
            }

            internal void Stop(string reason)
            {
                Complete = false;
                StopReason = reason;
            }
        }

        private static JObject Scan(JObject parameters, bool mutation) =>
            ScanCore(parameters, mutation, false, CancellationToken.None).GetAwaiter().GetResult();

        private static async Task<JObject> ScanCore(JObject parameters, bool mutation, bool background, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
#if !UNITY_EDITOR_WIN
            // Preserve no-yield file observation where read-sharing guarantees have not been verified.
            background = false;
#endif
            ReferenceDiagnosticUtility.Allowed(
                parameters,
                "action",
                "path",
                "destination",
                "scanRoot",
                "recursive",
                "maxAssets",
                "maxWork",
                "pageSize",
                "pageNumber",
                "referenceRevision"
            );
            string action = ReferenceDiagnosticUtility.Text(parameters, "action");
            string operation =
                action == "move" ? "preview_move"
                : action == "delete" ? "preview_delete"
                : action;
            bool preview = operation == "preview_move" || operation == "preview_delete";
            if (!(IsAction(operation) && (!mutation || preview)))
                throw new ArgumentException("Unsupported reference operation.");
            string source = ReferenceDiagnosticUtility.Path(parameters, "path");
            if (AssetDatabase.IsValidFolder(source))
                throw new ArgumentException("Reference diagnostics require a single imported asset file, not a folder.");
            string sourceGuid = AssetDatabase.AssetPathToGUID(source, AssetPathToGUIDOptions.OnlyExistingAssets);
            if (string.IsNullOrEmpty(sourceGuid))
                throw new ArgumentException("The source asset does not exist in the imported AssetDatabase.");
            source = AssetPathUtility.GetContainedAssetPath(AssetDatabase.GUIDToAssetPath(sourceGuid));
            string root = ReferenceDiagnosticUtility.Path(parameters, "scanRoot", true, "Assets");
            if (!AssetDatabase.IsValidFolder(root))
                throw new ArgumentException("scanRoot must identify an imported Assets folder.");
            string destination = operation == "preview_move" ? ReferenceDiagnosticUtility.Path(parameters, "destination") : null;
            if (destination == null && parameters["destination"] != null)
                throw new ArgumentException("Only move/preview_move accepts destination.");
            int maxAssets = ReferenceDiagnosticUtility.Integer(parameters, "maxAssets", 1024, 250000);
            var budget = new Budget { Maximum = ReferenceDiagnosticUtility.Integer(parameters, "maxWork", 10000, 4000000) };
            int size = ReferenceDiagnosticUtility.Integer(parameters, "pageSize", 50, 100);
            int page = ReferenceDiagnosticUtility.Integer(parameters, "pageNumber", 1, int.MaxValue);
            bool recursive = ReferenceDiagnosticUtility.Boolean(parameters, "recursive");
            string expected = ReferenceDiagnosticUtility.ExpectedRevision(parameters);
            if (mutation && expected == null)
                throw new ArgumentException("Guarded mutation requires an observed referenceRevision.");
            string sourceRevision = AssetDatabase.GetAssetDependencyHash(source).ToString();
            using (ReferenceFilePreparation prepared = background ? await ReferenceFilePreparation.CreateAsync(source, cancellationToken) : null)
            using (var observation = new ReferenceDiagnosticUtility.Observation())
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (
                    prepared != null
                    && (
                        AssetDatabase.AssetPathToGUID(source, AssetPathToGUIDOptions.OnlyExistingAssets) != sourceGuid
                        || AssetDatabase.GUIDToAssetPath(sourceGuid) != source
                        || AssetDatabase.GetAssetDependencyHash(source).ToString() != sourceRevision
                    )
                )
                    throw new ReferenceConflict("Source identity or imported revision changed during preparation.");
                foreach (
                    string value in new[]
                    {
                        operation,
                        source,
                        sourceGuid,
                        root,
                        destination,
                        recursive.ToString(),
                        maxAssets.ToString(CultureInfo.InvariantCulture),
                        budget.Maximum.ToString(CultureInfo.InvariantCulture),
                    }
                )
                    observation.Add(value);
                observation.Add(sourceRevision);
                observation.Add(prepared?.SourceHash ?? FileHash(source, 8L * 1024 * 1024 * 1024));
                observation.Add(prepared?.MetadataHash ?? FileHash(source + ".meta", 16L * 1024 * 1024));
                if (destination != null)
                {
                    observation.Add(AssetDatabase.AssetPathToGUID(destination, AssetPathToGUIDOptions.OnlyExistingAssets));
                    observation.Add(File.Exists(AssetPathUtility.GetFullAssetPath(destination)).ToString());
                    observation.Add(AssetDatabase.IsValidFolder(System.IO.Path.GetDirectoryName(destination).Replace('\\', '/')).ToString());
                }
                bool reverse = operation != "get_dependencies";
                var start = new Node { Path = source };
                var nodes = new Dictionary<string, Node>(StringComparer.Ordinal) { [source] = start };
                var results = new List<Node>();
                int inspected = 0;
                int excluded = 0;
                if (reverse)
                {
                    // Unity's enumeration is indivisible; refuse a large native result before processing it.
                    string[] guids = AssetDatabase.FindAssets("", new[] { root });
                    if (guids.Length > 65536)
                        throw new ArgumentException("Native scope enumeration exceeds 65536 GUIDs; narrow scanRoot.");
                    var candidates = new SortedSet<string>(StringComparer.Ordinal);
                    foreach (string guid in guids)
                    {
                        string candidate = AssetDatabase.GUIDToAssetPath(guid);
                        if (!Eligible(candidate))
                        {
                            excluded++;
                            continue;
                        }
                        if (!AssetDatabase.IsValidFolder(candidate))
                            candidates.Add(candidate);
                        if (candidates.Count > maxAssets)
                            throw new ArgumentException("Scope exceeds maxAssets; narrow scanRoot or increase maxAssets (maximum 4096).");
                    }
                    var incoming = new Dictionary<string, List<string>>(StringComparer.Ordinal);
                    foreach (string candidate in candidates)
                    {
                        if (!budget.Step())
                            break;
                        ObserveAsset(candidate, observation);
                        inspected++;
                        string[] dependencies = AssetDatabase.GetDependencies(candidate, false);
                        Array.Sort(dependencies, StringComparer.Ordinal);
                        foreach (string dependency in dependencies)
                        {
                            if (!budget.Step())
                                break;
                            if (dependency == candidate || !Eligible(dependency))
                                continue;
                            observation.Add(dependency);
                            if (!incoming.TryGetValue(dependency, out var owners))
                                incoming[dependency] = owners = new List<string>();
                            owners.Add(candidate);
                        }
                        if (!budget.Complete)
                            break;
                    }
                    var queue = new Queue<Node>();
                    queue.Enqueue(start);
                    while (queue.Count > 0 && budget.Complete)
                    {
                        Node node = queue.Dequeue();
                        if (!incoming.TryGetValue(node.Path, out var owners))
                            continue;
                        foreach (string owner in owners)
                        {
                            if (!budget.Step())
                                break;
                            if (nodes.ContainsKey(owner))
                                continue;
                            var next = new Node
                            {
                                Path = owner,
                                Via = node,
                                Depth = node.Depth + 1,
                            };
                            nodes.Add(owner, next);
                            results.Add(next);
                            if (recursive && next.Depth < 64)
                                queue.Enqueue(next);
                            else if (recursive)
                                budget.Stop("maxDepth64");
                        }
                    }
                }
                else
                {
                    var queue = new Queue<Node>();
                    queue.Enqueue(start);
                    while (queue.Count > 0 && budget.Complete)
                    {
                        Node node = queue.Dequeue();
                        if (!budget.Step())
                            break;
                        ObserveAsset(node.Path, observation);
                        inspected++;
                        string[] dependencies = AssetDatabase.GetDependencies(node.Path, false);
                        Array.Sort(dependencies, StringComparer.Ordinal);
                        foreach (string dependency in dependencies)
                        {
                            if (!budget.Step())
                                break;
                            if (dependency == node.Path)
                                continue;
                            if (!Eligible(dependency))
                            {
                                excluded++;
                                continue;
                            }
                            observation.Add(dependency);
                            if (nodes.ContainsKey(dependency))
                                continue;
                            if (nodes.Count >= maxAssets)
                            {
                                budget.Stop("maxAssets");
                                break;
                            }
                            var next = new Node
                            {
                                Path = dependency,
                                Via = node,
                                Depth = node.Depth + 1,
                            };
                            nodes.Add(dependency, next);
                            results.Add(next);
                            if (recursive && next.Depth < 64)
                                queue.Enqueue(next);
                            else if (recursive)
                                budget.Stop("maxDepth64");
                        }
                    }
                }
                observation.Add(budget.Complete.ToString());
                observation.Add(budget.StopReason);
                string revision = observation.Finish();
                if (expected != null && expected != revision)
                    throw new ReferenceConflict("Source, operation, destination, scope, budgets or imported reference graph changed since observation.");
                if (mutation && !budget.Complete)
                    throw new ReferenceConflict(
                        "Guarded mutation requires a complete scan within the declared candidate scope; increase budgets or narrow scanRoot and preview again."
                    );
                long offset = PaginationBounds.StartIndex(page, size);
                var entries = new JArray();
                foreach (Node node in results.OrderBy(node => node.Path, StringComparer.Ordinal).Skip((int)Math.Min(offset, results.Count)).Take(size))
                {
                    var chain = new List<string>();
                    for (Node current = node; current != null; current = current.Via)
                        chain.Add(current.Path);
                    if (!reverse)
                        chain.Reverse();
                    entries.Add(
                        new JObject
                        {
                            ["path"] = node.Path,
                            ["guid"] = AssetDatabase.AssetPathToGUID(node.Path),
                            ["direct"] = node.Depth == 1,
                            ["depth"] = node.Depth,
                            ["provenance"] = new JObject { ["api"] = "AssetDatabase.GetDependencies(recursive:false)", ["chain"] = new JArray(chain) },
                        }
                    );
                }
                var data = new JObject
                {
                    ["path"] = source,
                    ["guid"] = sourceGuid,
                    ["operation"] = operation,
                    ["destination"] = destination,
                    ["scanRoot"] = root,
                    ["recursive"] = recursive,
                    ["sourceRevision"] = sourceRevision,
                    ["referenceRevision"] = revision,
                    ["revisionMatches"] = expected == null ? JValue.CreateNull() : new JValue(true),
                    ["entries"] = entries,
                    ["totalEntries"] = budget.Complete ? new JValue(results.Count) : JValue.CreateNull(),
                    ["observedEntries"] = results.Count,
                    ["scanComplete"] = budget.Complete,
                    ["stopReason"] = budget.StopReason,
                    ["workUsed"] = budget.Used,
                    ["maxWork"] = budget.Maximum,
                    ["maxAssets"] = maxAssets,
                    ["inspectedAssets"] = inspected,
                    ["excludedEntries"] = excluded,
                    ["pageSize"] = size,
                    ["pageNumber"] = page,
                    ["hasMore"] = offset + entries.Count < results.Count,
                    ["mutationApplication"] = preview ? "guarded_move_delete" : null,
                    ["limitations"] =
                        "Imported AssetDatabase edges for Assets files only; reverse candidates are limited to scanRoot. Packages and built-ins are omitted. No comprehensive arbitrary string/custom-loader/Addressables discovery. Unsaved/unimported graph changes are not covered. Native FindAssets/GetDependencies calls are indivisible. Rechecks are observations, not an atomic filesystem transaction.",
                };
                ReferenceDiagnosticUtility.PageBytes(data);
                return data;
            }
        }

        private static bool Eligible(string path) => !string.IsNullOrEmpty(path) && path.StartsWith("Assets/", StringComparison.Ordinal) && path.Length <= 2048;

        private static void ObserveAsset(string path, ReferenceDiagnosticUtility.Observation observation)
        {
            path = AssetPathUtility.GetContainedAssetPath(path);
            observation.Add(path);
            observation.Add(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            observation.Add(AssetDatabase.GetAssetDependencyHash(path).ToString());
            // Imported graphs are authoritative; file observations also detect pending on-disk source changes.
            foreach (string file in new[] { path, path + ".meta" })
            {
                var info = new FileInfo(AssetPathUtility.GetFullAssetPath(file));
                observation.Add(info.Exists ? info.Length.ToString(CultureInfo.InvariantCulture) : "absent");
                observation.Add(info.Exists ? info.LastWriteTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture) : "absent");
            }
        }

        private static string FileHash(string path, long maximum)
        {
            using (var stream = File.OpenRead(AssetPathUtility.GetFullAssetPath(path)))
            {
                if (stream.Length > maximum)
                    throw new ArgumentException("Source/revision file exceeds its 8 GiB asset or 16 MiB metadata hash budget.");
                using (var hash = SHA256.Create())
                    return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
