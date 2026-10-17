using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Bounded label operations, with observed revisions and sequential, non-atomic writes.</summary>
    internal static class AssetLabels
    {
        private const int MaxTargets = 32;
        private const int MaxLabels = 128;
        private const int MaxLabelUnits = 256;
        private const int MaxMetaBytes = 1024 * 1024;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly Regex RevisionPattern = new Regex("\\Amcp-al-v1_[0-9a-f]{32}_[0-9a-f]{64}_[0-9a-f]{64}\\z", RegexOptions.CultureInvariant);

        // Inert private test seams. The ordinary serialized fixture restores each in finally/teardown.
        private static Action<string> OperationObserved = null;
        private static Action<int, string> BeforeCommit = null;
        private static Action<string> AfterSetLabels = null;

        internal static bool IsAction(string action) => action == "get_labels" || action == "set_labels" || action == "add_labels" || action == "remove_labels";

        internal static object Handle(JObject parameters, Func<string[], ErrorResponse> requireScriptConsent)
        {
            var rows = new JArray();
            bool enteredWrite = false;
            int current = -1;
            try
            {
                var request = Parse(parameters);
                bool mutation = request.Action != "get_labels";
                var budget = new Budget();
                var paths = new HashSet<string>(StringComparer.Ordinal);
                var guids = new HashSet<string>(StringComparer.Ordinal);
                foreach (Target target in request.Targets)
                    rows.Add(new JObject { ["path"] = target.Input, ["status"] = "not_attempted" });

                // Complete resolution/observations precede consent and every SetLabels call.
                for (int i = 0; i < request.Targets.Count; i++)
                {
                    current = i;
                    Target target = request.Targets[i];
                    Resolve(target, mutation);
                    budget.PathBytes += Bytes(target.Path);
                    if (budget.PathBytes > 32768)
                        Fail("asset_labels_limit_exceeded", "Canonical target paths exceed 32 KiB.");
                    if (!paths.Add(target.Path))
                        Fail("invalid_asset_labels", "Duplicate canonical target.");
                    target.Initial = Observe(target, budget);
                    if (!guids.Add(target.Initial.Guid))
                        Fail("invalid_asset_labels", "Duplicate asset GUID.");
                    if (mutation)
                        CheckRevision(target.Revision, target.Initial);
                    target.Planned = mutation ? Plan(request.Action, target.Initial.Labels, request.Labels) : target.Initial.Labels;
                    if (mutation)
                        budget.Labels(target.Planned);
                    SetObservation((JObject)rows[i], target.Initial, mutation ? "not_attempted" : "observed");
                    ((JObject)rows[i])["observation"] = mutation ? "before_attempt" : "read";
                }
                if (!mutation)
                    return new SuccessResponse("Read asset labels.", Data(rows));
                var consent = requireScriptConsent(request.Targets.Select(target => target.Path).ToArray());
                if (consent != null)
                    return consent;

                for (int i = 0; i < request.Targets.Count; i++)
                {
                    current = i;
                    Target target = request.Targets[i];
                    BeforeCommit?.Invoke(i, target.Path);
                    Observation latest = Observe(target, budget);
                    CheckRevision(target.Revision, latest);
                    EnsureEditable(target);
                    if (latest.Labels.SequenceEqual(target.Planned, StringComparer.Ordinal))
                    {
                        SetObservation((JObject)rows[i], latest, "unchanged");
                        ((JObject)rows[i])["observation"] = "pre_write_recheck";
                        continue;
                    }
                    // Invalidate before the API attempt: even an exception can leave changed labels.
                    AssetSearchSnapshots.Invalidate();
                    enteredWrite = true;
                    OperationObserved?.Invoke("set_labels");
                    AssetDatabase.SetLabels(target.Asset, target.Planned);
                    AfterSetLabels?.Invoke(target.Path);
                    Observation after = Observe(target, budget);
                    if (after.Guid != latest.Guid || !after.Labels.SequenceEqual(target.Planned, StringComparer.Ordinal))
                        Fail("asset_label_outcome_unknown", "SetLabels readback did not confirm the requested identity and exact label set.");
                    SetObservation((JObject)rows[i], after, "changed");
                    ((JObject)rows[i])["observation"] = "readback";
                    enteredWrite = false;
                }
                return new SuccessResponse("Applied sequential asset label operation.", Data(rows));
            }
            catch (LabelFailure failure)
            {
                string code = enteredWrite ? "asset_label_outcome_unknown" : failure.Code;
                string status =
                    enteredWrite ? "outcome_unknown"
                    : failure.Code == "asset_label_conflict" ? "conflict"
                    : "refused";
                if (current >= 0 && current < rows.Count)
                {
                    ((JObject)rows[current])["status"] = status;
                    if (failure.Current != null)
                    {
                        SetObservation((JObject)rows[current], failure.Current, status);
                        ((JObject)rows[current])["observation"] = "conflict_recheck";
                    }
                }
                return new ErrorResponse(code, Data(rows, failure.Message));
            }
            catch (Exception)
            {
                // Unity/provider/filesystem exceptions are a command boundary, never a rollback claim.
                if (current >= 0 && current < rows.Count)
                    ((JObject)rows[current])["status"] = enteredWrite ? "outcome_unknown" : "refused";
                return new ErrorResponse(
                    enteredWrite ? "asset_label_outcome_unknown" : "asset_labels_unavailable",
                    Data(rows, "Unable to complete a bounded label observation or operation.")
                );
            }
        }

        private static JObject Data(JArray rows, string reason = null) =>
            new JObject
            {
                ["results"] = rows,
                ["partial"] =
                    rows.OfType<JObject>().Any(row => (string)row["status"] == "changed")
                    && rows.OfType<JObject>().Any(row => (string)row["status"] != "changed" && (string)row["status"] != "unchanged"),
                ["atomic"] = false,
                ["reason"] = reason,
            };

        private static Request Parse(JObject parameters)
        {
            var allowed = new HashSet<string>(new[] { "action", "path", "labelRevision", "labelTargets", "labels" }, StringComparer.Ordinal);
            if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                Fail("invalid_asset_labels", "Unknown or irrelevant label option.");
            string action = String(parameters["action"]);
            if (!IsAction(action))
                Fail("invalid_asset_labels", "Unknown label action.");
            bool mutation = action != "get_labels";
            var request = new Request { Action = action };
            JToken targetsToken = parameters["labelTargets"];
            if (targetsToken != null)
            {
                if (parameters["path"] != null || parameters["labelRevision"] != null)
                    Fail("invalid_asset_labels", "Use either a single path/revision or labelTargets.");
                if (!(targetsToken is JArray targets) || targets.Count < 1 || targets.Count > MaxTargets)
                    Fail("invalid_asset_labels", "Expected 1-32 target rows.");
                foreach (JToken token in (JArray)targetsToken)
                {
                    if (!(token is JObject row) || row.Properties().Any(property => property.Name != "path" && property.Name != "revision"))
                        Fail("invalid_asset_labels", "Targets are closed path/revision objects.");
                    var targetRow = (JObject)token;
                    if (!mutation && targetRow["revision"] != null)
                        Fail("invalid_asset_labels", "get_labels does not accept revisions.");
                    request.Targets.Add(new Target { Input = String(targetRow["path"]), Revision = mutation ? String(targetRow["revision"]) : null });
                }
            }
            else
            {
                if (!mutation && parameters["labelRevision"] != null)
                    Fail("invalid_asset_labels", "get_labels does not accept revisions.");
                request.Targets.Add(new Target { Input = String(parameters["path"]), Revision = mutation ? String(parameters["labelRevision"]) : null });
            }
            var literalPaths = new HashSet<string>(StringComparer.Ordinal);
            long pathBytes = 0;
            foreach (Target target in request.Targets)
            {
                if (string.IsNullOrWhiteSpace(target.Input) || target.Input.Trim() != target.Input || target.Input.Any(char.IsControl))
                    Fail("invalid_asset_labels", "Invalid target path string.");
                pathBytes += Bytes(target.Input);
                if (pathBytes > 32768 || !literalPaths.Add(target.Input))
                    Fail("invalid_asset_labels", "Target path budget or duplicate target.");
                if (mutation && !RevisionPattern.IsMatch(target.Revision))
                    Fail("invalid_asset_labels", "Each mutation requires an observed label revision.");
            }
            if (mutation)
            {
                if (!(parameters["labels"] is JArray labels) || labels.Count > MaxLabels)
                    Fail("invalid_asset_labels", "labels must contain at most 128 strings.");
                var requested = new List<string>();
                long labelBytes = 0;
                foreach (JToken token in (JArray)parameters["labels"])
                {
                    string value = String(token);
                    if (string.IsNullOrWhiteSpace(value) || value.Trim() != value || value.Length > MaxLabelUnits || value.Any(char.IsControl))
                        Fail("invalid_asset_labels", "Invalid label string.");
                    labelBytes += Bytes(value);
                    if (labelBytes > 16384)
                        Fail("invalid_asset_labels", "Requested labels exceed 16 KiB.");
                    requested.Add(value);
                }
                request.Labels = Canonical(requested);
            }
            else if (parameters["labels"] != null)
                Fail("invalid_asset_labels", "get_labels does not accept labels.");
            return request;
        }

        private static string String(JToken value)
        {
            if (value == null || value.Type != JTokenType.String)
                Fail("invalid_asset_labels", "Expected a string.");
            return value.Value<string>();
        }

        private static int Bytes(string value)
        {
            try
            {
                return Utf8.GetByteCount(value);
            }
            catch (EncoderFallbackException)
            {
                Fail("invalid_asset_labels", "Invalid Unicode string.");
                return 0;
            }
        }

        private static void Resolve(Target target, bool mutation)
        {
            target.Path = AssetPathUtility.GetAssetReferencePath(target.Input, allowPackages: true);
            if (string.IsNullOrEmpty(target.Path) || target.Path == "Assets" || target.Path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                Fail("invalid_asset_labels", "Expected an imported asset or folder, not a root or meta file.");
            target.ReadOnly = target.Path.StartsWith("Packages/", StringComparison.Ordinal);
            if (mutation && target.ReadOnly)
                Fail("asset_labels_read_only", "Package label mutations are refused, including no-ops.");
            if (!target.ReadOnly)
            {
                target.Path = AssetPathUtility.GetContainedAssetPath(target.Path);
                target.Meta = SafePathUtility.ResolveWithinRoot(Application.dataPath, target.Path.Substring(7) + ".meta");
            }
            OperationObserved?.Invoke("load_asset");
            target.Asset = AssetDatabase.LoadMainAssetAtPath(target.Path);
            if (target.Asset == null)
                Fail("asset_labels_unavailable", "The target is not an imported main asset.");
            if (mutation)
                EnsureEditable(target);
        }

        private static void EnsureEditable(Target target)
        {
            // Recheck lexical/physical containment and the meta itself; never checkout or alter attributes.
            target.Meta = SafePathUtility.ResolveWithinRoot(Application.dataPath, target.Path.Substring(7) + ".meta");
            if (
                !File.Exists(target.Meta)
                || (File.GetAttributes(target.Meta) & FileAttributes.ReadOnly) != 0
                || !AssetDatabase.IsOpenForEdit(target.Path + ".meta")
            )
                Fail("asset_labels_read_only", "Asset metadata is not open for edit.");
        }

        private static Observation Observe(Target target, Budget budget)
        {
            OperationObserved?.Invoke("observe_labels");
            string guid = AssetDatabase.AssetPathToGUID(target.Path);
            if (string.IsNullOrEmpty(guid) || !Regex.IsMatch(guid, "\\A[0-9a-f]{32}\\z", RegexOptions.CultureInvariant))
                Fail("asset_labels_unavailable", "The imported target has no supported GUID.");
            string[] native = AssetDatabase.GetLabels(target.Asset);
            if (native == null || native.Length > MaxLabels || native.Any(value => value == null || value.Length > MaxLabelUnits))
                Fail("asset_labels_limit_exceeded", "Observed labels exceed bounded count or string size.");
            string[] labels = Canonical(native);
            budget.Labels(labels);
            string metaDigest = null;
            if (!target.ReadOnly)
            {
                target.Meta = SafePathUtility.ResolveWithinRoot(Application.dataPath, target.Path.Substring(7) + ".meta");
                OperationObserved?.Invoke("read_meta");
                using (var stream = new FileStream(target.Meta, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    long length = stream.Length;
                    if (length > MaxMetaBytes || length > 4 * MaxMetaBytes - budget.MetaBytes)
                        Fail("asset_labels_limit_exceeded", "Metadata exceeds 1 MiB per file or 4 MiB cumulative reads.");
                    using (var bytes = new MemoryStream())
                    {
                        var buffer = new byte[8192];
                        while (bytes.Length < length)
                        {
                            int count = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, length - bytes.Length));
                            if (count == 0)
                                Fail("asset_labels_unavailable", "Metadata changed during observation.");
                            budget.MetaBytes += count;
                            bytes.Write(buffer, 0, count);
                        }
                        if (stream.Length != length)
                            Fail("asset_labels_unavailable", "Metadata changed during observation.");
                        metaDigest = Digest(bytes.ToArray());
                    }
                }
            }
            string labelsDigest = Digest(Utf8.GetBytes(JsonConvert.SerializeObject(labels, Formatting.None)));
            return new Observation
            {
                Path = target.Path,
                Guid = guid,
                Labels = labels,
                ReadOnly = target.ReadOnly,
                MetaDigest = metaDigest,
                LabelsDigest = labelsDigest,
                Revision = target.ReadOnly ? null : "mcp-al-v1_" + guid + "_" + metaDigest + "_" + labelsDigest,
            };
        }

        private static string Digest(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static string[] Canonical(IEnumerable<string> values) =>
            values.Distinct(StringComparer.Ordinal).OrderBy(value => value, StringComparer.Ordinal).ToArray();

        private static string[] Plan(string action, string[] current, string[] requested)
        {
            var labels = new HashSet<string>(current, StringComparer.Ordinal);
            switch (action)
            {
                case "set_labels":
                    labels = new HashSet<string>(requested, StringComparer.Ordinal);
                    break;
                case "add_labels":
                    labels.UnionWith(requested);
                    break;
                case "remove_labels":
                    labels.ExceptWith(requested);
                    break;
                default:
                    Fail("invalid_asset_labels", "Unsupported mutation.");
                    break;
            }
            if (labels.Count > MaxLabels)
                Fail("asset_labels_limit_exceeded", "Planned labels exceed 128 entries.");
            return Canonical(labels);
        }

        private static void CheckRevision(string expected, Observation actual)
        {
            string reason = null;
            if (expected.Substring(10, 32) != actual.Guid)
                reason = "asset_identity_changed";
            else if (expected.Substring(108, 64) != actual.LabelsDigest)
                reason = "labels_changed";
            else if (expected.Substring(43, 64) != actual.MetaDigest)
                reason = "metadata_changed_without_label_set_change";
            if (reason != null)
                throw new LabelFailure("asset_label_conflict", reason, actual);
        }

        private static void SetObservation(JObject row, Observation observation, string status)
        {
            row["path"] = observation.Path;
            row["status"] = status;
            row["guid"] = observation.Guid;
            row["labels"] = new JArray(observation.Labels);
            row["revision"] = observation.Revision;
            row["read_only"] = observation.ReadOnly;
        }

        private static void Fail(string code, string reason) => throw new LabelFailure(code, reason);

        private sealed class LabelFailure : Exception
        {
            internal readonly string Code;
            internal readonly Observation Current;

            internal LabelFailure(string code, string reason, Observation current = null)
                : base(reason)
            {
                Code = code;
                Current = current;
            }
        }

        private sealed class Request
        {
            internal string Action;
            internal readonly List<Target> Targets = new List<Target>();
            internal string[] Labels;
        }

        private sealed class Target
        {
            internal string Input;
            internal string Revision;
            internal string Path;
            internal string Meta;
            internal bool ReadOnly;
            internal UnityEngine.Object Asset;
            internal Observation Initial;
            internal string[] Planned;
        }

        private sealed class Observation
        {
            internal string Path;
            internal string Guid;
            internal string[] Labels;
            internal string MetaDigest;
            internal string LabelsDigest;
            internal string Revision;
            internal bool ReadOnly;
        }

        private sealed class Budget
        {
            internal long MetaBytes;
            internal long PathBytes;
            private long _labelBytes;

            internal void Labels(string[] labels)
            {
                foreach (string label in labels)
                    _labelBytes += Bytes(label);
                if (_labelBytes > 65536)
                    Fail("asset_labels_limit_exceeded", "Cumulative observed/planned label text exceeds 64 KiB.");
            }
        }
    }
}
