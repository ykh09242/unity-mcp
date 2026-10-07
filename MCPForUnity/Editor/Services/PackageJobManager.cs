using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
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
        private const long DomainReloadTimeoutMs = 120_000;

        private static readonly object LockObj = new();
        private static readonly Dictionary<string, PackageJob> Jobs = new();

        static PackageJobManager()
        {
            TryRestoreFromSessionState();
        }

        private sealed class PersistedState
        {
            public List<PersistedJob> jobs { get; set; }
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
        }

        private static PackageJobStatus ParseStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return PackageJobStatus.Running;

            return status.Trim().ToLowerInvariant() switch
            {
                "succeeded" => PackageJobStatus.Succeeded,
                "failed" => PackageJobStatus.Failed,
                _ => PackageJobStatus.Running,
            };
        }

        private static void TryRestoreFromSessionState()
        {
            try
            {
                string json = SessionState.GetString(SessionKeyJobs, string.Empty);
                if (string.IsNullOrWhiteSpace(json))
                    return;

                var state = JsonConvert.DeserializeObject<PersistedState>(json);
                if (state?.jobs == null)
                    return;

                lock (LockObj)
                {
                    Jobs.Clear();
                    long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

                    foreach (var pj in state.jobs)
                    {
                        if (pj == null || string.IsNullOrWhiteSpace(pj.job_id))
                            continue;

                        var job = new PackageJob
                        {
                            JobId = pj.job_id,
                            Status = ParseStatus(pj.status),
                            Operation = pj.operation,
                            Package = pj.package_,
                            StartedUnixMs = pj.started_unix_ms,
                            FinishedUnixMs = pj.finished_unix_ms,
                            LastUpdateUnixMs = pj.last_update_unix_ms,
                            Error = pj.error,
                            ResultVersion = pj.result_version,
                            ResultName = pj.result_name,
                        };

                        // Domain reload recovery for running jobs
                        if (job.Status == PackageJobStatus.Running)
                        {
                            TryRecoverJob(job, now);
                        }

                        Jobs[pj.job_id] = job;
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PackageJobManager] Failed to restore SessionState: {ex.Message}");
            }
        }

        internal static void TryRecoverJob(PackageJob job, long nowMs)
        {
            try
            {
                string packageName = ExtractPackageName(job.Package);
                var allPackages = PackageInfo.GetAllRegisteredPackages();
                var info = FindPackageInfo(allPackages, packageName, job.Package);

                if (job.Operation == "add" || job.Operation == "embed")
                {
                    if (
                        info != null
                        && (job.Operation == "embed" ? info.source == PackageSource.Embedded : PackageRecoveryIdentity.MatchesVersion(info, job.Package))
                    )
                    {
                        job.Status = PackageJobStatus.Succeeded;
                        job.FinishedUnixMs = nowMs;
                        job.LastUpdateUnixMs = nowMs;
                        job.ResultVersion = info.version;
                        job.ResultName = info.name;
                        McpLog.Info($"[PackageJobManager] Recovered {job.Operation} job {job.JobId}: {info.name}@{info.version} installed.");
                    }
                    else if (nowMs - job.StartedUnixMs > DomainReloadTimeoutMs)
                    {
                        job.Status = PackageJobStatus.Failed;
                        job.FinishedUnixMs = nowMs;
                        job.LastUpdateUnixMs = nowMs;
                        job.Error = $"Package {job.Operation} timed out after domain reload.";
                        McpLog.Warn($"[PackageJobManager] Timed out {job.Operation} job {job.JobId} for '{job.Package}'.");
                    }
                }
                else if (job.Operation == "remove")
                {
                    if (info == null)
                    {
                        job.Status = PackageJobStatus.Succeeded;
                        job.FinishedUnixMs = nowMs;
                        job.LastUpdateUnixMs = nowMs;
                        McpLog.Info($"[PackageJobManager] Recovered remove job {job.JobId}: '{packageName}' is no longer installed.");
                    }
                    else if (nowMs - job.StartedUnixMs > DomainReloadTimeoutMs)
                    {
                        job.Status = PackageJobStatus.Failed;
                        job.FinishedUnixMs = nowMs;
                        job.LastUpdateUnixMs = nowMs;
                        job.Error = "Package removal timed out after domain reload.";
                        McpLog.Warn($"[PackageJobManager] Timed out remove job {job.JobId} for '{job.Package}'.");
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PackageJobManager] Recovery check failed for job {job.JobId}: {ex.Message}");
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

        internal static void PersistToSessionState()
        {
            try
            {
                PersistedState snapshot;
                lock (LockObj)
                {
                    // Keep every in-flight request; evict only completed history. This also
                    // bounds the managed dictionary between domain reloads.
                    if (Jobs.Count > MaxJobsToKeep)
                    {
                        var expiredIds = Jobs
                            .Values.Where(j => j.Status != PackageJobStatus.Running)
                            .OrderBy(j => j.LastUpdateUnixMs)
                            .Take(Jobs.Count - MaxJobsToKeep)
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
                            package_ = j.Package,
                            started_unix_ms = j.StartedUnixMs,
                            finished_unix_ms = j.FinishedUnixMs,
                            last_update_unix_ms = j.LastUpdateUnixMs,
                            error = j.Error,
                            result_version = j.ResultVersion,
                            result_name = j.ResultName,
                        })
                        .ToList();

                    snapshot = new PersistedState { jobs = jobs };
                }

                SessionState.SetString(SessionKeyJobs, JsonConvert.SerializeObject(snapshot));
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PackageJobManager] Failed to persist SessionState: {ex.Message}");
            }
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
                Package = package,
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
                if (!Jobs.TryGetValue(jobId, out var job))
                    return;

                job.Status = success ? PackageJobStatus.Succeeded : PackageJobStatus.Failed;
                job.FinishedUnixMs = now;
                job.LastUpdateUnixMs = now;
                job.Error = error;
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

        public static object ToSerializable(PackageJob job)
        {
            if (job == null)
                return null;

            return new
            {
                job_id = job.JobId,
                status = job.Status.ToString().ToLowerInvariant(),
                operation = job.Operation,
                package_ = job.Package,
                started_unix_ms = job.StartedUnixMs,
                finished_unix_ms = job.FinishedUnixMs,
                last_update_unix_ms = job.LastUpdateUnixMs,
                error = job.Error,
                result_version = job.ResultVersion,
                result_name = job.ResultName,
            };
        }
    }
}
