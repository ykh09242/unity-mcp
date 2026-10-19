using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Build
{
    /// <summary>Bounded versioned build snapshots, independent of the heavy native report and domain-static jobs.</summary>
    internal static class BuildReportStore
    {
        internal const int MaxRecords = 64;
        internal const int MaxFileBytes = 512 * 1024;
        internal const int MaxSteps = 512;
        internal const int MaxPackedAssets = 2048;
        private static readonly Regex IdPattern = new Regex("\\Abr-[0-9a-f]{32}\\z", RegexOptions.CultureInvariant);
        private static readonly Regex HashPattern = new Regex("\\A[0-9a-f]{64}\\z", RegexOptions.CultureInvariant);
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        // Private observation seams used only by owned fixtures; no caller-supplied artifact paths or clocks.
        private static string StorageRootOverride = null;
        private static string sessionId = Guid.NewGuid().ToString("N");
        private static Func<DateTime> UtcNow = () => DateTime.UtcNow;

        internal static bool IsAction(string action) => action == "report_history" || action == "report" || action == "compare_reports";

        internal static bool HasOptions(JObject request) =>
            new[]
            {
                "report_id",
                "baseline_report_id",
                "candidate_report_id",
                "report_section",
                "page_size",
                "page_number",
                "report_revision",
                "baseline_revision",
                "candidate_revision",
                "history_revision",
            }.Any(name => request[name] != null);

        private static string Root
        {
            get
            {
                string project = Path.GetDirectoryName(Application.dataPath);
                return SafePathUtility.ResolveWithinRoot(project, StorageRootOverride ?? "Library/MCPForUnity/BuildReports");
            }
        }

        private static string Location(string name) => SafePathUtility.ResolveWithinRoot(Root, name);

        private static string Hash(string text)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Utf8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }

        internal static string Identity(string jobId) => "br-" + Hash(jobId).Substring(0, 32);

        private static bool Terminal(BuildJobState state) => state != BuildJobState.Pending && state != BuildJobState.Building;

        internal static string SafeText(string value, int maximum = 256)
        {
            if (string.IsNullOrEmpty(value))
                return "";
            if (Path.IsPathRooted(value) || Regex.IsMatch(value, @"[A-Za-z]:[\\/]|\\\\|(?:^|\s)/"))
                return "[redacted-path]";
            string text = new string(value.Select(c => char.IsControl(c) ? ' ' : c).ToArray());
            return text.Length <= maximum ? text : text.Substring(0, maximum);
        }

        internal static string AssetName(string value)
        {
            string path = (value ?? "").Replace('\\', '/');
            if (
                SafeText(path, 512) != path
                || !(path.StartsWith("Assets/", StringComparison.Ordinal) || path.StartsWith("Packages/", StringComparison.Ordinal))
                || path.Split('/').Any(part => part == ".." || part == ".")
                || path.Length > 512
                || path.Any(char.IsControl)
            )
                return "[unavailable]";
            return path;
        }

        internal static void Observe(BuildJob job)
        {
            if (Terminal(job.State))
                return;
            try
            {
                Marker(job.JobId, "build", job.State, job.Target.ToString());
            }
            catch
            {
                job.ReportPersistenceError = "build_report_persistence_unavailable";
            }
        }

        internal static void Observe(BatchJob batch)
        {
            if (Terminal(batch.State))
            {
                Complete(batch);
                return;
            }
            try
            {
                Marker(batch.JobId, "batch", batch.State, "multiple");
            }
            catch { /* Report artifacts never change the accepted native build lifecycle. */ }
        }

        internal static void Complete(BuildJob job, BuildReport nativeReport = null)
        {
            if (!Terminal(job.State))
                return;
            try
            {
                job.ReportId = PersistSnapshot(CreateRecord(job, nativeReport));
                job.ReportPersistenceError = null;
                DeleteMarker(job.JobId);
            }
            catch
            {
                job.ReportPersistenceError = "build_report_persistence_unavailable";
            }
        }

        internal static void Complete(BatchJob batch)
        {
            try
            {
                var children = new JArray();
                foreach (BuildJob child in batch.Children.Take(64))
                {
                    if (Terminal(child.State) && child.ReportId == null)
                        Complete(child);
                    children.Add(
                        new JObject
                        {
                            ["job_id"] = child.JobId,
                            ["report_id"] = child.ReportId,
                            ["result"] = child.State.ToString().ToLowerInvariant(),
                            ["platform"] = child.Target.ToString(),
                        }
                    );
                }
                var summary = new JObject
                {
                    ["kind"] = "batch",
                    ["job_id"] = batch.JobId,
                    ["platform"] = "multiple",
                    ["result"] = batch.State.ToString().ToLowerInvariant(),
                    ["native_report_available"] = false,
                    ["child_count"] = batch.Children.Count,
                    ["errors"] = batch.Children.Sum(child => (long)child.TotalErrors),
                    ["warnings"] = batch.Children.Sum(child => (long)child.TotalWarnings),
                    ["job_total_size_mb"] = batch.Children.Sum(child => child.TotalSizeMb),
                };
                var record = Base(batch.JobId, summary);
                record["children"] = children;
                record["children_complete"] = batch.Children.Count <= 64;
                batch.ReportId = PersistSnapshot(record);
                batch.ReportPersistenceError = null;
                DeleteMarker(batch.JobId);
            }
            catch
            {
                batch.ReportPersistenceError = "build_report_persistence_unavailable";
            }
        }

        private static JObject Base(string jobId, JObject summary) =>
            new JObject
            {
                ["schema_version"] = 1,
                ["report_id"] = Identity(jobId),
                ["created_at"] = UtcNow().ToString("O"),
                ["expires_at"] = UtcNow().AddDays(30).ToString("O"),
                ["summary"] = summary,
                ["steps"] = new JArray(),
                ["packed_assets"] = new JArray(),
                ["children"] = new JArray(),
                ["steps_complete"] = false,
                ["packed_assets_complete"] = false,
                ["children_complete"] = true,
            };

        private static JObject CreateRecord(BuildJob job, BuildReport report)
        {
            var summary = new JObject
            {
                ["kind"] = "build",
                ["job_id"] = job.JobId,
                ["platform"] = job.Target.ToString(),
                ["result"] = job.State.ToString().ToLowerInvariant(),
                ["native_report_available"] = report != null,
                ["errors"] = job.TotalErrors,
                ["warnings"] = job.TotalWarnings,
                ["job_total_size_mb"] = job.TotalSizeMb,
                ["duration_seconds"] =
                    job.CompletedAt.HasValue && job.StartedAt != default
                        ? new JValue(Math.Max(0, (job.CompletedAt.Value - job.StartedAt).TotalSeconds))
                        : JValue.CreateNull(),
            };
            var record = Base(job.JobId, summary);
            if (report != null)
            {
                var native = report.summary;
                summary["native_result"] = native.result.ToString().ToLowerInvariant();
                summary["total_size_bytes"] = native.totalSize;
                summary["errors"] = native.totalErrors;
                summary["warnings"] = native.totalWarnings;
                summary["duration_seconds"] = native.totalTime.TotalSeconds;
                try
                {
                    BuildReportCapture.AddDetails(record, report);
                }
                catch
                {
                    record["steps"] = new JArray();
                    record["packed_assets"] = new JArray();
                    record["steps_complete"] = false;
                    record["packed_assets_complete"] = false;
                    record["details_note"] = "Native detail extraction unavailable; completed native summary retained.";
                }
            }
            return record;
        }

        private static string PersistSnapshot(JObject record)
        {
            Validate(record);
            string id = (string)record["report_id"];
            byte[] bytes;
            while (true)
            {
                var envelope = new JObject { ["revision"] = Hash(record.ToString(Formatting.None)), ["record"] = record };
                bytes = Utf8.GetBytes(envelope.ToString(Formatting.None));
                if (bytes.Length <= MaxFileBytes)
                    break;
                // Geometric trimming bounds serialization work and retains the authoritative summary.
                string section = ((JArray)record["packed_assets"]).Count > 0 ? "packed_assets" : "steps";
                var rows = (JArray)record[section];
                if (rows.Count == 0)
                    throw new InvalidOperationException("Report summary exceeds its byte budget.");
                int retained = rows.Count / 2;
                while (rows.Count > retained)
                    rows.RemoveAt(rows.Count - 1);
                record[section + "_complete"] = false;
                record["details_note"] = "Detail rows truncated to the bounded artifact byte budget; summary retained.";
            }
            Directory.CreateDirectory(Root);
            Prune(id);
            Atomic(Location(id + ".json"), bytes);
            return id;
        }

        private static void Atomic(string destination, byte[] bytes)
        {
            string temporary = Location("write-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                if (File.Exists(destination))
                    File.Replace(temporary, destination, null);
                else
                    File.Move(temporary, destination);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }
        }

        private static string[] Files(bool active = false)
        {
            if (!Directory.Exists(Root))
                return Array.Empty<string>();
            var files = new List<string>();
            int inspected = 0;
            foreach (string file in Directory.EnumerateFiles(Root))
            {
                if (++inspected > 256)
                    throw new InvalidOperationException("Artifact directory entry budget exceeded.");
                string name = Path.GetFileName(file);
                string suffix = active ? ".active.json" : ".json";
                if (name.EndsWith(suffix, StringComparison.Ordinal) && IdPattern.IsMatch(name.Substring(0, name.Length - suffix.Length)))
                    files.Add(Location(name));
                if (files.Count > MaxRecords)
                    throw new InvalidOperationException("Artifact retention inventory exceeds its bound.");
            }
            return files.ToArray();
        }

        private static void Prune(string keep)
        {
            string[] records = Files();
            foreach (string path in records)
            {
                try
                {
                    JObject record = Load(Path.GetFileNameWithoutExtension(path), allowExpired: true);
                    if (Expired(record) && (string)record["report_id"] != keep)
                        File.Delete(path);
                }
                catch (ReportFailure) { /* Corrupt artifacts remain explicit until bounded oldest-record eviction. */ }
            }
            records = Files();
            if (records.Length >= MaxRecords && !records.Any(path => Path.GetFileNameWithoutExtension(path) == keep))
                File.Delete(records.OrderBy(path => File.GetLastWriteTimeUtc(path)).ThenBy(path => path, StringComparer.Ordinal).First());
        }

        private static void Marker(string jobId, string kind, BuildJobState state, string platform)
        {
            if (!Regex.IsMatch(jobId ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z"))
                throw new InvalidOperationException("Invalid observation identity.");
            Directory.CreateDirectory(Root);
            string id = Identity(jobId);
            string path = Location(id + ".active.json");
            if (Files(true).Length >= MaxRecords && !File.Exists(path))
                throw new InvalidOperationException("Active marker limit exceeded.");
            var marker = new JObject
            {
                ["job_id"] = jobId,
                ["kind"] = kind,
                ["state"] = state.ToString().ToLowerInvariant(),
                ["platform"] = platform,
                ["session"] = sessionId,
                ["observed_at"] = UtcNow().ToString("O"),
            };
            Atomic(path, Utf8.GetBytes(marker.ToString(Formatting.None)));
        }

        private static void DeleteMarker(string jobId)
        {
            string path = Location(Identity(jobId) + ".active.json");
            if (File.Exists(path))
                File.Delete(path);
        }

        private static JObject Read(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length > MaxFileBytes)
                    throw new ReportFailure("build_report_corrupt");
                byte[] bytes = new byte[MaxFileBytes + 1];
                int count = 0,
                    read;
                while (count < bytes.Length && (read = stream.Read(bytes, count, bytes.Length - count)) > 0)
                    count += read;
                if (count > MaxFileBytes)
                    throw new ReportFailure("build_report_corrupt");
                using (
                    var reader = new JsonTextReader(new StringReader(Utf8.GetString(bytes, 0, count)))
                    {
                        MaxDepth = 16,
                        DateParseHandling = DateParseHandling.None,
                    }
                )
                {
                    JObject value = JObject.Load(reader);
                    if (reader.Read())
                        throw new ReportFailure("build_report_corrupt");
                    return value;
                }
            }
        }

        private static bool Expired(JObject record) =>
            DateTime.ParseExact((string)record["expires_at"], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind) <= UtcNow();

        private static JObject Load(string id, string expected = null, bool allowExpired = false)
        {
            if (id == null || !IdPattern.IsMatch(id))
                throw new ReportFailure("invalid_build_report_request");
            string path = Location(id + ".json");
            if (!File.Exists(path))
                throw new ReportFailure("build_report_not_found_or_evicted");
            try
            {
                var envelope = Read(path);
                var record = envelope["record"] as JObject;
                Validate(record);
                string revision = Hash(record.ToString(Formatting.None));
                if ((string)record["report_id"] != id || (string)envelope["revision"] != revision)
                    throw new ReportFailure("build_report_corrupt");
                if (!allowExpired && Expired(record))
                    throw new ReportFailure("build_report_expired");
                if (expected != null && expected != revision)
                    throw new ReportFailure("build_report_revision_conflict");
                record["report_revision"] = revision;
                return record;
            }
            catch (ReportFailure)
            {
                throw;
            }
            catch
            {
                throw new ReportFailure("build_report_corrupt");
            }
        }

        private static void Validate(JObject record)
        {
            if (record == null || (int?)record["schema_version"] != 1 || !IdPattern.IsMatch((string)record["report_id"] ?? ""))
                throw new ReportFailure("build_report_corrupt");
            var allowed = new HashSet<string>(
                new[]
                {
                    "schema_version",
                    "report_id",
                    "created_at",
                    "expires_at",
                    "summary",
                    "steps",
                    "packed_assets",
                    "children",
                    "steps_complete",
                    "packed_assets_complete",
                    "children_complete",
                    "details_note",
                },
                StringComparer.Ordinal
            );
            if (record.Properties().Any(p => !allowed.Contains(p.Name)))
                throw new ReportFailure("build_report_corrupt");
            foreach (string name in new[] { "created_at", "expires_at" })
                if (!DateTime.TryParseExact((string)record[name], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out _))
                    throw new ReportFailure("build_report_corrupt");
            if (
                !(record["summary"] is JObject summary)
                || !Regex.IsMatch((string)summary["job_id"] ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z")
                || Identity((string)summary["job_id"]) != (string)record["report_id"]
                || !new[] { "build", "batch" }.Contains((string)summary["kind"])
            )
                throw new ReportFailure("build_report_corrupt");
            foreach (var pair in new[] { ("steps", MaxSteps), ("packed_assets", MaxPackedAssets), ("children", 64) })
                if (!(record[pair.Item1] is JArray array) || array.Count > pair.Item2 || array.Any(row => !(row is JObject)))
                    throw new ReportFailure("build_report_corrupt");
            foreach (string flag in new[] { "steps_complete", "packed_assets_complete", "children_complete" })
                if (record[flag]?.Type != JTokenType.Boolean)
                    throw new ReportFailure("build_report_corrupt");
            var summaryFields = new HashSet<string>(
                new[]
                {
                    "kind",
                    "job_id",
                    "platform",
                    "result",
                    "native_report_available",
                    "child_count",
                    "errors",
                    "warnings",
                    "job_total_size_mb",
                    "duration_seconds",
                    "native_result",
                    "total_size_bytes",
                },
                StringComparer.Ordinal
            );
            if (
                summary.Properties().Any(property => !summaryFields.Contains(property.Name))
                || summary["native_report_available"]?.Type != JTokenType.Boolean
                || summary["platform"]?.Type != JTokenType.String
                || !new[] { "succeeded", "failed", "cancelled", "skipped" }.Contains((string)summary["result"])
            )
                throw new ReportFailure("build_report_corrupt");
            foreach (string name in new[] { "child_count", "errors", "warnings", "job_total_size_mb", "duration_seconds", "total_size_bytes" })
                if (summary[name] != null && summary[name].Type != JTokenType.Null && !NonnegativeNumber(summary[name]))
                    throw new ReportFailure("build_report_corrupt");
            foreach (JObject row in (JArray)record["steps"])
                if (
                    row.Properties().Any(property => !new[] { "name", "depth", "duration_seconds", "message_count" }.Contains(property.Name))
                    || row["name"]?.Type != JTokenType.String
                    || row["depth"]?.Type != JTokenType.Integer
                    || !NonnegativeNumber(row["depth"])
                    || !NonnegativeNumber(row["duration_seconds"])
                )
                    throw new ReportFailure("build_report_corrupt");
            foreach (JObject row in (JArray)record["packed_assets"])
                if (
                    row.Properties().Any(property => !new[] { "source_asset_path", "packed_size_bytes" }.Contains(property.Name))
                    || row["source_asset_path"]?.Type != JTokenType.String
                    || ((string)row["source_asset_path"] != "[unavailable]" && AssetName((string)row["source_asset_path"]) != (string)row["source_asset_path"])
                    || row["packed_size_bytes"]?.Type != JTokenType.Integer
                    || !NonnegativeNumber(row["packed_size_bytes"])
                )
                    throw new ReportFailure("build_report_corrupt");
            foreach (JObject row in (JArray)record["children"])
                if (
                    row.Properties().Any(property => !new[] { "job_id", "report_id", "result", "platform" }.Contains(property.Name))
                    || row["job_id"]?.Type != JTokenType.String
                    || row["result"]?.Type != JTokenType.String
                    || row["platform"]?.Type != JTokenType.String
                    || (row["report_id"]?.Type != JTokenType.Null && !IdPattern.IsMatch((string)row["report_id"] ?? ""))
                )
                    throw new ReportFailure("build_report_corrupt");
            foreach (JValue value in record.Descendants().OfType<JValue>())
            {
                if (value.Type == JTokenType.String)
                {
                    string text = (string)value;
                    if (text.Length > 512 || SafeText(text, 512) != text)
                        throw new ReportFailure("build_report_corrupt");
                }
                if (value.Type == JTokenType.Float && (double.IsNaN(value.Value<double>()) || double.IsInfinity(value.Value<double>())))
                    throw new ReportFailure("build_report_corrupt");
            }
        }

        private static bool NonnegativeNumber(JToken value) =>
            value != null && (value.Type == JTokenType.Integer || value.Type == JTokenType.Float) && value.Value<decimal>() >= 0;

        internal static object Handle(JObject request)
        {
            try
            {
                string action = Text(request, "action");
                string[] common = { "action", "page_size", "page_number" };
                string[] permitted =
                    action == "report_history" ? new[] { "history_revision" }
                    : action == "report" ? new[] { "report_id", "report_section", "report_revision" }
                    : new[] { "baseline_report_id", "candidate_report_id", "report_section", "baseline_revision", "candidate_revision" };
                if (request.Properties().Any(p => !common.Concat(permitted).Contains(p.Name)))
                    throw new ReportFailure("invalid_build_report_request");
                int size = Page(request, "page_size", 50, 100),
                    page = Page(request, "page_number", 1, int.MaxValue);
                JObject data;
                if (action == "report_history")
                    data = History(request, size, page);
                else if (action == "report")
                {
                    JObject record = Load(Text(request, "report_id"), Revision(request, "report_revision"));
                    data = ReportPage(record, Section(request), size, page);
                }
                else
                    data = Compare(
                        Load(Text(request, "baseline_report_id"), Revision(request, "baseline_revision")),
                        Load(Text(request, "candidate_report_id"), Revision(request, "candidate_revision")),
                        Section(request),
                        size,
                        page
                    );
                data["retention"] = new JObject
                {
                    ["max_reports"] = MaxRecords,
                    ["max_artifact_bytes"] = MaxFileBytes,
                    ["ttl_days"] = 30,
                };
                data["limitations"] =
                    "Bounded retained observations, not live native reports. Missing artifacts may have expired or been evicted. Native report/steps/packed arrays are indivisible; captured details can be partial. DetailedBuildReport availability is explicit. Messages/absolute paths are omitted. Local Library cleanup removes artifacts. BuildPlayer remains synchronous on the Editor main thread; no automatic settings changes or resumed builds.";
                if (Utf8.GetByteCount(data.ToString(Formatting.None)) > 256 * 1024)
                    throw new ReportFailure("build_report_response_limit");
                return new SuccessResponse("Read retained build report observations.", data);
            }
            catch (ReportFailure failure)
            {
                return new ErrorResponse(failure.Code);
            }
            catch
            {
                return new ErrorResponse("build_report_store_unavailable");
            }
        }

        private static int Page(JObject request, string name, int fallback, int maximum)
        {
            if (!PaginationBounds.TryRead(request[name], fallback, 1, maximum, name, out int value, out _))
                throw new ReportFailure("invalid_build_report_request");
            return value;
        }

        private static string Text(JObject request, string name, string fallback = null)
        {
            if (request[name] == null)
                return fallback;
            if (request[name].Type != JTokenType.String)
                throw new ReportFailure("invalid_build_report_request");
            return (string)request[name];
        }

        private static string Revision(JObject request, string name)
        {
            string value = Text(request, name);
            if (value != null && !HashPattern.IsMatch(value))
                throw new ReportFailure("invalid_build_report_request");
            return value;
        }

        private static string Section(JObject request)
        {
            string section = Text(request, "report_section", "summary");
            if (section != "summary" && section != "steps" && section != "packed_assets")
                throw new ReportFailure("invalid_build_report_request");
            return section;
        }

        private static JArray Paged(IEnumerable<JObject> rows, int size, int page) =>
            new JArray(rows.Skip((int)Math.Min(PaginationBounds.StartIndex(page, size), int.MaxValue)).Take(size));

        private static JObject ReportPage(JObject record, string section, int size, int page)
        {
            var data = new JObject
            {
                ["report_id"] = record["report_id"],
                ["report_revision"] = record["report_revision"],
                ["summary"] = record["summary"].DeepClone(),
                ["children"] = record["children"].DeepClone(),
                ["children_complete"] = record["children_complete"],
            };
            if (record["details_note"] != null)
                data["details_note"] = record["details_note"];
            if (section != "summary")
            {
                var rows = (JArray)record[section];
                data["section"] = section;
                data["entries"] = Paged(rows.Cast<JObject>(), size, page);
                data["total_entries"] = rows.Count;
                data["details_complete"] = record[section + "_complete"];
                data["page_size"] = size;
                data["page_number"] = page;
                data["has_more"] = PaginationBounds.StartIndex(page, size) + size < rows.Count;
            }
            return data;
        }

        private static JObject History(JObject request, int size, int page)
        {
            var reports = new List<JObject>();
            foreach (string path in Files())
            {
                string id = Path.GetFileNameWithoutExtension(path);
                try
                {
                    var record = Load(id, allowExpired: true);
                    var summary = (JObject)record["summary"].DeepClone();
                    summary["report_id"] = id;
                    summary["report_revision"] = record["report_revision"];
                    summary["created_at"] = record["created_at"];
                    summary["status"] = Expired(record) ? "expired" : "available";
                    reports.Add(summary);
                }
                catch (ReportFailure)
                {
                    reports.Add(new JObject { ["report_id"] = id, ["status"] = "corrupt" });
                }
            }
            reports = reports
                .OrderByDescending(row => (string)row["created_at"], StringComparer.Ordinal)
                .ThenBy(row => (string)row["report_id"], StringComparer.Ordinal)
                .ToList();
            string revision = Hash(new JArray(reports).ToString(Formatting.None));
            string expected = Revision(request, "history_revision");
            if (expected != null && expected != revision)
                throw new ReportFailure("build_report_revision_conflict");
            var active = new JArray();
            foreach (string path in Files(true))
            {
                try
                {
                    var marker = Read(path);
                    string job = (string)marker["job_id"];
                    if (!Regex.IsMatch(job ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z"))
                        throw new ReportFailure("build_report_corrupt");
                    active.Add(
                        new JObject
                        {
                            ["job_id"] = job,
                            ["kind"] = (string)marker["kind"] == "batch" ? "batch" : "build",
                            ["state"] =
                                (string)marker["session"] == sessionId ? ((string)marker["state"] == "building" ? "building" : "pending") : "interrupted",
                            ["resumed"] = false,
                        }
                    );
                }
                catch
                {
                    active.Add(new JObject { ["state"] = "corrupt_observation", ["resumed"] = false });
                }
            }
            return new JObject
            {
                ["reports"] = Paged(reports, size, page),
                ["total_reports"] = reports.Count,
                ["page_size"] = size,
                ["page_number"] = page,
                ["history_revision"] = revision,
                ["history_revision_scope"] = "retained_completed_report_inventory",
                ["has_more"] = PaginationBounds.StartIndex(page, size) + size < reports.Count,
                ["active_observations"] = active,
            };
        }

        internal static JObject FindStatus(string jobId = null)
        {
            try
            {
                var records = new List<JObject>();
                foreach (string path in Files())
                {
                    try
                    {
                        var record = Load(Path.GetFileNameWithoutExtension(path));
                        if (jobId == null || (string)record["summary"]["job_id"] == jobId)
                            records.Add(record);
                    }
                    catch (ReportFailure) { /* Unavailable historical evidence is handled by explicit report reads. */ }
                }
                JObject latest = records.OrderByDescending(record => (string)record["created_at"], StringComparer.Ordinal).FirstOrDefault();
                if (latest == null)
                    return null;
                var summary = (JObject)latest["summary"].DeepClone();
                summary["report_id"] = latest["report_id"];
                summary["report_revision"] = latest["report_revision"];
                summary["persisted"] = true;
                return summary;
            }
            catch
            {
                return null;
            }
        }

        private static JObject Compare(JObject baseline, JObject candidate, string section, int size, int page)
        {
            var data = new JObject
            {
                ["baseline_report_id"] = baseline["report_id"],
                ["candidate_report_id"] = candidate["report_id"],
                ["baseline_revision"] = baseline["report_revision"],
                ["candidate_revision"] = candidate["report_revision"],
                ["section"] = section,
            };
            foreach (string name in new[] { "total_size_bytes", "job_total_size_mb", "errors", "warnings", "duration_seconds", "child_count" })
            {
                var a = baseline["summary"][name];
                var b = candidate["summary"][name];
                data[name + "_delta"] =
                    a != null && b != null && a.Type != JTokenType.Null && b.Type != JTokenType.Null
                        ? new JValue(b.Value<decimal>() - a.Value<decimal>())
                        : JValue.CreateNull();
            }
            data["baseline_result"] = baseline["summary"]["result"];
            data["candidate_result"] = candidate["summary"]["result"];
            data["result_changed"] = !JToken.DeepEquals(baseline["summary"]["result"], candidate["summary"]["result"]);
            data["same_platform"] = JToken.DeepEquals(baseline["summary"]["platform"], candidate["summary"]["platform"]);
            if (section == "summary")
                return data;
            var left = Inventory((JArray)baseline[section], section);
            var right = Inventory((JArray)candidate[section], section);
            var changes = new List<JObject>();
            int added = 0,
                removed = 0,
                changed = 0;
            foreach (string key in left.Keys.Union(right.Keys).OrderBy(value => value, StringComparer.Ordinal))
            {
                left.TryGetValue(key, out JObject a);
                right.TryGetValue(key, out JObject b);
                string state;
                if (a == null)
                {
                    state = "added";
                    added++;
                }
                else if (b == null)
                {
                    state = "removed";
                    removed++;
                }
                else if (!JToken.DeepEquals(a, b))
                {
                    state = "changed";
                    changed++;
                }
                else
                    continue;
                changes.Add(
                    new JObject
                    {
                        ["key"] = key,
                        ["change"] = state,
                        ["baseline"] = a,
                        ["candidate"] = b,
                    }
                );
            }
            data["added_count"] = added;
            data["removed_count"] = removed;
            data["changed_count"] = changed;
            data["baseline_count"] = ((JArray)baseline[section]).Count;
            data["candidate_count"] = ((JArray)candidate[section]).Count;
            data["details_complete"] = (bool)baseline[section + "_complete"] && (bool)candidate[section + "_complete"];
            data["comparison_identity"] =
                section == "packed_assets" ? "source_asset_path with aggregated occurrence count and packed bytes" : "step name, depth and occurrence ordinal";
            data["identity_complete"] = section != "packed_assets" || (!left.ContainsKey("[unavailable]") && !right.ContainsKey("[unavailable]"));
            if (section == "packed_assets")
                data["packed_size_delta_bytes"] =
                    right.Values.Sum(row => row.Value<decimal>("packed_size_bytes")) - left.Values.Sum(row => row.Value<decimal>("packed_size_bytes"));
            data["entries"] = Paged(changes, size, page);
            data["total_changes"] = changes.Count;
            data["page_size"] = size;
            data["page_number"] = page;
            data["has_more"] = PaginationBounds.StartIndex(page, size) + size < changes.Count;
            return data;
        }

        private static Dictionary<string, JObject> Inventory(JArray rows, string section)
        {
            var result = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (JObject row in rows)
            {
                string key = section == "packed_assets" ? (string)row["source_asset_path"] : (string)row["name"] + "#" + (string)row["depth"];
                if (section == "packed_assets" && result.TryGetValue(key, out JObject previous))
                {
                    previous["packed_size_bytes"] = previous.Value<decimal>("packed_size_bytes") + row.Value<decimal>("packed_size_bytes");
                    previous["occurrences"] = previous.Value<int>("occurrences") + 1;
                }
                else
                {
                    if (section == "steps")
                    {
                        string initial = key;
                        int ordinal = 0;
                        while (result.ContainsKey(key))
                            key = initial + "#" + (++ordinal).ToString(CultureInfo.InvariantCulture);
                    }
                    result[key] = (JObject)row.DeepClone();
                    if (section == "packed_assets")
                        result[key]["occurrences"] = 1;
                }
            }
            return result;
        }

        private sealed class ReportFailure : Exception
        {
            internal string Code { get; }

            internal ReportFailure(string code)
                : base(code)
            {
                Code = code;
            }
        }
    }
}
