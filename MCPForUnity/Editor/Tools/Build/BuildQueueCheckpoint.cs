using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Build
{
    // SessionState is deliberately process scoped: an Editor restart never resumes a build.
    internal sealed class BuildQueueJournal
    {
        private const int MaxBytes = 1048576;
        internal const int MaxActiveEntries = 32;
        internal const int MaxRecentTerminalEntries = 64;
        internal const int MaxHistoryEntries = 4096;
        private readonly Func<string> read;
        private readonly Action<string> write;
        private readonly Func<bool> readWriting;
        private readonly Action<bool> writeWriting;
        private readonly string project;
        private List<BuildQueueTombstone> history;

        internal BuildQueueJournal(Func<string> read, Action<string> write, Func<bool> readWriting, Action<bool> writeWriting, string project)
        {
            this.read = read;
            this.write = write;
            this.readWriting = readWriting;
            this.writeWriting = writeWriting;
            this.project = project;
        }

        internal List<BuildQueueEntry> Load()
        {
            if (readWriting())
                throw new InvalidDataException("Build continuation storage has an incomplete write. Queued builds cannot be resumed safely.");
            string json = read();
            if (string.IsNullOrEmpty(json))
            {
                history = new List<BuildQueueTombstone>();
                return new List<BuildQueueEntry>();
            }
            try
            {
                if (Encoding.UTF8.GetByteCount(json) > MaxBytes)
                    throw new InvalidDataException("Build continuation storage exceeds its limit.");
                JObject root = JObject.Parse(json);
                int? version = (int?)root["version"];
                if (
                    (version != 1 && version != 2)
                    || root.Count != (version == 1 ? 4 : 5)
                    || (string)root["project"] != project
                    || root["entries"] is not JArray
                    || (version == 2 && root["history"] is not JArray)
                )
                    throw new InvalidDataException("Build continuation storage identity is invalid.");
                JToken signed = version == 1 ? root["entries"] : new JArray(root["entries"], root["history"]);
                if ((string)root["sha256"] != Hash(signed.ToString(Formatting.None)))
                    throw new InvalidDataException("Build continuation storage checksum is invalid.");
                var entries = root["entries"].ToObject<List<BuildQueueEntry>>();
                history = version == 1 ? new List<BuildQueueTombstone>() : root["history"].ToObject<List<BuildQueueTombstone>>();
                Validate(entries);
                ValidateHistory(entries, history);
                return entries;
            }
            catch (Exception error) when (error is JsonException || error is ArgumentException || error is InvalidCastException || error is OverflowException)
            {
                throw new InvalidDataException("Build continuation storage is corrupt; it will not be replaced or replayed.", error);
            }
        }

        internal List<BuildQueueEntry> Save(IEnumerable<BuildQueueEntry> source)
        {
            if (readWriting())
                throw new InvalidDataException("Build continuation storage has an incomplete write.");
            if (history == null)
                Load();
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var entries = source.ToList();
            Validate(entries);
            entries.RemoveAll(entry => entry.Settled && entry.DeadlineUnixMs < now);
            var nextHistory = history.Where(item => item.DeadlineUnixMs >= now).ToList();
            // Every accepted entry reserves a history slot. Settling cannot exhaust the
            // count budget; old exact IDs remain deduplicated until their original deadline.
            ValidateHistory(entries, nextHistory);
            var retired = entries.Where(entry => entry.Settled).Reverse().Skip(MaxRecentTerminalEntries).ToList();
            foreach (BuildQueueEntry entry in retired)
            {
                nextHistory.Add(
                    new BuildQueueTombstone
                    {
                        Id = entry.Id,
                        ChildIds = entry.Children.Select(child => child.JobId).ToArray(),
                        DeadlineUnixMs = entry.DeadlineUnixMs,
                    }
                );
                entries.Remove(entry);
            }
            JArray body = JArray.FromObject(entries);
            JArray retained = JArray.FromObject(nextHistory);
            string json = new JObject
            {
                ["version"] = 2,
                ["project"] = project,
                ["entries"] = body,
                ["history"] = retained,
                ["sha256"] = Hash(new JArray(body, retained).ToString(Formatting.None)),
            }.ToString(Formatting.None);
            if (Encoding.UTF8.GetByteCount(json) > MaxBytes)
                throw new InvalidDataException("Build continuation storage is full. No build was scheduled.");
            // A stale queued record must not become runnable after a failed transition write.
            writeWriting(true);
            if (!readWriting())
                throw new IOException("Build continuation write guard could not be persisted.");
            write(json);
            if (!string.Equals(read(), json, StringComparison.Ordinal))
                throw new IOException("Build continuation checkpoint could not be verified.");
            writeWriting(false);
            if (readWriting())
                throw new IOException("Build continuation write guard could not be cleared.");
            history = nextHistory;
            return entries;
        }

        internal bool ContainsRetainedId(string id) => history != null && history.Any(item => item.Id == id || item.ChildIds.Contains(id));

        private static void ValidateHistory(List<BuildQueueEntry> entries, List<BuildQueueTombstone> retained)
        {
            if (retained == null || entries.Count + retained.Count > MaxHistoryEntries)
                throw new InvalidDataException("Build continuation history capacity exceeded. Wait for retained deadlines before admitting new builds.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (
                var item in entries
                    .Select(entry => new BuildQueueTombstone
                    {
                        Id = entry.Id,
                        ChildIds = entry.Children.Select(child => child.JobId).ToArray(),
                        DeadlineUnixMs = entry.DeadlineUnixMs,
                    })
                    .Concat(retained)
            )
            {
                if (
                    item == null
                    || string.IsNullOrEmpty(item.Id)
                    || item.DeadlineUnixMs <= 0
                    || item.ChildIds == null
                    || item.ChildIds.Length == 0
                    || item.ChildIds.Length > 128
                )
                    throw new InvalidDataException("Build continuation history is invalid.");
                foreach (string id in new[] { item.Id }.Concat(item.ChildIds).Distinct(StringComparer.Ordinal))
                    if (string.IsNullOrEmpty(id) || !ids.Add(id))
                        throw new InvalidDataException("Build continuation history contains duplicate job IDs.");
            }
        }

        private static string Hash(string value)
        {
            using (SHA256 hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }

        private static void Validate(List<BuildQueueEntry> entries)
        {
            if (
                entries == null
                || entries.Count(entry => entry == null || entry.Children == null || entry.Children.Any(child => child == null || child.Phase != "terminal"))
                    > MaxActiveEntries
            )
                throw new InvalidDataException("Build continuation entry limit exceeded.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (BuildQueueEntry entry in entries)
            {
                if (
                    entry == null
                    || string.IsNullOrEmpty(entry.Id)
                    || !ids.Add(entry.Id)
                    || entry.DeadlineUnixMs <= 0
                    || entry.Children == null
                    || entry.Children.Count == 0
                    || entry.Children.Count > 128
                    || (!entry.Batch && entry.Children.Count != 1)
                    || entry.CurrentIndex < 0
                    || entry.CurrentIndex > entry.Children.Count
                )
                    throw new InvalidDataException("Build continuation entry is invalid.");
                var childIds = new HashSet<string>(StringComparer.Ordinal);
                foreach (BuildQueueChild child in entry.Children)
                {
                    if (
                        child == null
                        || string.IsNullOrEmpty(child.JobId)
                        || !childIds.Add(child.JobId)
                        || !Enum.IsDefined(typeof(BuildJobState), child.State)
                        || !new[] { "queued", "preparing", "native_started", "terminal" }.Contains(child.Phase)
                        || (child.Phase == "terminal" && (child.State == BuildJobState.Pending || child.State == BuildJobState.Building))
                    )
                        throw new InvalidDataException("Build continuation child is invalid.");
                }
                if (entry.Children.Take(entry.CurrentIndex).Any(child => child.Phase != "terminal"))
                    throw new InvalidDataException("Build continuation cursor would skip unsettled work.");
            }
        }
    }

    internal sealed class BuildQueueTombstone
    {
        public string Id;
        public string[] ChildIds;
        public long DeadlineUnixMs;
    }

    internal sealed class BuildQueueEntry
    {
        public string Id;
        public bool Batch;
        public bool Cancelled;
        public int CurrentIndex;
        public long DeadlineUnixMs;
        public string ReportId;
        public string ReportPersistenceError;
        public List<BuildQueueChild> Children = new();

        [JsonIgnore]
        internal bool Settled => Children != null && CurrentIndex == Children.Count && Children.All(child => child != null && child.Phase == "terminal");

        internal static BuildQueueEntry Single(BuildJob job, BuildPlayerOptions options) =>
            new BuildQueueEntry
            {
                Id = job.JobId,
                DeadlineUnixMs = DateTimeOffset.UtcNow.AddHours(2).ToUnixTimeMilliseconds(),
                Children = new List<BuildQueueChild> { BuildQueueChild.Capture(job, options) },
            };
    }

    internal sealed class BuildQueueChild
    {
        public string JobId;
        public BuildTarget Target;
        public string OutputPath;
        public BuildPlayerOptions Options;
        public string Phase = "queued";
        public BuildJobState State = BuildJobState.Pending;
        public string Error;
        public DateTime StartedAt;
        public DateTime? CompletedAt;
        public double TotalSizeMb;
        public int TotalErrors;
        public int TotalWarnings;
        public string ReportId;
        public string ReportPersistenceError;
        public bool SwitchTarget;
        public bool UnsupportedPreparation;
        public int? Backend;
        public string ProfilePath;
        public string ProfileGuid;
        public string ProfileHash;
        public bool ProfileOverridesGlobalScenes;
        public JArray ProfileSceneSettings;
        public JObject PlanRequest;
        public string PlanHash;
        public JObject PlanData;

        internal static BuildQueueChild Capture(BuildJob job, BuildPlayerOptions options)
        {
            options.scenes =
                options.scenes == null
                    ? EditorBuildSettings.scenes.Where(scene => scene.enabled).Select(scene => scene.path).ToArray()
                    : (string[])options.scenes.Clone();
            options.extraScriptingDefines = options.extraScriptingDefines == null ? null : (string[])options.extraScriptingDefines.Clone();
            return new BuildQueueChild
            {
                JobId = job.JobId,
                Target = job.Target,
                OutputPath = job.OutputPath,
                Options = options,
            };
        }

        internal void Observe(BuildJob job)
        {
            State = job.State;
            Error = job.ErrorMessage;
            StartedAt = job.StartedAt;
            CompletedAt = job.CompletedAt;
            TotalSizeMb = job.TotalSizeMb;
            TotalErrors = job.TotalErrors;
            TotalWarnings = job.TotalWarnings;
            ReportId = job.ReportId;
            ReportPersistenceError = job.ReportPersistenceError;
        }

        internal BuildJob Restore() =>
            new BuildJob(JobId, Target, OutputPath)
            {
                State = State,
                ErrorMessage = Error,
                StartedAt = StartedAt,
                CompletedAt = CompletedAt,
                TotalSizeMb = TotalSizeMb,
                TotalErrors = TotalErrors,
                TotalWarnings = TotalWarnings,
                ReportId = ReportId,
                ReportPersistenceError = ReportPersistenceError,
            };
    }

    [InitializeOnLoad]
    internal static class BuildQueueCheckpoint
    {
        internal const string StorageKey = "MCPForUnity.Build.QueuedV1";
        internal const string WritingKey = "MCPForUnity.Build.QueuedWritingV1";
        private static readonly BuildQueueJournal Journal = new(
            () => SessionState.GetString(StorageKey, ""),
            value => SessionState.SetString(StorageKey, value),
            () => SessionState.GetBool(WritingKey, false),
            value => SessionState.SetBool(WritingKey, value),
            Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
        );
        private static readonly HashSet<string> Scheduled = new(StringComparer.Ordinal);
        private static readonly Dictionary<string, Action> Preparations = new(StringComparer.Ordinal);
        private static List<BuildQueueEntry> entries;

        static BuildQueueCheckpoint()
        {
            EditorApplication.delayCall += Restore;
        }

        private static List<BuildQueueEntry> Entries => entries ??= Journal.Load();

        internal static void Enqueue(BuildQueueEntry entry, Action beforeBuild = null, IEnumerable<BuildJob> jobs = null, BatchJob batch = null)
        {
            if (Entries.Any(existing => existing.Id == entry.Id || existing.Children.Any(child => entry.Children.Any(next => next.JobId == child.JobId))))
                throw new InvalidOperationException("The build job ID already has a continuation checkpoint.");
            if (new[] { entry.Id }.Concat(entry.Children.Select(child => child.JobId)).Any(Journal.ContainsRetainedId))
                throw new InvalidOperationException("The build job ID already has a retained terminal checkpoint.");
            var next = new List<BuildQueueEntry>(Entries) { entry };
            entries = Journal.Save(next);
            if (batch != null)
                BuildJobStore.AddBatchJob(batch);
            if (jobs != null)
                foreach (BuildJob job in jobs)
                    BuildJobStore.AddBuildJob(job);
            if (beforeBuild != null)
                Preparations.Add(entry.Children[0].JobId, beforeBuild);
            RestoreJobs(entry);
            Scheduled.Add(entry.Id);
            if (entry.Batch)
                BuildRunner.ReserveNative(entry.Id);
            Continue(entry.Id);
        }

        private static void Restore()
        {
            EditorApplication.delayCall -= Restore;
            try
            {
                foreach (BuildQueueEntry entry in Entries)
                {
                    if (Scheduled.Contains(entry.Id))
                        continue;
                    RestoreJobs(entry);
                    if (entry.Children.All(child => child.Phase == "terminal"))
                    {
                        if (!entry.Settled)
                            Continue(entry.Id);
                        continue;
                    }
                    // A previously invoked build owns the recovery fence; queued jobs must
                    // never hide that fence by acquiring an in-memory native reservation.
                    if (BuildRunner.NativeRecoveryRequired)
                        continue;
                    Scheduled.Add(entry.Id);
                    if (entry.Batch)
                        BuildRunner.ReserveNative(entry.Id);
                    Continue(entry.Id);
                }
            }
            catch (Exception error)
            {
                Debug.LogWarning("[MCP Build] Queued build recovery refused: " + error.Message);
            }
        }

        private static void RestoreJobs(BuildQueueEntry entry)
        {
            BatchJob batch = entry.Batch ? BuildJobStore.GetBatchJob(entry.Id) : null;
            if (entry.Batch && batch == null)
            {
                batch = new BatchJob(entry.Id)
                {
                    CurrentIndex = entry.CurrentIndex,
                    State = entry.Cancelled ? BuildJobState.Cancelled : BuildJobState.Building,
                    ReportId = entry.ReportId,
                    ReportPersistenceError = entry.ReportPersistenceError,
                };
                BuildJobStore.AddBatchJob(batch);
            }
            foreach (BuildQueueChild child in entry.Children)
            {
                BuildJob job = BuildJobStore.GetBuildJob(child.JobId);
                if (job == null)
                {
                    job = child.Restore();
                    if (child.Phase == "queued" || child.Phase == "preparing")
                        job.State = BuildJobState.Pending;
                    if (child.Phase == "native_started")
                    {
                        job.State = BuildJobState.Failed;
                        job.ErrorMessage = "Native build invocation was interrupted by reload. It will not be executed again; recovery is required.";
                        job.CompletedAt = DateTime.UtcNow;
                    }
                    BuildJobStore.AddBuildJob(job);
                }
                if (batch != null && !batch.Children.Any(existing => existing.JobId == child.JobId))
                    batch.Children.Add(job);
            }
            if (batch != null && entry.Children.All(child => child.Phase == "terminal"))
                batch.State =
                    entry.Cancelled ? BuildJobState.Cancelled
                    : entry.Children.Any(child => child.State == BuildJobState.Failed) ? BuildJobState.Failed
                    : BuildJobState.Succeeded;
        }

        private static void Continue(string id)
        {
            BuildQueueEntry entry = Entries.SingleOrDefault(item => item.Id == id);
            if (entry == null || entry.Settled)
                return;
            while (entry.CurrentIndex < entry.Children.Count && entry.Children[entry.CurrentIndex].Phase == "terminal")
                entry.CurrentIndex++;
            BatchJob batch = entry.Batch ? BuildJobStore.GetBatchJob(id) : null;
            if (batch != null)
                batch.CurrentIndex = entry.CurrentIndex;
            if (entry.CurrentIndex >= entry.Children.Count)
            {
                if (batch != null)
                {
                    batch.State =
                        entry.Cancelled ? BuildJobState.Cancelled
                        : batch.Children.Any(child => child.State == BuildJobState.Failed) ? BuildJobState.Failed
                        : BuildJobState.Succeeded;
                    BuildReportStore.Complete(batch);
                    entry.ReportId = batch.ReportId;
                    entry.ReportPersistenceError = batch.ReportPersistenceError;
                    foreach (BuildQueueChild completed in entry.Children)
                        completed.Observe(batch.Children.Single(job => job.JobId == completed.JobId));
                }
                entries = Journal.Save(Entries);
                Scheduled.Remove(id);
                BuildRunner.ReleaseNative(id);
                return;
            }
            BuildQueueChild child = entry.Children[entry.CurrentIndex];
            BuildJob job = BuildJobStore.GetBuildJob(child.JobId);
            if (child.Phase == "native_started")
            {
                Scheduled.Remove(id);
                BuildRunner.ReleaseNative(id);
                return;
            }
            if (entry.Cancelled || entry.DeadlineUnixMs <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
            {
                job.State = entry.Cancelled ? (entry.Batch ? BuildJobState.Skipped : BuildJobState.Cancelled) : BuildJobState.Failed;
                job.ErrorMessage = entry.Cancelled
                    ? "Build was explicitly cancelled before native invocation."
                    : "Queued build expired before native invocation.";
                job.CompletedAt = DateTime.UtcNow;
                Complete(job);
                return;
            }
            entries = Journal.Save(Entries); // The current batch cursor exists before any target switch.
            BuildRunner.ScheduleCheckpointBuild(job, child);
        }

        internal static void Prepare(BuildQueueChild child)
        {
            BuildQueueEntry entry = Entries.Single(item => item.Children.Contains(child));
            if (entry.Cancelled || entry.DeadlineUnixMs <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
                throw new InvalidOperationException(entry.Cancelled ? "Build was explicitly cancelled." : "Queued build expired before native invocation.");
            if (child.UnsupportedPreparation && !Preparations.ContainsKey(child.JobId))
                throw new InvalidOperationException("Queued build preparation cannot be reconstructed safely after reload; no native build was invoked.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || BuildPipeline.isBuildingPlayer)
                throw new InvalidOperationException("Queued build cannot start while the Editor is playing or another native build is active.");
            ValidateForeignOwners(entry, child);
            BuildRunner.ValidateOutputPath(child.OutputPath);
            BuildRunner.ValidateScenePaths(child.Options.scenes);
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetMapping.GetTargetGroup(child.Target), child.Target))
                throw new InvalidOperationException($"Build platform '{child.Target}' is no longer installed.");
            ValidatePlan(child);
            ValidateProfile(child);
            child.Phase = "preparing";
            child.Observe(BuildJobStore.GetBuildJob(child.JobId));
            entries = Journal.Save(Entries);
            if (child.SwitchTarget && EditorUserBuildSettings.activeBuildTarget != child.Target)
            {
                if (!EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetMapping.GetTargetGroup(child.Target), child.Target))
                    throw new InvalidOperationException($"Failed to switch to build platform '{child.Target}'.");
            }
            if (child.Backend.HasValue)
            {
                NamedBuildTarget named = BuildTargetMapping.GetNamedBuildTarget(child.Target, (StandaloneBuildSubtarget)child.Options.subtarget);
                var backend = (ScriptingImplementation)child.Backend.Value;
                if (PlayerSettings.GetScriptingBackend(named) != backend)
                    PlayerSettings.SetScriptingBackend(named, backend);
            }
            if (Preparations.TryGetValue(child.JobId, out Action beforeBuild))
                beforeBuild();
        }

        private static void ValidatePlan(BuildQueueChild child)
        {
            if (child.PlanRequest == null)
                return;
            BuildPlan current;
            try
            {
                current = BuildPlan.Capture((JObject)child.PlanRequest.DeepClone());
            }
            catch (BuildPlanException error)
            {
                throw new InvalidOperationException("build_plan_stale: " + error.Message);
            }
            if (string.Equals(current.Sha256, child.PlanHash, StringComparison.OrdinalIgnoreCase))
                return;
            // Only the backend assignment recorded before preparation is ours to repeat.
            // Keep every other observation, including define digest and scene GUIDs, exact.
            JObject actual = current.ToData();
            JObject expected = (JObject)child.PlanData?.DeepClone();
            if (
                child.Phase != "queued"
                && child.Backend.HasValue
                && expected != null
                && (int?)actual["observed"]?["scripting_backend"]?["raw"] == child.Backend
            )
            {
                actual.Remove("plan_sha256");
                expected.Remove("plan_sha256");
                actual["observed"]["scripting_backend"] = expected["observed"]["scripting_backend"].DeepClone();
                if (JToken.DeepEquals(actual, expected))
                    return;
            }
            throw new InvalidOperationException("build_plan_stale: Build observations changed before execution. Request a fresh plan before applying.");
        }

        internal static void ValidateProfile(BuildQueueChild child)
        {
            if (child.ProfilePath == null)
                return;
            if (child.ProfileSceneSettings != null && !JToken.DeepEquals(child.ProfileSceneSettings, CaptureCurrentProfileSceneSettings(child)))
                throw new InvalidOperationException("Build profile scene settings changed while queued. Request a new build.");
            if (
                AssetDatabase.GUIDToAssetPath(child.ProfileGuid) != child.ProfilePath
                || AssetDatabase.AssetPathToGUID(child.ProfilePath) != child.ProfileGuid
                || AssetDatabase.GetAssetDependencyHash(child.ProfilePath).ToString() != child.ProfileHash
            )
                throw new InvalidOperationException("Build profile changed or disappeared while queued. Request a new build.");
        }

        private static JArray CaptureCurrentProfileSceneSettings(BuildQueueChild child)
        {
#if UNITY_6000_0_OR_NEWER
            if (child.ProfileOverridesGlobalScenes)
            {
                var profile = AssetDatabase.LoadAssetAtPath<UnityEditor.Build.Profile.BuildProfile>(child.ProfilePath);
                if (profile == null || !profile.overrideGlobalScenes)
                    throw new InvalidOperationException("Build profile changed or disappeared while queued. Request a new build.");
                return CaptureProfileSceneSettings(profile.scenes);
            }
#endif
            return CaptureProfileSceneSettings();
        }

        internal static JArray CaptureProfileSceneSettings(EditorBuildSettingsScene[] scenes = null) =>
            new JArray(
                (scenes ?? EditorBuildSettings.scenes).Select(scene => new JObject
                {
                    ["path"] = scene.path,
                    ["guid"] = AssetDatabase.AssetPathToGUID(scene.path),
                    ["enabled"] = scene.enabled,
                })
            );

        internal static void ValidateNativeAdmission(BuildQueueChild child)
        {
            BuildQueueEntry entry = Entries.Single(item => item.Children.Contains(child));
            if (entry.Cancelled || entry.DeadlineUnixMs <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
                throw new InvalidOperationException("Build was cancelled or expired before native invocation.");
            if (child.Phase != "preparing")
                throw new InvalidOperationException("Build continuation is not eligible for native invocation.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || BuildPipeline.isBuildingPlayer)
                throw new InvalidOperationException("Queued build cannot invoke native work while the Editor is playing or another native build is active.");
            ValidateForeignOwners(entry, child);
            if (child.SwitchTarget && EditorUserBuildSettings.activeBuildTarget != child.Target)
                throw new InvalidOperationException("Build platform changed after queued preparation. Request a new build.");
            if (child.Backend.HasValue)
            {
                NamedBuildTarget named = BuildTargetMapping.GetNamedBuildTarget(child.Target, (StandaloneBuildSubtarget)child.Options.subtarget);
                if ((int)PlayerSettings.GetScriptingBackend(named) != child.Backend.Value)
                    throw new InvalidOperationException("Scripting backend changed after queued preparation. Request a new build.");
            }
            ValidatePlan(child);
            ValidateProfile(child);
        }

        private static void ValidateForeignOwners(BuildQueueEntry entry, BuildQueueChild child)
        {
            if (NativeOperationBarriers.TryGetBuildContinuationBlocker(child.JobId, entry.Batch ? entry.Id : null, out NativeAdmissionBlocker blocker))
                throw new InvalidOperationException("Queued build native admission blocked: " + blocker.Reason);
        }

        internal static void MarkNativeStarted(BuildQueueChild child)
        {
            ValidateNativeAdmission(child);
            child.Phase = "native_started";
            entries = Journal.Save(Entries);
        }

        internal static void Complete(BuildJob job)
        {
            BuildQueueEntry entry = Entries.FirstOrDefault(item => item.Children.Any(child => child.JobId == job.JobId));
            if (entry == null)
                return;
            // Cancellation and expiry also finish jobs without RunBuildCore. Apply the
            // same bounded public-job retention and report observation on those paths.
            BuildJobStore.SetLastCompleted(job);
            BuildQueueChild child = entry.Children.Single(item => item.JobId == job.JobId);
            child.Observe(job);
            child.Phase = "terminal";
            Preparations.Remove(job.JobId);
            entries = Journal.Save(Entries);
            BuildRunner.ScheduleOnNextUpdate(() => Continue(entry.Id));
        }

        internal static bool Cancel(string id)
        {
            BuildQueueEntry entry = Entries.FirstOrDefault(item => item.Id == id);
            if (entry == null || entry.Settled || (!entry.Batch && (entry.Children[0].Phase == "native_started" || entry.Children[0].Phase == "terminal")))
                return false;
            entry.Cancelled = true;
            entries = Journal.Save(Entries); // Tombstone is durable before reporting cancellation.
            if (entry.Batch)
                BuildJobStore.GetBatchJob(entry.Id).State = BuildJobState.Cancelled;
            foreach (BuildQueueChild child in entry.Children.Where(child => child.Phase != "native_started" && child.Phase != "terminal"))
            {
                BuildJob job = BuildJobStore.GetBuildJob(child.JobId);
                job.State = entry.Batch ? BuildJobState.Skipped : BuildJobState.Cancelled;
                job.CompletedAt = DateTime.UtcNow;
                BuildReportStore.Complete(job);
                child.Observe(job);
                child.Phase = "terminal";
            }
            entries = Journal.Save(Entries);
            return true;
        }
    }
}
