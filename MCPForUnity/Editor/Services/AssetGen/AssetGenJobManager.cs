using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen
{
    public enum AssetGenJobState
    {
        Queued,
        Running,
        Importing,
        Done,
        Failed,
        Canceled,
    }

    /// <summary>
    /// Snapshot of a generation/import job. Safe continuation evidence survives domain reload
    /// and full Editor restart through the bound durable journal. NEVER carries a key or secret.
    /// </summary>
    public sealed class AssetGenJob
    {
        public string JobId;
        public string Kind; // model | image | audio | marketplace
        public string Provider;
        public string Action;
        public AssetGenJobState State;
        public float Progress;
        public string Format;
        public float TargetSize = 1f;
        public string AnimationType; // FBX/OBJ rig mode: null/none | generic | humanoid | legacy
        public string AssetPath;
        public string AssetGuid;

        // Model evidence only: saved bytes, registered GUID, or a hierarchy with indexed mesh geometry.
        // Null means this job has not reached model import; usable does not certify materials, rigs or rendering.
        public string ModelImportStage;
        public string ModelImportErrorCode;
        public string ArchiveModelPath;
        public JObject ArchiveSelection;
        public string Error;
        public string RecoveryState;
        public string RecoveryCode;
        public string ProviderTaskId;
        public string ProviderPhase;

        // Session-local telemetry is not a durable execution checkpoint.
        [JsonIgnore]
        internal AssetOperationProgress ProgressEvidence;
    }

    /// <summary>
    /// Drives asset-generation jobs on the Unity main thread via EditorApplication.update. Each
    /// job runs a generic submit → poll → (download | inline) → import state machine; the model
    /// and image paths supply their own submit/poll/import delegates. Because UnityWebRequest
    /// completes on the main thread, polling Task.IsCompleted from the update loop is
    /// main-thread-safe — we never block or use threadpool waits. The provider key is read once at
    /// submit time, captured only inside the submit/poll closures — never stored on the job,
    /// persisted, or logged.
    /// </summary>
    [InitializeOnLoad]
    public static class AssetGenJobManager
    {
        private const string JobKeyPrefix = "MCPForUnity.AssetGen.Job.";
        private const string JobIndexKey = "MCPForUnity.AssetGen.JobIndex";
        internal const int MaxTerminalJobs = 100;

        // Test seams (overridable; defaults are the production implementations).
        internal static IHttpTransport TransportOverrideForTests;
        internal static Func<AssetGenJob, string, AssetGenJob> ImportOverrideForTests;
        internal static double PollIntervalSeconds = 3.0;
        internal static double TimeoutSeconds = AssetGenJobCheckpoint.MaxDurationSeconds;
        internal static bool SkipModelVerificationForTests;
        internal static Func<double> ClockForTests = null;
        internal static Action<string> BeforeCheckpointWriteForTests = null;

        private static AssetGenJobJournal _journal;
        private static string _journalRootForTests;
        private static string _journalOwnerForTests;
        private static string _journalInstanceForTests;
        private static Func<DateTimeOffset> _journalUtcForTests;
        private static string _journalError;

        // Existing fake-transport fixtures remain session-only unless they explicitly select an
        // owned journal. Resetting the transport restores production persistence automatically.
        private static bool JournalEnabled => _journalRootForTests != null || TransportOverrideForTests == null;

        private static AssetGenJobJournal Journal
        {
            get
            {
                if (_journal != null)
                    return _journal;
                string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
                string owner =
                    _journalOwnerForTests
                    ?? AtomicAssetFile.Digest(
                        Encoding.UTF8.GetBytes(project + "\n" + Environment.MachineName + "\n" + Environment.UserDomainName + "\n" + Environment.UserName)
                    );
                const string instanceKey = "MCPForUnity.AssetGen.JournalInstance";
                string instance = _journalInstanceForTests ?? SessionState.GetString(instanceKey, "");
                if (!AssetGenJobCheckpoint.Hex(instance, 32))
                {
                    instance = Guid.NewGuid().ToString("N");
                    SessionState.SetString(instanceKey, instance);
                }
                string directory =
                    _journalRootForTests
                    ?? SafePathUtility.ResolveWithinRoot(project, Path.Combine("Library", "MCPForUnity", "AssetGen", owner.Substring(0, 32)));
                return _journal = new AssetGenJobJournal(directory, owner, instance, _journalUtcForTests ?? (() => DateTimeOffset.UtcNow));
            }
        }

        internal static void ConfigureDurableStoreForTests(string directory, string owner, string instance, Func<DateTimeOffset> utc)
        {
            _journal?.Dispose();
            _journal = null;
            _journalRootForTests = directory;
            _journalOwnerForTests = owner;
            _journalInstanceForTests = instance;
            _journalUtcForTests = utc;
            _journalError = null;
        }

        private static readonly Dictionary<string, AssetGenJob> Jobs = new();
        private static readonly Dictionary<string, Runner> Runners = new();
        private static readonly Dictionary<string, AssetGenJobCheckpoint> Checkpoints = new();
        private static long _epoch;

        // Keep the 100 most recently finished jobs, plus every active job.
        // Evicted IDs become unknown; generated assets/files are never removed here.
        private static readonly LinkedList<string> JobOrder = new();
        private static readonly Dictionary<string, LinkedListNode<string>> JobOrderNodes = new();
        private static readonly HashSet<string> TerminalJobIds = new();
        private static bool _jobIndexDirty;
        private static readonly List<string> _tickIds = new();
        private static bool _ticking;

        static AssetGenJobManager()
        {
            AssemblyReloadEvents.beforeAssemblyReload += PrepareForAssemblyReload;
            EditorApplication.quitting += PrepareForAssemblyReload;
            RestoreJobs();
        }

        internal static void RestoreJobs()
        {
            if (JournalEnabled)
            {
                try
                {
                    var records = Journal.Read(Now());
                    if (records != null)
                    {
                        var durableIds = new HashSet<string>(records.Select(r => (string)r["JobId"]), StringComparer.Ordinal);
                        string sessionIndex = SessionState.GetString(JobIndexKey, "");
                        // Same-process legacy/history entries can coexist with new durable jobs.
                        // Conflicting IDs still use verified disk evidence, never a stale session copy.
                        string[] sessionOnly =
                            Encoding.UTF8.GetByteCount(sessionIndex) <= AssetGenJobCheckpoint.MaxIndexBytes
                                ? sessionIndex.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries).Where(id => !durableIds.Contains(id)).ToArray()
                                : Array.Empty<string>();
                        foreach (var record in records)
                            SessionState.SetString(JobKeyPrefix + (string)record["JobId"], record.ToString(Formatting.None));
                        SessionState.SetString(JobIndexKey, string.Join(",", records.Select(r => (string)r["JobId"]).Concat(sessionOnly)));
                    }
                    _journalError = null;
                }
                catch (Exception)
                {
                    _journalError =
                        "Durable asset-generation recovery is unavailable or owned by another instance. Retained records must be verified before new work.";
                    McpLog.Warn(_journalError);
                    return;
                }
            }
            string index = SessionState.GetString(JobIndexKey, string.Empty);
            if (string.IsNullOrEmpty(index) || Encoding.UTF8.GetByteCount(index) > AssetGenJobCheckpoint.MaxIndexBytes)
                return;
            string[] ids = index.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
            var duplicate = new HashSet<string>(ids.GroupBy(id => id, StringComparer.Ordinal).Where(g => g.Count() > 1).Select(g => g.Key));
            var interrupted = new List<AssetGenJob>();
            foreach (string id in ids.Distinct(StringComparer.Ordinal))
            {
                if (!AssetGenJobCheckpoint.SafeId(id))
                    continue;
                AssetGenJob job = null;
                try
                {
                    JObject record = AssetGenJobCheckpoint.ParseRecord(SessionState.GetString(JobKeyPrefix + id, string.Empty));
                    job = record.ToObject<AssetGenJob>();
                    if (job == null || job.JobId != id || !Enum.IsDefined(typeof(AssetGenJobState), job.State))
                        continue;
                    Jobs[id] = job;
                    if (!IsTerminal(job.State))
                    {
                        AssetGenJobCheckpoint cp = duplicate.Contains(id) ? null : AssetGenJobCheckpoint.Read(record, id, Now());
                        if (duplicate.Contains(id))
                            throw new InvalidDataException("duplicate_job_record");
                        if (cp == null)
                        {
                            RecoveryFailure(job, "legacy_reload_interrupted", "outcome_unknown");
                            interrupted.Add(job);
                            continue;
                        }
                        if (cp.Kind != job.Kind || cp.Provider != job.Provider || !AssetGenJobCheckpoint.Finite(job.TargetSize))
                            throw new InvalidDataException("checkpoint_identity_invalid");
                        job.ArchiveModelPath = ModelArchiveSelection.NormalizeSelector(job.ArchiveModelPath);
                        Checkpoints[id] = cp;
                        if (cp.SavedPath != null)
                        {
                            // Writing intent may be the only retained path after an interrupted partial write.
                            job.AssetPath ??= cp.SavedPath;
                            job.AssetGuid ??= cp.RegisteredGuid;
                            if ((cp.Phase == "saved" || cp.Phase == "importing") && (job.Kind == "model" || job.Kind == "marketplace"))
                                job.ModelImportStage ??= "saved";
                        }
                        UpdateProviderEvidence(job, cp);
                        string failure = RestoreFailure(cp);
                        if (failure != null || Runners.Count >= AssetGenJobCheckpoint.MaxActive)
                        {
                            RecoveryFailure(job, failure ?? "checkpoint_invalid", failure == "reload_before_submission" ? "not_submitted" : "outcome_unknown");
                            interrupted.Add(job);
                            continue;
                        }
                        job.ProgressEvidence = new AssetOperationProgress();
                        job.ProgressEvidence.Begin(AssetOperationPhase.Reconnecting);
                        var runner = RunnerFromCheckpoint(job, cp);
                        Runners[id] = runner;
                        job.RecoveryState = "resuming";
                        job.RecoveryCode = "resumed";
                        UpdateProviderEvidence(runner);
                    }
                    TrackJob(job);
                }
                catch
                {
                    if (job == null)
                        continue;
                    RecoveryFailure(job, "checkpoint_invalid", "unavailable");
                    interrupted.Add(job);
                }
            }
            // Newly interrupted jobs finish after older terminal history; preserve the existing retention ordering.
            foreach (var job in interrupted)
                Persist(job);
            try
            {
                WriteJobIndex();
            }
            catch { /* metadata recovery is best-effort */ }
            if (Runners.Count > 0)
                EnsureTicking();
            OperationActivity.Invalidate();
        }

        private static string RestoreFailure(AssetGenJobCheckpoint cp) =>
            cp.Phase switch
            {
                "preparing" => "reload_before_submission",
                "submitting" => "submission_outcome_unknown",
                "refine_submitting" => "refine_outcome_unknown",
                "writing" => "local_write_outcome_unknown",
                "importing" => "import_outcome_unknown",
                "nonresumable" => cp.Provider == "openrouter" ? "synchronous_result_unavailable_after_reload" : "receipt_not_resumable",
                "polling" or "refining" or "downloading" or "saved" => null,
                _ => "checkpoint_invalid",
            };

        private static Runner RunnerFromCheckpoint(AssetGenJob job, AssetGenJobCheckpoint cp) =>
            new Runner
            {
                Job = job,
                Checkpoint = cp,
                Epoch = _epoch,
                Phase = RunnerPhase.Reconnect,
                StartedAt = cp.StartedAt,
                OutputFolder = cp.OutputFolder,
                Ext = cp.Extension,
                Name = cp.Name,
                Subfolder = cp.Subfolder,
                AsSprite = cp.AsSprite,
                Transparent = cp.Transparent,
                MarketplaceUid = cp.MarketplaceUid,
                LocalPath = cp.SavedPath,
                ProviderJobId = cp.Provider == "sketchfab" ? null : cp.Receipt,
            };

        private static void RecoveryFailure(AssetGenJob job, string code, string state = "unavailable")
        {
            job.State = AssetGenJobState.Failed;
            job.RecoveryState = state;
            job.RecoveryCode = code;
            job.Error = AssetGenJobCheckpoint.ErrorSummary(code);
        }

        private static bool RejectInvalidTarget(AssetGenJob job)
        {
            if (AssetGenJobCheckpoint.Finite(job.TargetSize))
                return false;
            // Never turn a non-finite requested size into a resumable finite default during safe projection.
            RecoveryFailure(job, "checkpoint_invalid", "not_submitted");
            job.ModelImportErrorCode = "invalid_target_size";
            job.Error = "target_size must be finite.";
            Jobs[job.JobId] = job;
            Persist(job);
            return true;
        }

        private static void EnsureCapacity()
        {
            if (JournalEnabled && _journalError != null)
                throw new IOException(_journalError);
            if (Jobs.Values.Count(job => !IsTerminal(job.State)) >= AssetGenJobCheckpoint.MaxActive)
                throw new InvalidOperationException("At most 64 active generation jobs are supported. Finish or cancel an existing job first.");
        }

        private static void PrepareForAssemblyReload()
        {
            _epoch++;
            EditorApplication.update -= Tick;
            _ticking = false;
            foreach (var runner in Runners.Values.ToArray())
            {
                try
                {
                    runner.Job.ProgressEvidence?.Stop();
                    runner.Cts.Cancel();
                    runner.Cts.Dispose();
                }
                catch { }
                // Observe faults without letting a stale continuation publish metadata or files.
                Observe(runner.SubmitTask);
                Observe(runner.PollTask);
                ObserveDownload(runner.DownloadTask);
                ObservePreparation(runner.PreparationTask);
            }
            Runners.Clear();
            _journal?.Dispose();
            _journal = null;
            OperationActivity.Invalidate();
        }

        private static void ObserveDownload(Task<HttpResult> task)
        {
            if (task != null)
                _ = task.ContinueWith(
                    t =>
                    {
                        if (t.Status == TaskStatus.RanToCompletion)
                            t.Result?.Dispose();
                        else if (t.IsFaulted)
                            _ = t.Exception;
                    },
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default
                );
        }

        private static void ObservePreparation(Task<PreparedArtifact> task)
        {
            if (task != null)
                _ = task.ContinueWith(
                    t =>
                    {
                        if (t.Status == TaskStatus.RanToCompletion)
                            t.Result?.Dispose();
                        else if (t.IsFaulted)
                            _ = t.Exception;
                    },
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default
                );
        }

        private static void Observe(Task task)
        {
            if (task != null)
                _ = task.ContinueWith(
                    t =>
                    {
                        _ = t.Exception;
                    },
                    CancellationToken.None,
                    TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default
                );
        }

        public static AssetGenJob StartModelGeneration(ModelGenRequest req)
        {
            if (req == null)
                throw new ArgumentNullException(nameof(req));
            EnsureCapacity();
            req.OutputFolder = AssetGenPaths.ResolveOutputFolder(req.OutputFolder, "Models");
            string provider = string.IsNullOrEmpty(req.Provider) ? "tripo" : req.Provider.ToLowerInvariant();
            IModelProviderAdapter adapter = AssetGenProviders.Model(provider); // throws NotSupportedException if unimplemented

            var job = NewJob("model", provider, "generate");
            job.Format = string.IsNullOrEmpty(req.Format) ? "glb" : req.Format;
            job.TargetSize = req.TargetSize;
            if (RejectInvalidTarget(job))
                return job;

            if (!TryResolveKey(provider, job, out string apiKey))
                return job;

            IHttpTransport transport = CreateJobTransport(job.JobId);
            var runner = new Runner
            {
                Job = job,
                SubmitFn = async ct =>
                {
                    if (string.Equals(provider, "fal", StringComparison.OrdinalIgnoreCase) && !SkipModelVerificationForTests)
                    {
                        req.Model = AssetGenModelCatalog.ResolveModel("model", provider, req.Model);
                        req.CatalogEntry = await FalModelCatalog.VerifyForGeneration(req.Model, "model", req.Mode, ct, apiKey);
                    }
                    AdmitSubmission(job);
                    string providerJobId = adapter is TripoAdapter tripo
                        ? await tripo.SubmitAsync(req, apiKey, transport, ct, () => AdmitSubmission(job))
                        : await adapter.SubmitAsync(req, apiKey, transport, ct);
                    req = null; // Submission is the final consumer; polling/import share this closure.
                    return providerJobId;
                },
                PollFn = (pid, ct) => adapter.PollAsync(pid, apiKey, transport, ct),
                ImportFn = ImportOverrideForTests ?? ModelImportPipeline.ImportInto,
                Transport = transport,
                OutputFolder = req.OutputFolder,
                Ext = (job.Format ?? "glb").TrimStart('.'),
                Name = NameFrom(req.Name, req.Prompt, job.JobId),
                Subfolder = "Models",
                ModelAdapter = adapter,
            };
            Register(job, runner);
            return job;
        }

        public static AssetGenJob StartImageGeneration(ImageGenRequest req)
        {
            if (req == null)
                throw new ArgumentNullException(nameof(req));
            EnsureCapacity();
            req.OutputFolder = AssetGenPaths.ResolveOutputFolder(req.OutputFolder, "Images");
            string provider = string.IsNullOrEmpty(req.Provider) ? "fal" : req.Provider.ToLowerInvariant();
            IImageProviderAdapter adapter = AssetGenProviders.Image(provider); // throws NotSupportedException if unimplemented

            var job = NewJob("image", provider, "generate");
            job.Format = "png";

            if (!TryResolveKey(provider, job, out string apiKey))
                return job;

            IHttpTransport transport = CreateJobTransport(job.JobId);
            bool asSprite = req.AsSprite;
            bool transparent = req.Transparent;
            var runner = new Runner
            {
                Job = job,
                SubmitFn = async ct =>
                {
                    if (!SkipModelVerificationForTests && string.Equals(provider, "fal", StringComparison.OrdinalIgnoreCase))
                    {
                        req.Model = AssetGenModelCatalog.ResolveModel("image", provider, req.Model);
                        req.CatalogEntry = await FalModelCatalog.VerifyForGeneration(req.Model, "image", req.Mode, ct, apiKey);
                    }
                    if (!SkipModelVerificationForTests && string.Equals(provider, "openrouter", StringComparison.OrdinalIgnoreCase))
                    {
                        req.Model = AssetGenModelCatalog.ResolveModel("image", provider, req.Model);
                        req.CatalogEntry = await OpenRouterModelCatalog.VerifyForGeneration(req.Model, req.Mode, ct);
                    }
                    AdmitSubmission(job);
                    string providerJobId = await adapter.SubmitAsync(req, apiKey, transport, ct);
                    req = null; // Submission is the final consumer; polling/import share this closure.
                    return providerJobId;
                },
                PollFn = (pid, ct) => adapter.PollAsync(pid, apiKey, transport, ct),
                ImportFn = ImportOverrideForTests ?? CreateImageImporter(asSprite, transparent),
                Transport = transport,
                OutputFolder = req.OutputFolder,
                Ext = "png",
                Name = NameFrom(req.Name, req.Prompt, job.JobId),
                Subfolder = "Images",
                AsSprite = asSprite,
                Transparent = transparent,
            };
            Register(job, runner);
            return job;
        }

        // Import keeps only its options, not the provider closure and its completed result.
        private static Func<AssetGenJob, string, AssetGenJob> CreateImageImporter(bool asSprite, bool transparent) =>
            (job, path) => ImageImportPipeline.ImportInto(job, path, asSprite, transparent, isColor: true);

        public static AssetGenJob StartAudioGeneration(AudioGenRequest req)
        {
            if (req == null)
                throw new ArgumentNullException(nameof(req));
            EnsureCapacity();
            req.OutputFolder = AssetGenPaths.ResolveOutputFolder(req.OutputFolder, "Audio");
            string provider = string.IsNullOrEmpty(req.Provider) ? "fal" : req.Provider.ToLowerInvariant();
            IAudioProviderAdapter adapter = AssetGenProviders.Audio(provider); // throws NotSupportedException if unimplemented

            var job = NewJob("audio", provider, "generate");
            job.Format = "wav";

            if (!TryResolveKey(provider, job, out string apiKey))
                return job;

            IHttpTransport transport = CreateJobTransport(job.JobId);
            var runner = new Runner
            {
                Job = job,
                SubmitFn = async ct =>
                {
                    if (!SkipModelVerificationForTests)
                    {
                        req.Model = AssetGenModelCatalog.ResolveModel("audio", provider, req.Model);
                        req.CatalogEntry = await FalModelCatalog.VerifyForGeneration(req.Model, "audio", "text", ct, apiKey);
                    }
                    AdmitSubmission(job);
                    string providerJobId = await adapter.SubmitAsync(req, apiKey, transport, ct);
                    req = null; // Submission is the final consumer; polling/import share this closure.
                    return providerJobId;
                },
                PollFn = (pid, ct) => adapter.PollAsync(pid, apiKey, transport, ct),
                ImportFn = ImportOverrideForTests ?? AudioImportPipeline.ImportInto,
                Transport = transport,
                OutputFolder = req.OutputFolder,
                Ext = "wav", // default; the poll's ResultExt (wav/mp3) overrides at write time
                Name = NameFrom(req.Name, req.Prompt, job.JobId),
                Subfolder = "Audio",
            };
            Register(job, runner);
            return job;
        }

        public static AssetGenJob StartMarketplaceImport(string uid, float targetSize, string name, string outputFolder, string archiveModelPath = null)
        {
            if (!SketchfabAdapter.TryNormalizeModelUid(uid, out string canonicalUid))
                throw new ArgumentException("'uid' must be a 32-character hexadecimal Sketchfab model ID.", nameof(uid));
            uid = canonicalUid;
            EnsureCapacity();
            archiveModelPath = ModelArchiveSelection.NormalizeSelector(archiveModelPath);
            outputFolder = AssetGenPaths.ResolveOutputFolder(outputFolder, "Sketchfab");
            var adapter = AssetGenProviders.Marketplace("sketchfab"); // throws NotSupported if unimplemented
            var job = NewJob("marketplace", "sketchfab", "import");
            job.ArchiveModelPath = archiveModelPath;
            job.TargetSize = targetSize;
            if (RejectInvalidTarget(job))
                return job;
            if (!TryResolveKey("sketchfab", job, out string apiKey))
                return job;
            IHttpTransport transport = CreateJobTransport(job.JobId);
            var runner = new Runner
            {
                Job = job,
                SubmitFn = ct => adapter.ResolveDownloadUrlAsync(uid, apiKey, transport, ct), // returns the zip/gltf URL as providerJobId
                PollFn = (pid, ct) =>
                    Task.FromResult(
                        new ProviderPollResult
                        {
                            State = ProviderPollState.Succeeded,
                            Progress = 1f,
                            DownloadUrl = pid,
                            ResultExt = "zip",
                        }
                    ),
                ImportFn = ImportOverrideForTests ?? ModelImportPipeline.ImportInto,
                Transport = transport,
                OutputFolder = outputFolder,
                Ext = "zip",
                Name = NameFrom(name, uid, job.JobId),
                Subfolder = "Sketchfab",
                MarketplaceUid = uid,
            };
            Register(job, runner);
            return job;
        }

        internal static void AppendActivity(List<OperationActivityRow> rows)
        {
            foreach (var job in Jobs.Values)
            {
                Runners.TryGetValue(job.JobId, out var runner);
                var progress = job.ProgressEvidence?.Read();
                bool canceled = job.State == AssetGenJobState.Canceled || progress?.cancellation_requested == true;
                string detail = "Recovery: " + (job.RecoveryState ?? "not observed") + (job.RecoveryCode == null ? "" : " · " + job.RecoveryCode);
                if (progress?.completed != null)
                    detail += " · Downloaded " + progress.Value.completed + (progress.Value.total.HasValue ? " / " + progress.Value.total : "") + " bytes";
                if (job.ModelImportStage != null)
                    detail += " · Import: " + job.ModelImportStage;
                rows.Add(
                    new OperationActivityRow
                    {
                        Owner = "generation",
                        Id = job.JobId,
                        Operation = "Asset generation · " + job.Kind + " · " + job.Provider,
                        State = job.RecoveryState == "outcome_unknown" ? "unknown" : job.State.ToString().ToLowerInvariant(),
                        Phase = progress?.phase ?? job.ProviderPhase ?? job.State.ToString().ToLowerInvariant(),
                        Detail = detail,
                        Active = runner != null || !IsTerminal(job.State),
                        CanCancel = !canceled && (runner != null || job.State == AssetGenJobState.Queued),
                        Cancellation =
                            canceled
                                ? "Local cancellation requested; provider completion is not confirmed"
                                    + (runner?.ImportInProgress == true ? " · local import is still running" : "")
                            : runner != null || job.State == AssetGenJobState.Queued ? "Local cancellation is available; remote provider work may continue"
                            : "No supported local cancellation owner; inspect provider evidence",
                        EvidenceId = job.JobId,
                    }
                );
            }
        }

        internal static object ActivityEvidence(string jobId)
        {
            var job = GetJob(jobId);
            if (job == null)
                return new { observation = "Generation job is no longer retained; no operation was started or resumed." };
            return new
            {
                job_id = job.JobId,
                state = job.State.ToString().ToLowerInvariant(),
                recovery_state = job.RecoveryState,
                recovery_code = job.RecoveryCode,
                provider_task_id = AssetGenJobCheckpoint.SafeId(job.ProviderTaskId) ? job.ProviderTaskId : null,
                provider_phase = job.ProviderPhase,
                asset_path = job.AssetPath,
                asset_guid = job.AssetGuid,
                model_import_stage = job.ModelImportStage,
                model_import_error_code = job.ModelImportErrorCode,
                progress_detail = job.ProgressEvidence?.Read(),
                local_runner_owned = Runners.ContainsKey(jobId),
                observation = "Existing owner observation only. Local cancellation does not confirm remote provider completion. Use the generation tool status for full result details.",
            };
        }

        public static AssetGenJob GetJob(string jobId)
        {
            if (string.IsNullOrEmpty(jobId))
                return null;
            if (Jobs.TryGetValue(jobId, out var job))
                return job;
            if (JournalEnabled && _journalError != null)
                throw new IOException(_journalError);
            return null;
        }

        /// <summary>Most-recent-first snapshot of known jobs (for the GUI readout). Never contains keys.</summary>
        public static IReadOnlyList<AssetGenJob> RecentJobs(int max = 20)
        {
            var recent = new List<AssetGenJob>();
            for (var node = JobOrder.Last; node != null && recent.Count < max; node = node.Previous)
                if (Jobs.TryGetValue(node.Value, out var job))
                    recent.Add(job);
            return recent;
        }

        public static bool Cancel(string jobId)
        {
            if (string.IsNullOrEmpty(jobId))
                return false;
            if (Runners.TryGetValue(jobId, out var r))
            {
                r.Job.ProgressEvidence?.RequestCancellation();
                r.Canceled = true;
                r.Job.State = AssetGenJobState.Canceled;
                r.Job.RecoveryState = "terminal";
                r.Job.RecoveryCode = "canceled";
                bool persisted = true;
                try
                {
                    PersistRequired(r.Job, r.Checkpoint);
                }
                catch
                {
                    persisted = false;
                }
                try
                {
                    r.Cts.Cancel();
                }
                catch { }
                if (!r.ImportInProgress)
                    Finalize(r);
                return persisted;
            }
            if (Jobs.TryGetValue(jobId, out var job) && job.State == AssetGenJobState.Queued)
            {
                job.ProgressEvidence?.RequestCancellation();
                job.State = AssetGenJobState.Canceled;
                Persist(job);
                return true;
            }
            return false;
        }

        // ---------- runner ----------

        private enum RunnerPhase
        {
            Reconnect,
            Submit,
            AwaitSubmit,
            Poll,
            AwaitPoll,
            Download,
            AwaitDownload,
            AwaitPreparation,
            Import,
        }

        private sealed class Runner
        {
            public AssetGenJob Job;
            public AssetGenJobCheckpoint Checkpoint;
            public long Epoch;
            public IModelProviderAdapter ModelAdapter;
            public bool AsSprite;
            public bool Transparent;
            public string MarketplaceUid;
            public Func<CancellationToken, Task<string>> SubmitFn;
            public Func<string, CancellationToken, Task<ProviderPollResult>> PollFn;
            public Func<AssetGenJob, string, AssetGenJob> ImportFn;
            public IHttpTransport Transport;
            public string OutputFolder;
            public string Ext;
            public string OverrideExt;
            public string Name;
            public string Subfolder;

            public CancellationTokenSource Cts = new();
            public RunnerPhase Phase = RunnerPhase.Submit;
            public double StartedAt;
            public double NextPollAt;
            public string ProviderJobId;
            public string DownloadUrl;
            public string LocalPath;
            public Task<string> SubmitTask;
            public Task<ProviderPollResult> PollTask;
            public Task<HttpResult> DownloadTask;
            public Task<PreparedArtifact> PreparationTask;
            public bool Canceled;
            public bool ImportInProgress;
            public AssetOperationProgress.Snapshot? ActivityObservation;
        }

        private static bool TryResolveKey(string provider, AssetGenJob job, out string apiKey)
        {
            if (!SecureKeyStore.Current.TryGet(provider, out apiKey) || string.IsNullOrEmpty(apiKey))
            {
                job.State = AssetGenJobState.Failed;
                job.Error = AssetGenProviders.MissingKeyMessage(provider);
                Jobs[job.JobId] = job;
                Persist(job);
                return false;
            }
            return true;
        }

        private static void Register(AssetGenJob job, Runner runner)
        {
            runner.StartedAt = Now();
            runner.Epoch = _epoch;
            runner.Checkpoint = new AssetGenJobCheckpoint
            {
                JobId = job.JobId,
                Kind = job.Kind,
                Provider = job.Provider,
                StartedAt = runner.StartedAt,
                Deadline = runner.StartedAt + Math.Min(AssetGenJobCheckpoint.MaxDurationSeconds, Math.Max(0, TimeoutSeconds)),
                OutputFolder = runner.OutputFolder,
                Name = SanitizeName(runner.Name),
                Extension = runner.Ext,
                Subfolder = runner.Subfolder,
                AsSprite = runner.AsSprite,
                Transparent = runner.Transparent,
                MarketplaceUid = AssetGenJobCheckpoint.SafeId(runner.MarketplaceUid, 128) ? runner.MarketplaceUid : null,
            };
            Jobs[job.JobId] = job;
            Runners[job.JobId] = runner;
            Checkpoints[job.JobId] = runner.Checkpoint;
            AttachMeshyCheckpoint(runner);
            try
            {
                PersistRequired(job, runner.Checkpoint);
                EnsureTicking();
            }
            catch
            {
                RecoveryFailure(job, "checkpoint_write_failed", "not_submitted");
                Finalize(runner);
                Persist(job);
            }
        }

        private static void EnsureTicking()
        {
            if (_ticking)
                return;
            EditorApplication.update += Tick;
            _ticking = true;
        }

        private static void Tick()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
                return;
            if (Runners.Count == 0)
            {
                EditorApplication.update -= Tick;
                _ticking = false;
                return;
            }
            // Snapshot keys into a reused buffer so Advance can mutate Runners mid-iteration
            // without churning the GC on every editor-update frame.
            _tickIds.Clear();
            _tickIds.AddRange(Runners.Keys);
            foreach (string id in _tickIds)
            {
                if (Runners.TryGetValue(id, out var r))
                {
                    Advance(r);
                    ObserveActivity(r);
                }
            }
        }

        private static void ObserveActivity(Runner runner)
        {
            var current = runner.Job.ProgressEvidence?.Read();
            var previous = runner.ActivityObservation;
            if (
                current.HasValue
                && (
                    !previous.HasValue
                    || current.Value.phase_sequence != previous.Value.phase_sequence
                    || current.Value.phase != previous.Value.phase
                    || current.Value.completed != previous.Value.completed
                    || current.Value.total != previous.Value.total
                    || current.Value.active != previous.Value.active
                    || current.Value.cancellation_requested != previous.Value.cancellation_requested
                )
            )
                OperationActivity.Invalidate();
            runner.ActivityObservation = current;
        }

        /// <summary>Advance one job one step. Returns true when the job is terminal.</summary>
        internal static bool TryAdvanceForTests(string jobId)
        {
            if (Runners.TryGetValue(jobId, out var r))
            {
                Advance(r);
                ObserveActivity(r);
                return IsTerminal(r.Job.State);
            }
            return Jobs.TryGetValue(jobId, out var j) && IsTerminal(j.State);
        }

        private static IHttpTransport CreateJobTransport(string jobId) =>
            new JobHttpTransport(jobId, _epoch, TransportOverrideForTests ?? new UnityWebRequestTransport());

        // Look up the live owner for every send, including later uploads/refinement and recovery.
        // The wrapper retains only identity, never the runner or its completed tasks and payloads.
        private sealed class JobHttpTransport : IHttpTransport
        {
            private readonly string _jobId;
            private readonly long _ownerEpoch;
            private readonly IHttpTransport _inner;

            internal JobHttpTransport(string jobId, long ownerEpoch, IHttpTransport inner)
            {
                _jobId = jobId;
                _ownerEpoch = ownerEpoch;
                _inner = inner;
            }

            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
            {
                if (spec == null)
                    throw new ArgumentNullException(nameof(spec));
                ct.ThrowIfCancellationRequested();
                if (_ownerEpoch != _epoch || !Runners.TryGetValue(_jobId, out var runner))
                    throw new OperationCanceledException();
                Admit(runner);
                if (spec.DownloadProvider != null)
                    return _inner.SendAsync(spec, ct);

                double remaining = runner.Checkpoint.Deadline - Now();
                if (remaining <= 0)
                    throw new RecoveryAbort("job_timeout");
                bool read =
                    string.IsNullOrEmpty(spec.Method)
                    || string.Equals(spec.Method, "GET", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(spec.Method, "HEAD", StringComparison.OrdinalIgnoreCase);
                // POST/PUT can upload large input, wait for a receipt, or synchronously generate.
                // Poll/metadata reads keep the short default; adapters may explicitly override it.
                double requested = spec.TimeoutSeconds ?? (read ? 120 : AssetGenJobCheckpoint.MaxDurationSeconds);
                UnityWebRequestTransport.ResolveTimeoutSeconds(requested); // Reject invalid policy before clamping.
                return _inner.SendAsync(spec.WithTimeout(Math.Min(requested, remaining)), ct);
            }
        }

        private sealed class RecoveryAbort : Exception
        {
            internal readonly string Code;

            internal RecoveryAbort(string code)
                : base(AssetGenJobCheckpoint.ErrorSummary(code)) => Code = code;
        }

        private static bool Current(Runner r) =>
            r.Epoch == _epoch
            && Runners.TryGetValue(r.Job.JobId, out var current)
            && ReferenceEquals(r, current)
            && Jobs.TryGetValue(r.Job.JobId, out var job)
            && ReferenceEquals(job, r.Job);

        private static void Admit(Runner r)
        {
            if (!Current(r) || r.Canceled || r.Cts.IsCancellationRequested || IsTerminal(r.Job.State))
                throw new OperationCanceledException();
            if (Now() >= r.Checkpoint.Deadline)
                throw new RecoveryAbort("job_timeout");
        }

        private static void AdmitSubmission(AssetGenJob job)
        {
            if (!Runners.TryGetValue(job.JobId, out var runner) || !ReferenceEquals(runner.Job, job))
                throw new OperationCanceledException();
            Admit(runner);
            runner.Checkpoint.Phase = "submitting";
            runner.Job.ProgressEvidence?.Begin(AssetOperationPhase.Submitting);
            runner.Job.ProviderPhase = "submit";
            PersistRequired(job, runner.Checkpoint);
            Admit(runner);
        }

        private static async Task<string> SubmitAndCheckpoint(Runner r)
        {
            string receipt = await r.SubmitFn(r.Cts.Token);
            Admit(r);
            r.ProviderJobId = receipt;
            AssetGenJobCheckpoint cp = r.Checkpoint;
            cp.Meshy = (r.ModelAdapter as MeshyAdapter)?.CaptureRecovery();
            cp.FalOutputField = (r.ModelAdapter as FalModelAdapter)?.CaptureOutputField();
            cp.Receipt = r.Job.Provider == "sketchfab" ? null : receipt;
            cp.Phase = "polling";
            if (!cp.Validate(r.Job.JobId, Now()))
            {
                // Current live work can use its existing receipt, but unsafe values never enter persistent metadata.
                cp.Receipt = null;
                cp.Meshy = null;
                cp.FalOutputField = null;
                cp.Phase = "nonresumable";
                r.Job.RecoveryCode = "receipt_not_resumable";
            }
            UpdateProviderEvidence(r);
            PersistRequired(r.Job, cp);
            return receipt;
        }

        private static void AttachMeshyCheckpoint(Runner r)
        {
            if (r.ModelAdapter is not MeshyAdapter adapter)
                return;
            adapter.RecoveryCheckpoint = (phase, state) =>
            {
                Admit(r);
                if (phase == "refine_admit")
                    return;
                // Validate a detached candidate before any provider-controlled value can enter the stored checkpoint.
                var candidate = JObject.FromObject(r.Checkpoint).ToObject<AssetGenJobCheckpoint>();
                candidate.Meshy = state;
                candidate.Phase = phase;
                if (!candidate.Validate(r.Job.JobId, Now()))
                    throw new RecoveryAbort("checkpoint_invalid");
                r.Checkpoint = candidate;
                Checkpoints[r.Job.JobId] = candidate;
                UpdateProviderEvidence(r);
                PersistRequired(r.Job, r.Checkpoint);
            };
        }

        private static void UpdateProviderEvidence(Runner r) => UpdateProviderEvidence(r.Job, r.Checkpoint);

        private static void UpdateProviderEvidence(AssetGenJob job, AssetGenJobCheckpoint cp)
        {
            job.RecoveryState =
                cp.Phase == "nonresumable" ? "unavailable"
                : job.RecoveryState == "resuming" ? "resuming"
                : "available";
            string id = cp.Provider == "sketchfab" ? cp.MarketplaceUid : cp.Meshy?.RefineId ?? cp.Receipt;
            if (cp.Provider == "fal" && AssetGenJobCheckpoint.FalReceipt(id))
                id = id.Substring(id.LastIndexOf('/') + 1);
            job.ProviderTaskId = AssetGenJobCheckpoint.SafeId(id) ? id : null;
            job.ProviderPhase =
                cp.Phase == "saved" ? "saved"
                : cp.Phase == "importing" ? "import"
                : cp.Phase == "downloading" ? "download"
                : cp.Provider == "meshy" ? (cp.Meshy?.RefineId != null || cp.Phase == "refine_submitting" ? "refine" : "preview")
                : "poll";
        }

        private static void Reconnect(Runner r)
        {
            Admit(r);
            AssetGenJobCheckpoint cp = r.Checkpoint;
            r.ImportFn = ImportOverrideForTests;
            if (r.ImportFn == null)
            {
                if (r.Job.Kind == "image")
                    r.ImportFn = CreateImageImporter(cp.AsSprite, cp.Transparent);
                else if (r.Job.Kind == "audio")
                    r.ImportFn = AudioImportPipeline.ImportInto;
                else
                    r.ImportFn = ModelImportPipeline.ImportInto;
            }
            if (cp.Phase == "saved")
            {
                ValidateSaved(r);
                Admit(r);
                r.Phase = RunnerPhase.Import;
                return;
            }
            // Local saved recovery must never touch credentials, catalog or adapter construction.
            if (r.Job.Kind == "marketplace")
            {
                if (!SketchfabAdapter.TryNormalizeModelUid(cp.MarketplaceUid, out string canonicalUid))
                    throw new RecoveryAbort("checkpoint_invalid");
                cp.MarketplaceUid = canonicalUid;
            }
            if (!SecureKeyStore.Current.TryGet(r.Job.Provider, out string key) || string.IsNullOrEmpty(key))
                throw new RecoveryAbort("reconnect_key_unavailable");
            Admit(r);
            r.Transport = CreateJobTransport(r.Job.JobId);
            if (r.Job.Kind == "model")
            {
                r.ModelAdapter = AssetGenProviders.Model(r.Job.Provider);
                if (r.ModelAdapter is MeshyAdapter meshy)
                    meshy.RestoreRecovery(cp.Meshy);
                if (r.ModelAdapter is FalModelAdapter fal)
                    fal.RestoreOutputField(cp.FalOutputField);
                AttachMeshyCheckpoint(r);
                r.PollFn = (id, ct) => r.ModelAdapter.PollAsync(id, key, r.Transport, ct);
            }
            else if (r.Job.Kind == "image")
            {
                var adapter = AssetGenProviders.Image(r.Job.Provider);
                r.PollFn = (id, ct) => adapter.PollAsync(id, key, r.Transport, ct);
            }
            else if (r.Job.Kind == "audio")
            {
                var adapter = AssetGenProviders.Audio(r.Job.Provider);
                r.PollFn = (id, ct) => adapter.PollAsync(id, key, r.Transport, ct);
            }
            else
            {
                var adapter = AssetGenProviders.Marketplace("sketchfab");
                r.PollFn = async (_, ct) =>
                    new ProviderPollResult
                    {
                        State = ProviderPollState.Succeeded,
                        Progress = 1f,
                        ResultExt = "zip",
                        DownloadUrl = await adapter.ResolveDownloadUrlAsync(cp.MarketplaceUid, key, r.Transport, ct),
                    };
            }
            r.NextPollAt = Now();
            r.Phase = RunnerPhase.Poll;
            r.Job.State = AssetGenJobState.Running;
        }

        private static void Advance(Runner r)
        {
            if (!Current(r))
                return;
            if (IsTerminal(r.Job.State))
            {
                Finalize(r);
                return;
            }
            if (r.Canceled)
            {
                r.Job.State = AssetGenJobState.Canceled;
                Persist(r.Job);
                Finalize(r);
                return;
            }
            if (Now() >= r.Checkpoint.Deadline)
            {
                Fail(r, AssetGenJobCheckpoint.ErrorSummary("job_timeout"), "job_timeout");
                return;
            }

            try
            {
                Admit(r);
                switch (r.Phase)
                {
                    case RunnerPhase.Reconnect:
                        Reconnect(r);
                        break;
                    case RunnerPhase.Submit:
                        r.Job.ProgressEvidence?.Begin(AssetOperationPhase.Preparing);
                        r.Job.State = AssetGenJobState.Running;
                        Persist(r.Job);
                        if (r.Job.Provider == "sketchfab")
                        {
                            r.Checkpoint.Phase = AssetGenJobCheckpoint.SafeId(r.MarketplaceUid, 128) ? "polling" : "nonresumable";
                            PersistRequired(r.Job, r.Checkpoint);
                            Admit(r);
                        }
                        r.SubmitTask = SubmitAndCheckpoint(r);
                        r.Phase = RunnerPhase.AwaitSubmit;
                        break;

                    case RunnerPhase.AwaitSubmit:
                        if (!r.SubmitTask.IsCompleted)
                            break;
                        if (Faulted(r.SubmitTask, out string subErr))
                        {
                            Fail(r, subErr, RecoveryCode(r.SubmitTask));
                            break;
                        }
                        r.ProviderJobId = r.SubmitTask.Result;
                        r.SubmitTask = null;
                        r.SubmitFn = null;
                        if (string.IsNullOrEmpty(r.ProviderJobId))
                        {
                            Fail(r, "Provider returned no job id.");
                            break;
                        }
                        r.NextPollAt = Now();
                        r.Phase = RunnerPhase.Poll;
                        break;

                    case RunnerPhase.Poll:
                        r.Job.ProgressEvidence?.Begin(AssetOperationPhase.WaitingProvider);
                        if (Now() < r.NextPollAt)
                            break;
                        r.PollTask = r.PollFn(r.ProviderJobId, r.Cts.Token);
                        r.Phase = RunnerPhase.AwaitPoll;
                        break;

                    case RunnerPhase.AwaitPoll:
                        if (!r.PollTask.IsCompleted)
                            break;
                        if (Faulted(r.PollTask, out string pollErr))
                        {
                            Fail(r, pollErr, RecoveryCode(r.PollTask));
                            break;
                        }
                        ProviderPollResult pr = r.PollTask.Result;
                        r.PollTask = null;
                        r.Job.Progress = Mathf.Clamp01(pr.Progress);
                        Persist(r.Job);
                        if (pr.State == ProviderPollState.Succeeded)
                        {
                            r.PollFn = null;
                            r.OverrideExt = pr.ResultExt;
                            if (pr.InlineData != null && pr.InlineData.Length > 0)
                            {
                                if (pr.InlineData.LongLength > PreparedArtifact.BackgroundThreshold)
                                {
                                    StartPreparation(r, new HttpResult { Body = pr.InlineData });
                                    break;
                                }
                                r.LocalPath = WriteFile(r, pr.InlineData);
                                r.Job.State = AssetGenJobState.Importing;
                                Persist(r.Job);
                                r.Phase = RunnerPhase.Import;
                                // Admit this freshly owned file in the same update, before independent auto-import can create metadata.
                                Advance(r);
                            }
                            else if (!string.IsNullOrEmpty(pr.DownloadUrl))
                            {
                                r.DownloadUrl = pr.DownloadUrl;
                                if (r.Checkpoint.Phase != "nonresumable")
                                    r.Checkpoint.Phase = "downloading";
                                UpdateProviderEvidence(r);
                                PersistRequired(r.Job, r.Checkpoint);
                                r.Phase = RunnerPhase.Download;
                            }
                            else
                            {
                                Fail(r, "Provider succeeded but returned no result data.");
                            }
                        }
                        else if (pr.State == ProviderPollState.Failed)
                        {
                            Fail(r, string.IsNullOrEmpty(pr.Error) ? "Provider reported failure." : pr.Error);
                        }
                        else
                        {
                            r.NextPollAt = Now() + PollIntervalSeconds;
                            r.Phase = RunnerPhase.Poll;
                        }
                        break;

                    case RunnerPhase.Download:
                        r.Job.ProgressEvidence?.Begin(AssetOperationPhase.Downloading);
                        AssetDownloadPolicy.RequireAllowedUrl(r.Job.Provider, r.DownloadUrl);
                        r.DownloadTask = r.Transport.SendAsync(
                            new HttpRequestSpec
                            {
                                Method = "GET",
                                Url = r.DownloadUrl,
                                DownloadProvider = r.Job.Provider,
                                DownloadProgress = r.Job.ProgressEvidence,
                            },
                            r.Cts.Token
                        );
                        r.Phase = RunnerPhase.AwaitDownload;
                        break;

                    case RunnerPhase.AwaitDownload:
                        if (!r.DownloadTask.IsCompleted)
                            break;
                        if (Faulted(r.DownloadTask, out string dlErr))
                        {
                            Fail(r, dlErr, RecoveryCode(r.DownloadTask));
                            break;
                        }
                        HttpResult res = r.DownloadTask.Result;
                        r.DownloadTask = null;
                        if (res?.IsSuccess == true && (res.DownloadedFile?.Length ?? res.Body?.LongLength ?? 0) > PreparedArtifact.BackgroundThreshold)
                        {
                            StartPreparation(r, res);
                            break;
                        }
                        using (res)
                        {
                            if (res == null || !res.IsSuccess || (res.DownloadedFile?.Length ?? res.Body?.LongLength ?? 0) == 0)
                            {
                                Fail(r, $"Download failed (HTTP {res?.Status}).");
                                break;
                            }
                            r.LocalPath =
                                res.DownloadedFile != null
                                    ? WriteFile(r, res.DownloadedFile.Rewind(), res.DownloadedFile.Length, res.DownloadedFile.Sha256)
                                    : WriteFile(r, res.Body);
                        }
                        r.Job.State = AssetGenJobState.Importing;
                        Persist(r.Job);
                        r.Phase = RunnerPhase.Import;
                        // Saved and importing checkpoints still surround the pipeline; no Unity update is inserted between them.
                        Advance(r);
                        break;

                    case RunnerPhase.AwaitPreparation:
                        if (!r.PreparationTask.IsCompleted)
                            break;
                        if (Faulted(r.PreparationTask, out string preparationError))
                        {
                            Fail(r, preparationError);
                            break;
                        }
                        using (PreparedArtifact prepared = r.PreparationTask.Result)
                        {
                            r.PreparationTask = null;
                            r.Job.Format = prepared.Extension;
                            r.LocalPath = WriteValidatedFile(r, null, prepared.Length, prepared.Hash, prepared.Extension, prepared);
                        }
                        r.Job.State = AssetGenJobState.Importing;
                        Persist(r.Job);
                        r.Phase = RunnerPhase.Import;
                        Advance(r);
                        break;

                    case RunnerPhase.Import:
                        r.Job.ProgressEvidence?.Begin(AssetOperationPhase.Importing);
                        ValidateSaved(r);
                        Admit(r);
                        r.Checkpoint.Phase = "importing";
                        UpdateProviderEvidence(r);
                        PersistRequired(r.Job, r.Checkpoint);
                        Admit(r);
                        // Once admitted, this synchronous pipeline may finish. Never replay it after reload.
                        r.ImportInProgress = true;
                        AssetGenJob imported;
                        try
                        {
                            imported = r.ImportFn(r.Job, r.LocalPath);
                        }
                        finally
                        {
                            r.ImportInProgress = false;
                        }
                        if (!Current(r))
                            return;
                        if (imported != null && !ReferenceEquals(imported, r.Job))
                        {
                            imported.ProgressEvidence = r.Job.ProgressEvidence;
                            r.Job = imported;
                            Jobs[r.Job.JobId] = imported;
                        }
                        if (!Current(r))
                            return;
                        if (r.Canceled || r.Job.State == AssetGenJobState.Canceled)
                        {
                            r.Job.State = AssetGenJobState.Canceled;
                            Persist(r.Job);
                            Finalize(r);
                            return;
                        }
                        if (r.Job.State != AssetGenJobState.Failed)
                        {
                            r.Job.State = AssetGenJobState.Done;
                            r.Job.Progress = 1f;
                            r.Job.RecoveryCode = "completed";
                        }
                        r.Job.RecoveryState = "terminal";
                        Persist(r.Job);
                        Finalize(r);
                        break;
                }
            }
            catch (Exception e)
            {
                if (!Current(r))
                    return;
                if (r.Canceled)
                {
                    r.Job.State = AssetGenJobState.Canceled;
                    Persist(r.Job);
                    Finalize(r);
                    return;
                }
                Fail(r, SecretRedactor.Scrub(e.Message), e is RecoveryAbort abort ? abort.Code : "generation_failed");
            }
        }

        // Per-kind allowlist of result file extensions. The write extension can come from a
        // provider-controlled result URL (OverrideExt), so a rogue provider could otherwise land a
        // .cs/.asmdef/.meta/.asset under Assets/ and get it compiled/imported on Refresh — Editor
        // RCE. Anything outside these sets is rejected. Mirrors ModelImportPipeline's allowlist style.
        private static readonly HashSet<string> AudioAllowedExtensions = new(StringComparer.OrdinalIgnoreCase) { "wav", "mp3", "ogg", "aiff", "aif", "flac" };
        private static readonly HashSet<string> ImageAllowedExtensions = new(StringComparer.OrdinalIgnoreCase)
        {
            "png",
            "jpg",
            "jpeg",
            "exr",
            "tga",
            "psd",
            "tiff",
            "webp",
            "gif",
            "bmp",
        };
        private static readonly HashSet<string> ModelAllowedExtensions = new(StringComparer.OrdinalIgnoreCase)
        {
            "glb",
            "gltf",
            "fbx",
            "obj",
            "usd",
            "usdz",
            "dae",
            "ply",
            "stl",
            "zip",
        };

        // Fail closed: an unexpected kind allows nothing, so the RCE boundary never opens by default.
        private static readonly HashSet<string> NoAllowedExtensions = new(StringComparer.OrdinalIgnoreCase);

        /// <summary>
        /// Whether <paramref name="ext"/> (no leading dot) is an allowed result extension for the
        /// job <paramref name="kind"/> (audio | image | model | marketplace). Internal so the
        /// allowlist can be unit-tested directly.
        /// </summary>
        internal static bool IsAllowedResultExtension(string kind, string ext) => AllowedExtensionsFor(kind).Contains((ext ?? string.Empty).TrimStart('.'));

        private static HashSet<string> AllowedExtensionsFor(string kind)
        {
            switch ((kind ?? string.Empty).ToLowerInvariant())
            {
                case "audio":
                    return AudioAllowedExtensions;
                case "image":
                    return ImageAllowedExtensions;
                case "model":
                case "marketplace":
                    return ModelAllowedExtensions;
                default:
                    return NoAllowedExtensions; // fail closed for unexpected kinds
            }
        }

        private static void StartPreparation(Runner r, HttpResult input)
        {
            r.Job.ProgressEvidence?.Begin(AssetOperationPhase.Preparing);
            try
            {
                Admit(r);
                // Snapshot primitives on the main thread. Workers own input and never capture the runner.
                string root = Path.GetDirectoryName(Application.dataPath);
                r.PreparationTask = PreparedArtifact.CreateAsync(input, root, r.Job.Kind, r.Job.Provider, r.Ext, r.OverrideExt, r.Cts.Token);
                r.Phase = RunnerPhase.AwaitPreparation;
            }
            catch
            {
                input.Dispose();
                throw;
            }
        }

        private static string WriteFile(Runner r, byte[] bytes)
        {
            if (bytes == null)
                throw new IOException("Provider artifact is missing.");
            using var input = new MemoryStream(bytes, false);
            return WriteFile(r, input, bytes.LongLength, HashBytes(bytes));
        }

        private static string WriteFile(Runner r, Stream input, long length, string hash)
        {
            if (!input.CanSeek || length <= 0 || length > AssetDownloadTransport.MaxDownloadBytes || input.Length != length)
                throw new IOException("Provider artifact exceeds the 8 GiB limit or has changed length.");
            string ext = PreparedArtifact.DetectExtension(input, r.Job.Kind, r.Job.Provider, r.Ext, r.OverrideExt, r.Cts.Token);
            if (r.Job.Kind == "image" || r.Job.Provider == "tripo")
                r.Job.Format = ext;
            return WriteValidatedFile(r, input, length, hash, ext);
        }

        private static string WriteValidatedFile(Runner r, Stream input, long length, string hash, string ext, PreparedArtifact prepared = null)
        {
            Admit(r);
            string root = AssetGenPaths.ResolveOutputFolder(r.OutputFolder, r.Subfolder);
            string baseName = SanitizeName(r.Name);
            for (int n = 0; n < 64; n++)
            {
                string path = root.TrimEnd('/') + "/" + baseName + (n == 0 ? "" : "_" + n) + "." + ext;
                if (Occupied(path))
                    continue;
                AssetGenJobCheckpoint cp = r.Checkpoint;
                cp.Phase = "writing";
                cp.SavedPath = path;
                cp.SavedLength = length;
                cp.SavedHash = hash;
                cp.Extension = ext;
                cp.MetadataPresent = false;
                cp.MetadataLength = 0;
                cp.MetadataHash = null;
                cp.RegisteredGuid = null;
                PersistRequired(r.Job, cp);
                Admit(r);
                if (Occupied(path))
                    continue;
                using var folders = new AssetFolderScope();
                folders.EnsureFolder(root);
                Admit(r);
                string abs = AssetGenPaths.ToAbsolute(path);
                FileStream stream = null;
                try
                {
                    if (prepared != null)
                    {
                        if (MetadataOccupied(path))
                            throw new RecoveryAbort("saved_asset_identity_changed");
                        prepared.Publish(abs);
                    }
                    else
                        stream = new FileStream(abs, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                }
                catch (IOException) when (File.Exists(abs) || Directory.Exists(abs))
                {
                    continue;
                }
                // From this point a partial file belongs to this job. Preserve it even if writing/checkpointing fails.
                folders.Complete();
                r.Job.AssetPath = path;
                r.LocalPath = path;
                if (stream != null)
                    using (stream)
                    {
                        Admit(r);
                        if (MetadataOccupied(path))
                            throw new RecoveryAbort("saved_asset_identity_changed");
                        var buffer = new byte[64 * 1024];
                        long copied = 0;
                        int read;
                        while ((read = input.Read(buffer, 0, buffer.Length)) != 0)
                        {
                            Admit(r);
                            if (read > length - copied)
                                throw new IOException("Provider artifact changed length during publication.");
                            stream.Write(buffer, 0, read);
                            copied += read;
                        }
                        if (copied != length)
                            throw new IOException("Provider artifact ended during publication.");
                        stream.Flush();
                    }
                Admit(r);
                CaptureMetadata(cp);
                cp.Phase = "saved";
                r.Job.AssetGuid = cp.RegisteredGuid;
                if (r.Job.Kind == "model" || r.Job.Kind == "marketplace")
                    r.Job.ModelImportStage = "saved";
                UpdateProviderEvidence(r);
                PersistRequired(r.Job, cp);
                return path;
            }
            throw new IOException("No unoccupied generated output slot was available within 64 attempts.");
        }

        private static bool MetadataOccupied(string path)
        {
            string meta = AssetGenPaths.ToAbsolute(path + ".meta");
            return File.Exists(meta) || Directory.Exists(meta) || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path));
        }

        private static bool Occupied(string path)
        {
            // Resolve both paths: Exists alone does not reliably detect a dangling metadata link on Mono.
            string abs = AssetGenPaths.ToAbsolute(path);
            return MetadataOccupied(path) || File.Exists(abs) || Directory.Exists(abs);
        }

        private static string HashBytes(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static string HashFile(string abs, long expected)
        {
            if (expected <= 0 || expected > AssetGenJobCheckpoint.MaxFileBytes)
                throw new RecoveryAbort("saved_asset_identity_changed");
            using var stream = new FileStream(abs, FileMode.Open, FileAccess.Read, FileShare.Read, 64 * 1024);
            if (stream.Length != expected)
                throw new RecoveryAbort("saved_asset_identity_changed");
            using var sha = SHA256.Create();
            byte[] buffer = new byte[64 * 1024];
            long length = 0;
            int read;
            while ((read = stream.Read(buffer, 0, buffer.Length)) != 0)
            {
                length += read;
                if (length > expected)
                    throw new RecoveryAbort("saved_asset_identity_changed");
                sha.TransformBlock(buffer, 0, read, buffer, 0);
            }
            if (length != expected)
                throw new RecoveryAbort("saved_asset_identity_changed");
            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
        }

        private static void CaptureMetadata(AssetGenJobCheckpoint cp)
        {
            string meta = AssetGenPaths.ToAbsolute(cp.SavedPath + ".meta");
            if (Directory.Exists(meta))
                throw new RecoveryAbort("saved_asset_identity_changed");
            cp.MetadataPresent = File.Exists(meta);
            cp.MetadataLength = cp.MetadataPresent ? new FileInfo(meta).Length : 0;
            cp.MetadataHash = cp.MetadataPresent ? HashFile(meta, cp.MetadataLength) : null;
            cp.RegisteredGuid = AssetDatabase.AssetPathToGUID(cp.SavedPath);
            if (string.IsNullOrEmpty(cp.RegisteredGuid))
                cp.RegisteredGuid = null;
            if (!cp.MetadataPresent && cp.RegisteredGuid != null)
                throw new RecoveryAbort("saved_asset_identity_changed");
        }

        private static void ValidateSaved(Runner r)
        {
            Admit(r);
            AssetGenJobCheckpoint cp = r.Checkpoint;
            string abs = AssetGenPaths.ToAbsolute(cp.SavedPath);
            string meta = AssetGenPaths.ToAbsolute(cp.SavedPath + ".meta");
            if (!File.Exists(abs))
                throw new RecoveryAbort("saved_asset_missing");
            if (
                !cp.Validate(r.Job.JobId, Now())
                || !IsAllowedResultExtension(r.Job.Kind, Path.GetExtension(cp.SavedPath))
                || HashFile(abs, cp.SavedLength) != cp.SavedHash
                || Directory.Exists(meta)
                || File.Exists(meta) != cp.MetadataPresent
            )
                throw new RecoveryAbort("saved_asset_identity_changed");
            if (cp.MetadataPresent && HashFile(meta, cp.MetadataLength) != cp.MetadataHash)
                throw new RecoveryAbort("saved_asset_identity_changed");
            string guid = AssetDatabase.AssetPathToGUID(cp.SavedPath);
            if (!string.Equals(guid ?? "", cp.RegisteredGuid ?? "", StringComparison.Ordinal))
                throw new RecoveryAbort("saved_asset_identity_changed");
            Admit(r);
        }

        private static string NameFrom(string explicitName, string prompt, string jobId)
        {
            if (!string.IsNullOrWhiteSpace(explicitName))
                return explicitName;
            if (!string.IsNullOrWhiteSpace(prompt))
                return prompt;
            return "asset_" + jobId.Substring(0, 8);
        }

        private static string SanitizeName(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw))
                return "asset";
            var sb = new System.Text.StringBuilder();
            foreach (char c in raw.Trim())
            {
                if (char.IsLetterOrDigit(c) || c == '_' || c == '-')
                    sb.Append(c);
                else if (c == ' ')
                    sb.Append('_');
                if (sb.Length >= 48)
                    break;
            }
            string s = sb.ToString().Trim('_', '-');
            return string.IsNullOrEmpty(s) ? "asset" : s;
        }

        private static string RecoveryCode(Task task) => task?.Exception?.GetBaseException() is RecoveryAbort abort ? abort.Code : "generation_failed";

        private static void Fail(Runner r, string message, string code = "generation_failed")
        {
            if (!Current(r))
                return;
            r.Job.State = AssetGenJobState.Failed;
            r.Job.RecoveryCode = code;
            r.Job.RecoveryState =
                r.Checkpoint.Phase == "submitting"
                || r.Checkpoint.Phase == "refine_submitting"
                || r.Checkpoint.Phase == "writing"
                || r.Checkpoint.Phase == "importing"
                    ? "outcome_unknown"
                    : "unavailable";
            r.Job.Error = SecretRedactor.Scrub(string.IsNullOrEmpty(message) ? "Generation failed." : message);
            Persist(r.Job);
            Finalize(r);
        }

        private static void Finalize(Runner r)
        {
            if (!Runners.TryGetValue(r.Job.JobId, out var current) || !ReferenceEquals(current, r))
                return;
            r.Job.ProgressEvidence?.Stop();
            Runners.Remove(r.Job.JobId);
            OperationActivity.Invalidate();
            try
            {
                if (r.Job.State == AssetGenJobState.Failed || r.Canceled)
                    r.Cts?.Cancel();
            }
            catch { }
            Observe(r.SubmitTask);
            Observe(r.PollTask);
            ObserveDownload(r.DownloadTask);
            ObservePreparation(r.PreparationTask);
            try
            {
                r.Cts?.Dispose();
            }
            catch { }
        }

        private static AssetGenJob NewJob(string kind, string provider, string action)
        {
            return new AssetGenJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = kind,
                Provider = provider,
                Action = action,
                State = AssetGenJobState.Queued,
                Progress = 0f,
                ProgressEvidence = new AssetOperationProgress(),
            };
        }

        private static void PersistRequired(AssetGenJob job, AssetGenJobCheckpoint checkpoint)
        {
            if (IsTerminal(job.State) && !Runners.ContainsKey(job.JobId))
                job.ProgressEvidence?.Stop();
            try
            {
                if (checkpoint != null)
                {
                    checked
                    {
                        checkpoint.Revision++;
                    }
                    if (!checkpoint.Validate(job.JobId, Now()))
                        throw new InvalidDataException("checkpoint_invalid");
                }
                JObject record = AssetGenJobCheckpoint.Project(job, checkpoint);
                TrackJob(job);
                string index = string.Join(",", JobOrder);
                if (Encoding.UTF8.GetByteCount(index) > AssetGenJobCheckpoint.MaxIndexBytes)
                    throw new InvalidDataException("index_size_invalid");
                BeforeCheckpointWriteForTests?.Invoke(checkpoint?.Phase ?? "terminal");
                if (JournalEnabled)
                    Journal.Save(record, JobOrder, Now(), true);
                SessionState.SetString(JobKeyPrefix + job.JobId, record.ToString(Formatting.None));
                SessionState.SetString(JobIndexKey, index);
                _jobIndexDirty = false;
            }
            catch
            {
                throw new RecoveryAbort("checkpoint_write_failed");
            }
            finally
            {
                OperationActivity.Invalidate();
            }
        }

        private static void Persist(AssetGenJob job)
        {
            if (IsTerminal(job.State) && !Runners.ContainsKey(job.JobId))
                job.ProgressEvidence?.Stop();
            TrackJob(job);
            try
            {
                Checkpoints.TryGetValue(job.JobId, out var checkpoint);
                JObject record = AssetGenJobCheckpoint.Project(job, checkpoint);
                SessionState.SetString(JobKeyPrefix + job.JobId, record.ToString(Formatting.None));
                WriteJobIndex();
                if (JournalEnabled)
                    Journal.Save(record, JobOrder, Now(), false);
            }
            catch { /* progress/terminal metadata is best-effort; side-effect admission always uses required writes */ }
            finally
            {
                OperationActivity.Invalidate();
            }
        }

        private static void TrackJob(AssetGenJob job)
        {
            if (!JobOrderNodes.ContainsKey(job.JobId))
            {
                JobOrderNodes[job.JobId] = JobOrder.AddLast(job.JobId);
                _jobIndexDirty = true;
            }
            if (!IsTerminal(job.State) || !TerminalJobIds.Add(job.JobId))
                return;
            // A long-running older job must remain queryable when it finally completes.
            var completedNode = JobOrderNodes[job.JobId];
            JobOrder.Remove(completedNode);
            JobOrder.AddLast(completedNode);
            _jobIndexDirty = true;
            if (TerminalJobIds.Count <= MaxTerminalJobs)
                return;

            for (var node = JobOrder.First; node != null && TerminalJobIds.Count > MaxTerminalJobs; )
            {
                var next = node.Next;
                string id = node.Value;
                if (TerminalJobIds.Remove(id))
                {
                    Jobs.Remove(id);
                    Checkpoints.Remove(id);
                    JobOrderNodes.Remove(id);
                    JobOrder.Remove(node);
                    _jobIndexDirty = true;
                    try
                    {
                        SessionState.EraseString(JobKeyPrefix + id);
                    }
                    catch { /* persistence is best-effort */ }
                }
                node = next;
            }
        }

        private static void WriteJobIndex()
        {
            if (!_jobIndexDirty)
                return;
            string index = string.Join(",", JobOrder);
            if (Encoding.UTF8.GetByteCount(index) > AssetGenJobCheckpoint.MaxIndexBytes)
                throw new InvalidDataException("index_size_invalid");
            SessionState.SetString(JobIndexKey, index);
            _jobIndexDirty = false;
        }

        private static bool Faulted(Task t, out string error)
        {
            if (t.IsFaulted)
            {
                Exception ex = t.Exception?.GetBaseException();
                error = SecretRedactor.Scrub(ex?.Message ?? "request failed");
                return true;
            }
            if (t.IsCanceled)
            {
                error = "Canceled.";
                return true;
            }
            error = null;
            return false;
        }

        private static bool IsTerminal(AssetGenJobState s) => s == AssetGenJobState.Done || s == AssetGenJobState.Failed || s == AssetGenJobState.Canceled;

        private static double Now() => ClockForTests?.Invoke() ?? EditorApplication.timeSinceStartup;

        internal static void ResetTransientForTests()
        {
            PrepareForAssemblyReload();
            Jobs.Clear();
            Checkpoints.Clear();
            JobOrder.Clear();
            JobOrderNodes.Clear();
            TerminalJobIds.Clear();
            _jobIndexDirty = false;
        }

        internal static void ResetForTests()
        {
            PrepareForAssemblyReload();
            string index = SessionState.GetString(JobIndexKey, string.Empty);
            foreach (string id in index.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
                SessionState.EraseString(JobKeyPrefix + id);
            SessionState.EraseString(JobIndexKey);
            Jobs.Clear();
            Checkpoints.Clear();
            JobOrder.Clear();
            JobOrderNodes.Clear();
            TerminalJobIds.Clear();
            _jobIndexDirty = false;
            TransportOverrideForTests = null;
            ImportOverrideForTests = null;
            PollIntervalSeconds = 3.0;
            TimeoutSeconds = AssetGenJobCheckpoint.MaxDurationSeconds;
            SkipModelVerificationForTests = false;
            ClockForTests = null;
            BeforeCheckpointWriteForTests = null;
            _journalRootForTests = null;
            _journalOwnerForTests = null;
            _journalInstanceForTests = null;
            _journalUtcForTests = null;
            _journalError = null;
            AssetGenModelCatalog.ResetForTests();
        }
    }
}
