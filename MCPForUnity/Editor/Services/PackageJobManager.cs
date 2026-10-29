using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Services
{
    internal enum PackageJobStatus
    {
        Running,
        Succeeded,
        Failed,
        Unknown,
    }

    internal sealed class PackageJob
    {
        public string JobId { get; set; }
        public PackageJobStatus Status { get; set; }
        public string Operation { get; set; }
        public string Package { get; set; }
        public long StartedUnixMs { get; set; }
        public long? FinishedUnixMs { get; set; }
        public long LastUpdateUnixMs { get; set; }
        public string Error { get; set; }
        public string ResultVersion { get; set; }
        public string ResultName { get; set; }
        public JObject Resolve { get; set; }
        public string RecoveryReason { get; set; }
        public JObject PackageObservation { get; set; }
    }

    internal static class PackageRecoveryIdentity
    {
        internal static bool IsSourceIdentifier(string identifier)
        {
            return !string.IsNullOrEmpty(identifier)
                && (
                    identifier.StartsWith("http", StringComparison.OrdinalIgnoreCase)
                    || identifier.StartsWith("git", StringComparison.OrdinalIgnoreCase)
                    || identifier.StartsWith("ssh://", StringComparison.OrdinalIgnoreCase)
                    || identifier.StartsWith("file:", StringComparison.OrdinalIgnoreCase)
                    || identifier.EndsWith(".git", StringComparison.OrdinalIgnoreCase)
                );
        }

        internal static bool MatchesVersion(PackageInfo info, string identifier)
        {
            if (IsSourceIdentifier(identifier))
                return true;

            int atIndex = identifier?.IndexOf('@') ?? -1;
            return atIndex < 0 || atIndex == identifier.Length - 1 || string.Equals(info.version, identifier.Substring(atIndex + 1), StringComparison.Ordinal);
        }

        internal static bool MatchesSource(PackageInfo info, string identifier, string packagesDirectory)
        {
            bool isFile = identifier.StartsWith("file:", StringComparison.OrdinalIgnoreCase);
            if (
                isFile
                    ? info.source != PackageSource.Local && info.source != PackageSource.LocalTarball && info.source != PackageSource.Git
                    : info.source != PackageSource.Git
            )
                return false;

            // file: can identify a local package or a Git FILE URL. Registered source is authoritative.
            bool isLocalFile = isFile && info.source != PackageSource.Git;

            string source = info.packageId;
            string prefix = info.name + "@";
            if (source != null && source.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                source = source.Substring(prefix.Length);

            // Unity's Git marker is not part of the URL passed to Git; local IDs normalize separators.
            string requested = NormalizeSource(identifier, isLocalFile);
            source = NormalizeSource(source, isLocalFile);
            if (string.Equals(source, requested, StringComparison.Ordinal))
                return true;

            // A local folder ID may be absolute even when the request was relative to Packages.
            // Tarball resolvedPath is an extracted folder, not the requested archive.
            if (!isLocalFile || info.source != PackageSource.Local || string.IsNullOrEmpty(info.resolvedPath))
                return false;

            string path = requested.Substring("file:".Length);
            string fullPath = Path.GetFullPath(Path.IsPathRooted(path) ? path : Path.Combine(packagesDirectory, path))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string resolvedPath = Path.GetFullPath(info.resolvedPath.Replace('\\', '/')).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return string.Equals(fullPath, resolvedPath, comparison);
        }

        private static string NormalizeSource(string source, bool isFile)
        {
            if (source == null)
                return null;
            if (isFile)
                return source.StartsWith("file:", StringComparison.OrdinalIgnoreCase)
                    ? "file:" + source.Substring("file:".Length).Replace('\\', '/')
                    : source.Replace('\\', '/');
            return source.StartsWith("git+", StringComparison.OrdinalIgnoreCase) ? source.Substring(4) : source;
        }
    }

    internal static class PackageJobManager
    {
        private const string SessionKeyJobs = "MCPForUnity.PackageJobsV1";
        private const int MaxJobsToKeep = 10;

        private static readonly object LockObj = new();
        private static readonly Dictionary<string, PackageJob> Jobs = new();

        internal static Func<string> SessionReader = () => SessionState.GetString(SessionKeyJobs, string.Empty);
        internal static Action<string> SessionWriter = value => SessionState.SetString(SessionKeyJobs, value);
        internal static JObject ResolveRegistration = null;
        internal static Func<PackageInfo[]> RegisteredPackagesReader = PackageInfo.GetAllRegisteredPackages;

        static PackageJobManager()
        {
            TryRestoreFromSessionState();
        }

        private sealed class PersistedState
        {
            public List<PersistedJob> jobs { get; set; }

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public JToken resolve_registration { get; set; }
        }

        private sealed class PersistedJob
        {
            public string job_id { get; set; }
            public string status { get; set; }
            public string operation { get; set; }
            public string package_ { get; set; }
            public long started_unix_ms { get; set; }
            public long? finished_unix_ms { get; set; }
            public long last_update_unix_ms { get; set; }
            public string error { get; set; }
            public string result_version { get; set; }
            public string result_name { get; set; }

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string recovery_reason { get; set; }

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public JToken resolve { get; set; }

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public JObject package_observation { get; set; }
        }

        private static PackageJobStatus ParseStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return PackageJobStatus.Running;

            return status.Trim().ToLowerInvariant() switch
            {
                "succeeded" => PackageJobStatus.Succeeded,
                "failed" => PackageJobStatus.Failed,
                "unknown" => PackageJobStatus.Unknown,
                _ => PackageJobStatus.Running,
            };
        }

        private static void TryRestoreFromSessionState()
        {
            try
            {
                string json = SessionReader();
                if (string.IsNullOrWhiteSpace(json))
                    return;

                var state = JsonConvert.DeserializeObject<PersistedState>(json);
                if (state?.jobs == null)
                    return;

                lock (LockObj)
                {
                    Jobs.Clear();
                    ResolveRegistration = NormalizeRegistration(state.resolve_registration as JObject);
                    long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    PackageInfo[] recoveryPackages = null;

                    foreach (var pj in state.jobs)
                    {
                        if (pj == null || string.IsNullOrWhiteSpace(pj.job_id))
                            continue;

                        var job = new PackageJob
                        {
                            JobId = pj.job_id,
                            Status = ParseStatus(pj.status),
                            Operation = pj.operation,
                            Package = PackageSourceSecurity.ForStorage(pj.package_),
                            StartedUnixMs = pj.started_unix_ms,
                            FinishedUnixMs = pj.finished_unix_ms,
                            LastUpdateUnixMs = pj.last_update_unix_ms,
                            Error = PackageSourceSecurity.Redact(pj.error),
                            ResultVersion = pj.result_version,
                            ResultName = pj.result_name,
                            RecoveryReason = pj.recovery_reason,
                            PackageObservation = pj.package_observation,
                            Resolve = pj.operation == "resolve" ? NormalizeResolve(pj.resolve as JObject) : null,
                        };

                        if (job.Operation == "resolve" && job.Status == PackageJobStatus.Succeeded)
                            job.Status = PackageJobStatus.Unknown;

                        // Lost observations remain reconcilable; native failures do not.
                        if (CanReconcile(job))
                        {
                            TryRecoverJobWithSnapshot(job, now, ref recoveryPackages);
                        }

                        Jobs[pj.job_id] = job;
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PackageJobManager] Failed to restore SessionState: {PackageSourceSecurity.Redact(ex.Message)}");
            }
            finally
            {
                OperationActivity.Invalidate();
            }
        }

        internal static void TryRecoverJob(PackageJob job, long nowMs)
        {
            PackageJobStatus? previous = job?.Status;
            long? updated = job?.LastUpdateUnixMs;
            PackageInfo[] recoveryPackages = null;
            TryRecoverJobWithSnapshot(job, nowMs, ref recoveryPackages);
            if (previous != job?.Status || updated != job?.LastUpdateUnixMs)
                OperationActivity.Invalidate();
        }

        internal static bool CanReconcile(PackageJob job) =>
            job != null && (job.Status == PackageJobStatus.Running || job.Status == PackageJobStatus.Unknown && job.Operation is "add" or "embed" or "remove");

        private static void MarkRecoveryUnknown(PackageJob job, long nowMs, string reason)
        {
            if (job.Status != PackageJobStatus.Unknown || job.RecoveryReason != reason)
                job.LastUpdateUnixMs = nowMs;
            job.Status = PackageJobStatus.Unknown;
            job.FinishedUnixMs = null;
            job.Error = null;
            job.RecoveryReason = reason;
        }

        private static void TryRecoverJobWithSnapshot(PackageJob job, long nowMs, ref PackageInfo[] recoveryPackages)
        {
            if (!CanReconcile(job))
                return;
            if (job.Operation == "resolve")
            {
                job.Status = PackageJobStatus.Unknown;
                job.FinishedUnixMs = job.LastUpdateUnixMs = nowMs;
                job.Resolve = ResolveEvidence("unknown", "trigger_state_unknown");
                return;
            }
            try
            {
                string packageName = ExtractPackageName(job.Package);
                var allPackages = recoveryPackages ??= RegisteredPackagesReader();
                var info = FindPackageInfo(allPackages, packageName, job.Package);

                bool desiredState = job.Operation switch
                {
                    "add" => info != null && PackageRecoveryIdentity.MatchesVersion(info, job.Package),
                    "embed" => info != null && info.source == PackageSource.Embedded,
                    "remove" => info == null,
                    _ => false,
                };
                // Registration may predate admission and never proves this Request completed.
                MarkRecoveryUnknown(job, nowMs, "request_lost_after_reload");
                job.PackageObservation = new JObject
                {
                    ["status"] = "observed",
                    ["desired_state_observed"] = desiredState,
                    ["observed_unix_ms"] = nowMs,
                    ["completion_effect"] = "none",
                    ["registered_name"] = info?.name,
                    ["registered_version"] = info?.version,
                };
            }
            catch (Exception ex)
            {
                MarkRecoveryUnknown(job, nowMs, "request_lost_after_reload");
                job.PackageObservation = new JObject
                {
                    ["status"] = "unavailable",
                    ["reason"] = "registration_unavailable",
                    ["completion_effect"] = "none",
                };
                McpLog.Warn($"[PackageJobManager] Recovery check failed for job {job.JobId}: {PackageSourceSecurity.Redact(ex.Message)}");
            }
        }

        /// <summary>
        /// Find a PackageInfo by name, falling back to packageId or git/local source for non-standard identifiers.
        /// </summary>
        private static PackageInfo FindPackageInfo(PackageInfo[] allPackages, string packageName, string originalIdentifier)
        {
            if (!PackageRecoveryIdentity.IsSourceIdentifier(originalIdentifier))
                return allPackages.FirstOrDefault(p => string.Equals(p.name, packageName, StringComparison.OrdinalIgnoreCase));

            string packagesDirectory = originalIdentifier.StartsWith("file:", StringComparison.OrdinalIgnoreCase)
                ? Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath, "..", "Packages"))
                : null;
            return allPackages.FirstOrDefault(p => PackageRecoveryIdentity.MatchesSource(p, originalIdentifier, packagesDirectory));
        }

        internal static string ExtractPackageName(string packageIdentifier)
        {
            if (string.IsNullOrEmpty(packageIdentifier))
                return packageIdentifier;

            if (PackageRecoveryIdentity.IsSourceIdentifier(packageIdentifier))
                return packageIdentifier;

            // Strip version: "com.unity.foo@1.0.0" -> "com.unity.foo"
            int atIndex = packageIdentifier.IndexOf('@');
            if (atIndex > 0)
                return packageIdentifier.Substring(0, atIndex);

            // Git URLs and file: paths — can't reliably extract name, return as-is
            return packageIdentifier;
        }

        internal static void AppendActivity(List<OperationActivityRow> rows)
        {
            lock (LockObj)
                foreach (var job in Jobs.Values)
                    rows.Add(OperationActivity.PackageRow(job));
        }

        internal static void PersistToSessionState() => TryPersistToSessionState();

        private static bool TryPersistToSessionState()
        {
            try
            {
                PersistedState snapshot;
                lock (LockObj)
                {
                    // Running requests do not consume the completed-history allowance.
                    int completedCount = Jobs.Values.Count(j => j.Status != PackageJobStatus.Running);
                    if (completedCount > MaxJobsToKeep)
                    {
                        var expiredIds = Jobs
                            .Values.Where(j => j.Status != PackageJobStatus.Running)
                            .OrderBy(j => j.LastUpdateUnixMs)
                            .Take(completedCount - MaxJobsToKeep)
                            .Select(j => j.JobId)
                            .ToList();
                        foreach (string id in expiredIds)
                            Jobs.Remove(id);
                    }
                    var jobs = Jobs
                        .Values.OrderByDescending(j => j.LastUpdateUnixMs)
                        .Select(j => new PersistedJob
                        {
                            job_id = j.JobId,
                            status = j.Status.ToString().ToLowerInvariant(),
                            operation = j.Operation,
                            package_ = PackageSourceSecurity.ForStorage(j.Package),
                            started_unix_ms = j.StartedUnixMs,
                            finished_unix_ms = j.FinishedUnixMs,
                            last_update_unix_ms = j.LastUpdateUnixMs,
                            error = PackageSourceSecurity.Redact(j.Error),
                            result_version = j.ResultVersion,
                            result_name = j.ResultName,
                            recovery_reason = j.RecoveryReason,
                            package_observation = j.PackageObservation,
                            resolve = j.Resolve,
                        })
                        .ToList();

                    snapshot = new PersistedState { jobs = jobs, resolve_registration = ResolveRegistration };
                }

                SessionWriter(JsonConvert.SerializeObject(snapshot));
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PackageJobManager] Failed to persist SessionState: {PackageSourceSecurity.Redact(ex.Message)}");
                return false;
            }
            finally
            {
                OperationActivity.Invalidate();
            }
        }

        internal static JObject ResolveEvidence(string trigger, string reason) =>
            new JObject
            {
                ["trigger_state"] = trigger,
                ["triggered"] = trigger == "triggered" ? new JValue(true) : JValue.CreateNull(),
                ["completion"] = "unknown",
                ["reason"] = reason,
                ["persistence_scope"] = "editor_session",
                ["coordination_lifetime"] = "trigger_only",
                ["native_lifetime_supported"] = false,
            };

        private static JObject NormalizeResolve(JObject raw)
        {
            string state = raw?["trigger_state"]?.Type == JTokenType.String ? (string)raw["trigger_state"] : null;
            return state switch
            {
                "triggered" => ResolveEvidence("triggered", "completion_unobservable"),
                "failed" => ResolveEvidence("failed", "trigger_failed"),
                _ => ResolveEvidence("unknown", "trigger_state_unknown"),
            };
        }

        private static JObject NormalizeRegistration(JObject raw)
        {
            string phase = raw?["phase"]?.Type == JTokenType.String ? (string)raw["phase"] : null;
            if ((phase != "registering" && phase != "registered") || raw?["observed_unix_ms"]?.Type != JTokenType.Integer)
                return null;
            if (!long.TryParse(raw["observed_unix_ms"].ToString(), out long time) || time < 0)
                return null;
            long sequence = long.TryParse(raw["sequence"]?.ToString(), out long parsed) && parsed >= 0 ? parsed : 0;
            return new JObject
            {
                ["phase"] = phase,
                ["observed_unix_ms"] = time,
                ["sequence"] = sequence,
                ["scope"] = "editor_global",
            };
        }

        internal static bool HasRunningJobs()
        {
            lock (LockObj)
                return Jobs.Values.Any(job => job.Status == PackageJobStatus.Running);
        }

        internal static void RecordResolveRegistration(string phase, long time)
        {
            lock (LockObj)
            {
                if (!Jobs.Values.Any(job => job.Operation == "resolve"))
                    return;
                long previous = long.TryParse(ResolveRegistration?["sequence"]?.ToString(), out long parsed) && parsed >= 0 ? parsed : 0;
                ResolveRegistration = NormalizeRegistration(
                    new JObject
                    {
                        ["phase"] = phase,
                        ["observed_unix_ms"] = time,
                        ["sequence"] = previous < long.MaxValue ? previous + 1 : previous,
                    }
                );
            }
            PersistToSessionState();
        }

        internal static bool TryStartResolveJob(long now, out PackageJob job)
        {
            job = new PackageJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Operation = "resolve",
                Status = PackageJobStatus.Running,
                StartedUnixMs = now,
                LastUpdateUnixMs = now,
                Resolve = ResolveEvidence("requested", "trigger_state_unknown"),
            };
            Dictionary<string, PackageJob> previous;
            lock (LockObj)
            {
                previous = new Dictionary<string, PackageJob>(Jobs);
                Jobs.Add(job.JobId, job);
            }
            if (TryPersistToSessionState())
                return true;
            lock (LockObj)
            {
                Jobs.Clear();
                foreach (var entry in previous)
                    Jobs.Add(entry.Key, entry.Value);
            }
            job = null;
            return false;
        }

        internal static void FinishResolveInvocation(PackageJob job, bool acknowledged, long now)
        {
            job.Status = acknowledged ? PackageJobStatus.Unknown : PackageJobStatus.Failed;
            job.FinishedUnixMs = job.LastUpdateUnixMs = now;
            job.Resolve = ResolveEvidence(acknowledged ? "triggered" : "failed", acknowledged ? "completion_unobservable" : "trigger_failed");
            // If this update fails, the session intent remains ambiguous after reload. Never repeat it.
            PersistToSessionState();
        }

        public static string StartJob(string operation, string package)
        {
            string jobId = Guid.NewGuid().ToString("N");
            long started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            var job = new PackageJob
            {
                JobId = jobId,
                Status = PackageJobStatus.Running,
                Operation = operation,
                Package = PackageSourceSecurity.ForStorage(package),
                StartedUnixMs = started,
                FinishedUnixMs = null,
                LastUpdateUnixMs = started,
                Error = null,
                ResultVersion = null,
                ResultName = null,
            };

            lock (LockObj)
            {
                Jobs[jobId] = job;
            }
            PersistToSessionState();
            return jobId;
        }

        public static void CompleteJob(string jobId, bool success, string error = null, string version = null, string name = null)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (!Jobs.TryGetValue(jobId, out var job) || job.Operation == "resolve")
                    return;

                job.Status = success ? PackageJobStatus.Succeeded : PackageJobStatus.Failed;
                job.FinishedUnixMs = now;
                job.LastUpdateUnixMs = now;
                job.Error = PackageSourceSecurity.Redact(error);
                job.RecoveryReason = null;
                job.PackageObservation = null;
                job.ResultVersion = version;
                job.ResultName = name;
            }
            PersistToSessionState();
        }

        public static PackageJob GetJob(string jobId)
        {
            if (string.IsNullOrWhiteSpace(jobId))
                return null;

            lock (LockObj)
            {
                Jobs.TryGetValue(jobId, out var job);
                return job;
            }
        }

        public static PackageJob GetLatestJob()
        {
            lock (LockObj)
            {
                return Jobs.Values.OrderByDescending(j => j.StartedUnixMs).FirstOrDefault();
            }
        }

        internal static object ActivityEvidence(string id)
        {
            var job = GetJob(id);
            if (job == null)
                return null;
            // Activity evidence is copyable. Omit raw source identifiers and native errors,
            // which may embed credentials; status also redacts legacy diagnostics.
            return new
            {
                job_id = job.JobId,
                status = job.Status.ToString().ToLowerInvariant(),
                operation = job.Operation,
                package_ = PackageRecoveryIdentity.IsSourceIdentifier(job.Package) ? "source package" : job.Package,
                started_unix_ms = job.StartedUnixMs,
                finished_unix_ms = job.FinishedUnixMs,
                last_update_unix_ms = job.LastUpdateUnixMs,
                has_error = !string.IsNullOrEmpty(job.Error),
                result_version = job.ResultVersion,
                result_name = job.ResultName,
                recovery = job.RecoveryReason == null
                    ? null
                    : new
                    {
                        reason = job.RecoveryReason,
                        native_completion = "unobserved",
                        retry_safe = false,
                    },
                resolve = job.Operation == "resolve" ? NormalizeResolve(job.Resolve) : null,
                package_observation = job.PackageObservation?.DeepClone(),
                observation = "Source identifiers and native error text are omitted from copyable Activity evidence.",
            };
        }

        public static object ToSerializable(PackageJob job)
        {
            if (job == null)
                return null;

            var result = new
            {
                job_id = job.JobId,
                status = job.Status.ToString().ToLowerInvariant(),
                operation = job.Operation,
                package_ = PackageSourceSecurity.Redact(job.Package),
                started_unix_ms = job.StartedUnixMs,
                finished_unix_ms = job.FinishedUnixMs,
                last_update_unix_ms = job.LastUpdateUnixMs,
                error = PackageSourceSecurity.Redact(job.Error),
                result_version = job.ResultVersion,
                result_name = job.ResultName,
            };
            if (job.Operation != "resolve" && job.RecoveryReason == null)
                return result;
            var observation = JObject.FromObject(result);
            if (job.Operation == "resolve")
                observation["resolve"] = job.Resolve?.DeepClone() ?? ResolveEvidence("unknown", "trigger_state_unknown");
            else
                observation["recovery"] = new JObject
                {
                    ["reason"] = job.RecoveryReason,
                    ["native_completion"] = "unobserved",
                    ["retry_safe"] = false,
                };
            if (job.PackageObservation != null)
                observation["package_observation"] = job.PackageObservation.DeepClone();
            return observation;
        }
    }
}
