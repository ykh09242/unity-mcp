using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.SourceFreshness;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    [InitializeOnLoad]
    internal static class ScriptDiagnosticJobs
    {
        private sealed class Job
        {
            internal string Id,
                RequestHash,
                State = "queued",
                Reason;
            internal readonly CancellationTokenSource Cancellation = new();
            internal readonly string CursorSecret = Guid.NewGuid().ToString("N");
            internal JObject Result;
            internal long CompletedAt,
                Bytes;
            internal bool Terminal;
        }

        private static readonly Dictionary<string, Job> Jobs = new(StringComparer.Ordinal);
        private static readonly SemaphoreSlim Execution = new(1, 1);
        private static readonly string Generation = Guid.NewGuid().ToString("N");
        private static bool _closing;
        private static int _active;

        static ScriptDiagnosticJobs()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }

        private static void Stop()
        {
            _closing = true;
            foreach (var job in Jobs.Values.Where(job => !job.Terminal))
                job.Cancellation.Cancel();
        }

        internal static bool ValidId(string id) => id != null && id.Length == 32 && id.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f');

        internal static JObject Start(string id, JObject options)
        {
            if (!ValidId(id))
                throw new ArgumentException("invalid_analysis_job_id");
            string requestHash = SourceSnapshot.Hash(options.ToString(Formatting.None));
            if (Jobs.TryGetValue(id, out var existing))
            {
                if (existing.RequestHash != requestHash)
                    throw new ArgumentException("analysis_job_identity_conflict");
                return Summary(existing);
            }
            if (_closing || _active >= 3)
                throw new InvalidOperationException("analysis_capacity_unavailable");
            var capabilities = ScriptAnalysisHost.Capabilities();
            if ((bool?)capabilities["available"] != true)
                throw new InvalidOperationException((string)capabilities["reason"] ?? "analyzer_unavailable");
            if ((bool?)capabilities["consent"] != true)
                throw new InvalidOperationException("analysis_consent_required");
            var job = new Job { Id = id, RequestHash = requestHash };
            Jobs.Add(id, job);
            _active++;
            _ = ExecuteAsync(job, (JObject)options.DeepClone());
            return Summary(job);
        }

        private static async Task ExecuteAsync(Job job, JObject options)
        {
            bool entered = false;
            await Task.Yield();
            try
            {
                job.Cancellation.CancelAfter(TimeSpan.FromSeconds((int?)options["timeout_seconds"] ?? 120));
                var cancellation = job.Cancellation.Token;
                await Execution.WaitAsync(cancellation);
                entered = true;
                job.State = "running";
                var selection = ScriptSelection.Resolve(options);
                var before = await SourceFreshnessService.CaptureAsync(cancellation);
                var request = await ScriptCompilationContext.CaptureAsync(selection, before, (bool?)options["include_suppressed"] ?? false, cancellation);
                var result = await ScriptAnalysisHost.AnalyzeAsync(request, cancellation, job.Id);
                var after = await SourceFreshnessService.CaptureAsync(cancellation);
                BindSourceEvidence(result, before, after);
                result["selected_files"] = new JArray(
                    selection.Files.Select(file => new JObject
                    {
                        ["path"] = file.Path,
                        ["bytes_sha256"] = file.Hash,
                        ["guid"] = file.Guid,
                    })
                );
                result["context_assemblies"] = new JArray(
                    request["assemblies"]
                        .Select(a => new JObject
                        {
                            ["name"] = a["name"],
                            ["language_version"] = a["language_version"],
                            ["source_count"] = ((JArray)a["sources"]).Count,
                        })
                );
                job.Bytes = Encoding.UTF8.GetByteCount(result.ToString(Formatting.None));
                if (job.Bytes > 9L * 1024 * 1024)
                    throw new InvalidOperationException("analysis_result_limit");
                job.Result = result;
                job.State = "completed";
            }
            catch (OperationCanceledException)
            {
                job.State = "canceled";
                job.Reason = "canceled_or_deadline";
            }
            catch (Exception error)
            {
                job.State = "failed";
                job.Reason = error.Message.Length > 512 ? error.Message.Substring(0, 512) : error.Message;
            }
            finally
            {
                // Never advertise settled cancellation or admit more work while the owned process is alive.
                if (entered && ScriptAnalysisHost.Owner.HasActiveChild(job.Id))
                {
                    job.State = "unsettled";
                    job.Reason = "analysis_process_unsettled";
                }
                else
                {
                    job.Terminal = true;
                    job.CompletedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    job.Cancellation.Dispose();
                    _active--;
                    if (entered)
                        Execution.Release();
                    Trim();
                }
            }
        }

        internal static void BindSourceEvidence(JObject result, SourceSnapshot before, SourceSnapshot after)
        {
            bool stale = before.Token != after.Token;
            var gaps = result["coverage_gaps"] as JArray ?? new JArray();
            foreach (string reason in before.Reasons.Concat(after.Reasons).Distinct())
                gaps.Add(reason);
            if (stale)
                gaps.Add("source_changed_during_analysis");
            result["coverage_gaps"] = gaps;
            result["complete"] = (bool?)result["complete"] == true && before.Complete && after.Complete && !stale;
            result["stale"] = stale;
            result["source_token"] = before.Token;
            result["source_digest"] = before.SourceDigest;
            result["context_digest"] = before.ContextDigest;
            result["project_id"] = before.ProjectId;
            result["session_id"] = before.SessionId;
            result["observed_at_unix_ms"] = before.ObservedAtUnixMs;
            result["freshness_basis"] = "captured_source_at_completion";
        }

        private static void Trim()
        {
            var completed = Jobs.Values.Where(j => j.Terminal).OrderBy(j => j.CompletedAt).ToList();
            long bytes = completed.Sum(j => j.Bytes);
            while (completed.Count > 16 || bytes > 128L * 1024 * 1024)
            {
                var first = completed[0];
                completed.RemoveAt(0);
                Jobs.Remove(first.Id);
                bytes -= first.Bytes;
            }
        }

        private static JObject Summary(Job job) =>
            new JObject
            {
                ["schema"] = "script_quality_job@1",
                ["job_id"] = job.Id,
                ["state"] = job.State,
                ["reason"] = job.Reason,
                ["terminal"] = job.Terminal,
                ["editor_generation"] = Generation,
            };

        internal static JObject Status(string id, int size, string cursor)
        {
            if (!Jobs.TryGetValue(id, out var job))
                return new JObject
                {
                    ["job_id"] = id,
                    ["state"] = "unavailable",
                    ["terminal"] = true,
                    ["reason"] = "expired_evicted_or_editor_reloaded",
                };
            var response = Summary(job);
            if (job.Result == null)
                return response;
            int start = 0;
            if (cursor != null)
            {
                string[] parts = cursor.Split('.');
                if (parts.Length != 2 || !int.TryParse(parts[0], out start) || start < 0 || parts[1] != CursorHash(job, start))
                    throw new ArgumentException("invalid_analysis_cursor");
            }
            var result = (JObject)job.Result.DeepClone();
            var rows = ((JArray)result["diagnostics"])
                .Select(row => new JObject { ["scope"] = "selected", ["diagnostic"] = row })
                .Concat(((JArray)result["context_diagnostics"]).Select(row => new JObject { ["scope"] = "context", ["diagnostic"] = row }))
                .ToArray();
            result.Remove("diagnostics");
            result.Remove("context_diagnostics");
            if (start > rows.Length)
                throw new ArgumentException("invalid_analysis_cursor");
            var page = new JArray();
            int offset = start;
            int bytes = Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) + 2048;
            while (offset < rows.Length && page.Count < size)
            {
                int length = Encoding.UTF8.GetByteCount(rows[offset].ToString(Formatting.None));
                if (bytes + length > 512 * 1024)
                    break;
                bytes += length;
                page.Add(rows[offset++]);
            }
            if (offset == start && offset < rows.Length)
                throw new InvalidOperationException("diagnostic_page_limit");
            result["rows"] = page;
            result["total_rows"] = rows.Length;
            result["offset"] = start;
            result["next_cursor"] = offset < rows.Length ? offset + "." + CursorHash(job, offset) : null;
            response["result"] = result;
            return response;
        }

        private static string CursorHash(Job job, int offset) =>
            SourceSnapshot.Hash(job.CursorSecret + "\n" + job.Id + "\n" + job.Result["source_token"] + "\n" + offset);

        internal static JObject Cancel(string id)
        {
            if (!Jobs.TryGetValue(id, out var job))
                return Status(id, 100, null);
            if (!job.Terminal)
            {
                job.State = "canceling";
                job.Cancellation.Cancel();
            }
            return Summary(job);
        }
    }
}
