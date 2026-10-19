using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Build.Reporting;

namespace MCPForUnity.Editor.Tools.Build
{
    public enum BuildJobState
    {
        Pending,
        Building,
        Succeeded,
        Failed,
        Cancelled,
        Skipped,
    }

    public class BuildJob
    {
        public string JobId { get; }
        public BuildJobState State { get; set; } = BuildJobState.Pending;
        public BuildTarget Target { get; set; }
        public string OutputPath { get; set; }
        public DateTime StartedAt { get; set; }
        public DateTime? CompletedAt { get; set; }
        public string ErrorMessage { get; set; }

        // Extracted from BuildReport at completion to avoid retaining the heavy native object
        public double TotalSizeMb { get; set; }
        public int TotalErrors { get; set; }
        public int TotalWarnings { get; set; }
        public string ReportId { get; internal set; }
        public string ReportPersistenceError { get; internal set; }

        public BuildJob(string jobId, BuildTarget target, string outputPath)
        {
            JobId = jobId;
            Target = target;
            OutputPath = outputPath;
        }

        public object ToStatusResponse()
        {
            var data = new Dictionary<string, object>
            {
                ["job_id"] = JobId,
                ["result"] = State.ToString().ToLowerInvariant(),
                ["platform"] = Target.ToString(),
                ["output_path"] = OutputPath,
            };

            if (ReportId != null)
                data["report_id"] = ReportId;
            if (ReportPersistenceError != null)
                data["report_persistence_error"] = ReportPersistenceError;

            if (StartedAt != default)
                data["started_at"] = StartedAt.ToString("O");

            if (CompletedAt.HasValue)
            {
                if (StartedAt != default)
                    data["duration_seconds"] = (CompletedAt.Value - StartedAt).TotalSeconds;
                data["completed_at"] = CompletedAt.Value.ToString("O");
            }

            if (State == BuildJobState.Succeeded || State == BuildJobState.Failed)
            {
                data["total_size_mb"] = TotalSizeMb;
                data["errors"] = TotalErrors;
                data["warnings"] = TotalWarnings;
            }

            if (!string.IsNullOrEmpty(ErrorMessage))
                data["error"] = ErrorMessage;

            return data;
        }
    }

    public class BatchJob
    {
        public string JobId { get; }
        public BuildJobState State { get; set; } = BuildJobState.Pending;
        public List<BuildJob> Children { get; } = new();
        public int CurrentIndex { get; set; } = -1;
        public string ReportId { get; internal set; }
        public string ReportPersistenceError { get; internal set; }

        public BatchJob(string jobId)
        {
            JobId = jobId;
        }

        public object ToStatusResponse()
        {
            int completed = 0;
            string currentBuild = null;
            var builds = new List<object>();

            foreach (var child in Children)
            {
                if (
                    child.State == BuildJobState.Succeeded
                    || child.State == BuildJobState.Failed
                    || child.State == BuildJobState.Skipped
                    || child.State == BuildJobState.Cancelled
                )
                    completed++;
                if (child.State == BuildJobState.Building)
                    currentBuild = child.JobId;
                builds.Add(child.ToStatusResponse());
            }

            var data = new Dictionary<string, object>
            {
                ["job_id"] = JobId,
                ["result"] = State.ToString().ToLowerInvariant(),
                ["completed"] = completed,
                ["total"] = Children.Count,
                ["current_build"] = currentBuild,
                ["builds"] = builds,
            };
            if (ReportId != null)
                data["report_id"] = ReportId;
            if (ReportPersistenceError != null)
                data["report_persistence_error"] = ReportPersistenceError;
            return data;
        }
    }

    /// <summary>
    /// Live jobs remain domain-static and are never silently resumed after reload.
    /// BuildReportStore separately retains bounded completion snapshots and interruption
    /// observations. Native BuildPlayer still blocks the editor main thread.
    /// </summary>
    public static class BuildJobStore
    {
        private static readonly Dictionary<string, BuildJob> _buildJobs = new();
        private static readonly Dictionary<string, BatchJob> _batchJobs = new();
        private static BuildJob _lastCompletedJob;

        public static string CreateJobId() => $"build-{Guid.NewGuid():N}".Substring(0, 16);

        public static string CreateBatchId() => $"batch-{Guid.NewGuid():N}".Substring(0, 16);

        public static void AddBuildJob(BuildJob job)
        {
            _buildJobs[job.JobId] = job;
            BuildReportStore.Observe(job);
        }

        public static void AddBatchJob(BatchJob job)
        {
            _batchJobs[job.JobId] = job;
            BuildReportStore.Observe(job);
        }

        public static BuildJob GetBuildJob(string jobId)
        {
            _buildJobs.TryGetValue(jobId, out var job);
            return job;
        }

        public static BatchJob GetBatchJob(string jobId)
        {
            _batchJobs.TryGetValue(jobId, out var job);
            return job;
        }

        public static BuildJob LastCompletedJob => _lastCompletedJob;

        public static string ActiveJobId
        {
            get
            {
                // Prefer the batch so polling spans all children, including preparation gaps.
                foreach (var batch in _batchJobs.Values)
                    if (batch.State == BuildJobState.Pending || batch.State == BuildJobState.Building)
                        return batch.JobId;
                foreach (var job in _buildJobs.Values)
                    if (job.State == BuildJobState.Pending || job.State == BuildJobState.Building)
                        return job.JobId;
                return null;
            }
        }

        public static void SetLastCompleted(BuildJob job)
        {
            _lastCompletedJob = job;
            if (job.ReportId == null && job.ReportPersistenceError == null)
                BuildReportStore.Complete(job);
            PruneOldJobs();
        }

        private const int MaxRetainedJobs = 50;

        private static void PruneOldJobs()
        {
            if (_buildJobs.Count <= MaxRetainedJobs)
                return;

            var toRemove = new List<string>();
            foreach (var kvp in _buildJobs)
            {
                if (kvp.Value.State != BuildJobState.Building && kvp.Value.State != BuildJobState.Pending && kvp.Value != _lastCompletedJob)
                    toRemove.Add(kvp.Key);
            }

            foreach (var key in toRemove)
            {
                _buildJobs.Remove(key);
                if (_buildJobs.Count <= MaxRetainedJobs / 2)
                    break;
            }

            // Also prune batch jobs whose children are all terminal
            var batchesToRemove = new List<string>();
            foreach (var kvp in _batchJobs)
            {
                var batch = kvp.Value;
                if (batch.State == BuildJobState.Building || batch.State == BuildJobState.Pending)
                    continue;
                // Keep the original summary while any child is still retained.
                // Pruning individual child references would change historical totals.
                if (!batch.Children.Exists(c => _buildJobs.ContainsKey(c.JobId)))
                    batchesToRemove.Add(kvp.Key);
            }
            foreach (var key in batchesToRemove)
                _batchJobs.Remove(key);
        }
    }
}
