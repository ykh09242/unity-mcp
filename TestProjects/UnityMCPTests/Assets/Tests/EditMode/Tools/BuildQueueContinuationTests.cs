using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class BuildQueueContinuationTests
    {
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;
        private const string NativeKey = "MCPForUnity.Build.NativeOwnerV1";
        private string previousQueue;
        private bool previousWriting;
        private Func<BuildPlayerOptions, BuildReport> previousBuild;
        private EditorApplication.CallbackFunction[] previousCallbacks;
        private Dictionary<string, BuildJob> previousJobs;
        private Dictionary<string, BatchJob> previousBatches;
        private BuildJob previousLast;
        private int invocations;
        private bool captured;

        private static Dictionary<string, BuildJob> Jobs =>
            (Dictionary<string, BuildJob>)typeof(BuildJobStore).GetField("_buildJobs", PrivateStatic).GetValue(null);
        private static Dictionary<string, BatchJob> Batches =>
            (Dictionary<string, BatchJob>)typeof(BuildJobStore).GetField("_batchJobs", PrivateStatic).GetValue(null);

        private static HashSet<string> Owners(string name) => (HashSet<string>)typeof(BuildRunner).GetField(name, PrivateStatic).GetValue(null);

        private static List<BuildQueueEntry> Entries => (List<BuildQueueEntry>)typeof(BuildQueueCheckpoint).GetField("entries", PrivateStatic).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            captured = false;
            if (
                !Application.isBatchMode
                || Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)) != Environment.GetEnvironmentVariable("UNITY_MCP_BUILD_RELOAD_PROJECT")
            )
                Assert.Ignore("Build queue execution fixtures require an explicitly identified disposable batch Editor.");
            Assert.IsFalse(BuildPipeline.isBuildingPlayer || BuildRunner.HasNativeOwner || BuildRunner.NativeRecoveryRequired);
            Assert.IsNull(BuildJobStore.ActiveJobId, "Preserve existing live build jobs.");
            previousCallbacks = Callbacks();
            previousQueue = SessionState.GetString(BuildQueueCheckpoint.StorageKey, "");
            previousWriting = SessionState.GetBool(BuildQueueCheckpoint.WritingKey, false);
            previousBuild = BuildRunner.BuildPlayerForQueuedBuild;
            previousJobs = new Dictionary<string, BuildJob>(Jobs);
            previousBatches = new Dictionary<string, BatchJob>(Batches);
            previousLast = BuildJobStore.LastCompletedJob;
            captured = true;
            SessionState.EraseString(BuildQueueCheckpoint.StorageKey);
            SessionState.EraseBool(BuildQueueCheckpoint.WritingKey);
            ForgetDomain();
            invocations = 0;
            BuildRunner.BuildPlayerForQueuedBuild = options =>
            {
                invocations++;
                throw new InvalidOperationException("controlled queued build invocation");
            };
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured)
                return;
            DropCallbacks();
            ForgetDomain();
            foreach (var pair in previousJobs)
                Jobs.Add(pair.Key, pair.Value);
            foreach (var pair in previousBatches)
                Batches.Add(pair.Key, pair.Value);
            typeof(BuildJobStore).GetField("_lastCompletedJob", PrivateStatic).SetValue(null, previousLast);
            SessionState.SetString(BuildQueueCheckpoint.StorageKey, previousQueue);
            SessionState.SetBool(BuildQueueCheckpoint.WritingKey, previousWriting);
            SessionState.EraseBool(NativeKey);
            BuildRunner.BuildPlayerForQueuedBuild = previousBuild;
            captured = false;
        }

        [Test]
        public void PendingBuildRebindsOriginalIdAndFrozenOptionsAndInvokesOnce()
        {
            BuildJob original = Job();
            BuildPlayerOptions options = Options(original);
            options.extraScriptingDefines = new[] { "FROZEN" };
            BuildRunner.ScheduleBuild(original, options);
            options.extraScriptingDefines[0] = "CHANGED";
            SimulateDomainLoss();
            Restore();
            Restore(); // Duplicate recovery must not install another native callback.
            BuildJob restored = BuildJobStore.GetBuildJob(original.JobId);
            Assert.IsNotNull(restored);
            Assert.AreEqual(BuildJobState.Pending, restored.State);
            Assert.AreEqual("FROZEN", Entries[0].Children[0].Options.extraScriptingDefines[0]);
            ExpectInvocationFailure();
            Advance();
            Advance();
            Restore();
            Advance();
            Assert.AreEqual(1, invocations);
            Assert.AreEqual(BuildJobState.Failed, restored.State);
            Assert.IsFalse(BuildRunner.HasNativeOwner || BuildRunner.NativeRecoveryRequired);
        }

        [Test]
        public void NativeStartedCheckpointRetainsFenceAndNeverInvokesAgain()
        {
            BuildJob job = Job();
            BuildRunner.ScheduleBuild(job, Options(job));
            BuildQueueChild child = Entries[0].Children[0];
            BuildQueueCheckpoint.Prepare(child);
            BuildQueueCheckpoint.MarkNativeStarted(child);
            SessionState.SetBool(NativeKey, true);
            SimulateDomainLoss();
            Restore();
            Advance();
            Assert.AreEqual(0, invocations);
            Assert.IsTrue(BuildRunner.NativeRecoveryRequired);
            StringAssert.Contains("will not be executed again", BuildJobStore.GetBuildJob(job.JobId).ErrorMessage);
        }

        [Test]
        public void ExplicitPendingCancellationSurvivesDomainLoss()
        {
            BuildJob job = Job();
            BuildRunner.ScheduleBuild(job, Options(job));
            JObject result = JObject.FromObject(ManageBuild.HandleCommand(new JObject { ["action"] = "cancel", ["job_id"] = job.JobId }));
            Assert.IsTrue((bool)result["success"], result.ToString());
            SimulateDomainLoss();
            Restore();
            Advance();
            Assert.AreEqual(0, invocations);
            Assert.AreEqual(BuildJobState.Cancelled, BuildJobStore.GetBuildJob(job.JobId).State);
        }

        [Test]
        public void MoreThanThirtyTwoSequentialCancelledJobsLeaveAdmissionAvailableAndRetainDedupe()
        {
            string firstId = null;
            for (int index = 0; index < 80; index++)
            {
                BuildJob job = Job();
                firstId ??= job.JobId;
                BuildRunner.ScheduleBuild(job, Options(job));
                Assert.IsTrue(BuildQueueCheckpoint.Cancel(job.JobId));
                Advance();
                Advance();
                Assert.IsFalse(BuildRunner.HasNativeOwner || BuildRunner.NativeRecoveryRequired);
            }
            Assert.LessOrEqual(Jobs.Count, BuildQueueJournal.MaxRecentTerminalEntries);
            SimulateDomainLoss();
            Restore();
            BuildJob duplicate = new BuildJob(firstId, EditorUserBuildSettings.activeBuildTarget, "Builds/UnusedQueuedTest.exe");
            Assert.Throws<InvalidOperationException>(() => BuildRunner.ScheduleBuild(duplicate, Options(duplicate)));
            BuildJob next = Job();
            Assert.DoesNotThrow(() => BuildRunner.ScheduleBuild(next, Options(next)));
            Assert.IsTrue(BuildQueueCheckpoint.Cancel(next.JobId));
            Advance();
            Advance();
            Assert.AreEqual(0, invocations);
            Assert.LessOrEqual(Entries.Count, BuildQueueJournal.MaxRecentTerminalEntries);
        }

        [Test]
        public void ExpiredOriginalDeadlineIsTerminalWithoutInvocation()
        {
            BuildJob job = Job();
            BuildQueueEntry entry = BuildQueueEntry.Single(job, Options(job));
            entry.DeadlineUnixMs = DateTimeOffset.UtcNow.AddSeconds(-1).ToUnixTimeMilliseconds();
            BuildQueueCheckpoint.Enqueue(entry, jobs: new[] { job });
            Advance();
            Assert.AreEqual(0, invocations);
            Assert.AreEqual(BuildJobState.Failed, job.State);
            StringAssert.Contains("expired", job.ErrorMessage);
        }

        [Test]
        public void ChangedProfileIdentityIsRefusedBeforeNativeInvocation()
        {
            BuildJob job = Job();
            BuildQueueEntry entry = BuildQueueEntry.Single(job, Options(job));
            entry.Children[0].ProfilePath = "Assets/__MissingQueuedProfile.asset";
            entry.Children[0].ProfileGuid = Guid.NewGuid().ToString("N");
            entry.Children[0].ProfileHash = "original-profile-hash";
            BuildQueueCheckpoint.Enqueue(entry, jobs: new[] { job });
            SimulateDomainLoss();
            Restore();
            LogAssert.Expect(LogType.Error, new Regex("Build profile changed or disappeared"));
            Advance();
            Advance();
            Assert.AreEqual(0, invocations);
            StringAssert.Contains("Build profile changed", BuildJobStore.GetBuildJob(job.JobId).ErrorMessage);
        }

        [Test]
        public void NullScenesFreezeEnabledBuildSettingsAtAdmission()
        {
            BuildJob job = Job();
            var expected = EditorBuildSettings.scenes.Where(scene => scene.enabled).Select(scene => scene.path).ToArray();
            BuildPlayerOptions options = Options(job);
            options.scenes = null;
            BuildQueueChild child = BuildQueueChild.Capture(job, options);
            CollectionAssert.AreEqual(expected, child.Options.scenes);
        }

        [Test]
        public void ChangedProfileSceneSettingsAreRefusedAfterDomainLoss()
        {
            BuildJob job = Job();
            BuildQueueEntry entry = BuildQueueEntry.Single(job, Options(job));
            BuildQueueChild child = entry.Children[0];
            child.ProfilePath = "Assets/__QueuedProfile.asset";
            child.ProfileSceneSettings = BuildQueueCheckpoint.CaptureProfileSceneSettings();
            child.ProfileSceneSettings.Add(
                new JObject
                {
                    ["path"] = "Assets/__FormerBuildScene.unity",
                    ["guid"] = "old",
                    ["enabled"] = true,
                }
            );
            BuildQueueCheckpoint.Enqueue(entry, jobs: new[] { job });
            SimulateDomainLoss();
            Restore();
            LogAssert.Expect(LogType.Error, new Regex("Build profile scene settings changed"));
            Advance();
            Advance();
            Assert.AreEqual(0, invocations);
            StringAssert.Contains("Build profile scene settings changed", BuildJobStore.GetBuildJob(job.JobId).ErrorMessage);
        }

        [Test]
        public void ChangedPlanObservationIsRefusedAfterDomainLoss()
        {
            string scene = AssetDatabase
                .FindAssets("t:Scene")
                .Select(AssetDatabase.GUIDToAssetPath)
                .FirstOrDefault(path => path.StartsWith("Assets/", StringComparison.Ordinal));
            Assert.IsNotNull(scene, "The owned test project must contain a preauthored scene.");
            BuildJob job = Job();
            var request = new JObject
            {
                ["action"] = "build_from_plan",
                ["target"] = job.Target.ToString(),
                ["scenes"] = new JArray(scene),
                ["output_path"] = job.OutputPath,
            };
            BuildPlan plan = BuildPlan.Capture(request);
            BuildRunner.SchedulePreparedBuild(job, plan.CopyOptions(), null, request, plan);
            string originalProduct = PlayerSettings.productName;
            try
            {
                PlayerSettings.productName = originalProduct + "_ChangedWhileQueued";
                SimulateDomainLoss();
                Restore();
                LogAssert.Expect(LogType.Error, new Regex("build_plan_stale"));
                Advance();
                Advance();
                Assert.AreEqual(0, invocations);
                StringAssert.Contains("build_plan_stale", BuildJobStore.GetBuildJob(job.JobId).ErrorMessage);
            }
            finally
            {
                PlayerSettings.productName = originalProduct;
            }
        }

        [Test]
        public void BatchRestorationRetainsCompletedChildAndInvokesOnlyQueuedChild()
        {
            var batch = new BatchJob("queued-batch-" + Guid.NewGuid().ToString("N")) { State = BuildJobState.Building };
            BuildJob first = Job();
            first.State = BuildJobState.Succeeded;
            first.CompletedAt = DateTime.UtcNow;
            BuildJob second = Job();
            batch.Children.AddRange(new[] { first, second });
            BuildQueueEntry entry = BuildQueueEntry.Single(first, Options(first));
            entry.Id = batch.JobId;
            entry.Batch = true;
            entry.CurrentIndex = 1;
            entry.Children[0].Observe(first);
            entry.Children[0].Phase = "terminal";
            entry.Children.Add(BuildQueueChild.Capture(second, Options(second)));
            BuildQueueCheckpoint.Enqueue(entry, jobs: batch.Children, batch: batch);
            SimulateDomainLoss();
            Restore();
            ExpectInvocationFailure();
            Advance();
            Advance();
            Advance();
            Assert.AreEqual(1, invocations);
            BatchJob restored = BuildJobStore.GetBatchJob(batch.JobId);
            Assert.AreEqual(BuildJobState.Succeeded, restored.Children[0].State);
            Assert.AreEqual(second.JobId, restored.Children[1].JobId);
            Assert.AreEqual(BuildJobState.Failed, restored.State);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LateBatchCancellationRemainsCancelledAfterDeferredSettlement(bool reload)
        {
            BatchJob batch = CompleteFinalChildBeforeSettlement();
            JObject result = JObject.FromObject(ManageBuild.HandleCommand(new JObject { ["action"] = "cancel", ["job_id"] = batch.JobId }));
            Assert.IsTrue((bool)result["success"], result.ToString());
            if (reload)
            {
                SimulateDomainLoss();
                Restore();
            }
            Advance();
            Advance();
            Assert.AreEqual(BuildJobState.Cancelled, BuildJobStore.GetBatchJob(batch.JobId).State);
            SimulateDomainLoss();
            Restore();
            Assert.AreEqual(BuildJobState.Cancelled, BuildJobStore.GetBatchJob(batch.JobId).State);
            Assert.AreEqual(0, invocations);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CompletedBatchStatusRetainsReportMetadataByOriginalId(bool persistenceFailure)
        {
            FieldInfo storage = typeof(BuildReportStore).GetField("StorageRootOverride", PrivateStatic);
            object originalStorage = storage.GetValue(null);
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), ".work", "BuildQueueReport_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                string reportRoot = Path.Combine(root, "reports");
                if (persistenceFailure)
                    File.WriteAllText(reportRoot, "owned path obstruction");
                storage.SetValue(null, reportRoot);
                BatchJob batch = CompleteFinalChildBeforeSettlement();
                Advance();
                Advance();
                string reportId = batch.ReportId;
                string persistenceError = batch.ReportPersistenceError;
                if (persistenceFailure)
                    Assert.AreEqual("build_report_persistence_unavailable", persistenceError);
                else
                    Assert.IsNotNull(reportId);
                SimulateDomainLoss();
                Restore();
                JObject response = JObject.FromObject(ManageBuild.HandleCommand(new JObject { ["action"] = "status", ["job_id"] = batch.JobId }));
                Assert.IsTrue((bool)response["success"], response.ToString());
                Assert.AreEqual(batch.JobId, (string)response["data"]["job_id"]);
                Assert.AreEqual(reportId, (string)response["data"]["report_id"]);
                Assert.AreEqual(persistenceError, (string)response["data"]["report_persistence_error"]);
                Assert.AreEqual("succeeded", (string)response["data"]["result"]);
            }
            finally
            {
                storage.SetValue(null, originalStorage);
                StringAssert.StartsWith(Path.Combine(Path.GetDirectoryName(Application.dataPath), ".work", "BuildQueueReport_"), root);
                Directory.Delete(root, true);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ForeignCompilationOwnerBlocksRestoredBuildBeforePreparationOrInvocation(bool afterPreparation)
        {
            Assert.IsNull(CompilationJobTracker.Reservation.State, "Preserve any real compilation owner.");
            BuildJob original = Job();
            BuildRunner.ScheduleBuild(original, Options(original));
            SimulateDomainLoss();
            Restore();
            if (afterPreparation)
                Advance();
            string generation = CompilationJobTracker.Reservation.Reserve(
                "foreign-compile-" + Guid.NewGuid().ToString("N"),
                EditorApplication.timeSinceStartup + 60
            );
            try
            {
                LogAssert.Expect(LogType.Error, new Regex("Queued build native admission blocked: native_job_active"));
                Advance();
                Advance();
                Assert.AreEqual(0, invocations);
                StringAssert.Contains("native_job_active", BuildJobStore.GetBuildJob(original.JobId).ErrorMessage);
                Assert.IsNotNull(CompilationJobTracker.Reservation.State);
            }
            finally
            {
                Assert.IsTrue(CompilationJobTracker.Reservation.AbortBeforeStart(generation));
            }
        }

        [Test]
        public void OwnQueuedReservationDoesNotHideLostNativeBuildFence()
        {
            BuildJob job = Job();
            BuildRunner.ScheduleBuild(job, Options(job));
            SessionState.SetBool(NativeKey, true);
            Assert.IsTrue(BuildRunner.NativeRecoveryRequired);
            Assert.IsTrue(NativeOperationBarriers.TryGetBuildContinuationBlocker(job.JobId, null, out var blocker));
            Assert.AreEqual("recovery_required", blocker.Reason);
            LogAssert.Expect(LogType.Error, new Regex("Queued build native admission blocked: recovery_required"));
            Advance();
            Assert.AreEqual(0, invocations);
            Assert.IsTrue(BuildRunner.NativeRecoveryRequired);
        }

        [Test]
        public void BuildContinuationExcludesOnlyItsExactChildAndBatchIds()
        {
            string childId = "owned-child";
            string batchId = "owned-batch";
            BuildRunner.ReserveNative(childId);
            BuildRunner.ReserveNative(batchId);
            try
            {
                Assert.IsFalse(BuildRunner.HasForeignNativeOwner(childId, batchId));
                Assert.IsFalse(NativeOperationBarriers.TryGetBuildContinuationBlocker(childId, batchId, out _));
                BuildRunner.ReserveNative("foreign-build");
                Assert.IsTrue(NativeOperationBarriers.TryGetBuildContinuationBlocker(childId, batchId, out var blocker));
                Assert.AreEqual("native_job_active", blocker.Reason);
            }
            finally
            {
                BuildRunner.ReleaseNative(childId);
                BuildRunner.ReleaseNative(batchId);
                BuildRunner.ReleaseNative("foreign-build");
            }
        }

        private static BatchJob CompleteFinalChildBeforeSettlement()
        {
            BuildJob child = Job();
            var batch = new BatchJob("late-batch-" + Guid.NewGuid().ToString("N")) { State = BuildJobState.Building };
            batch.Children.Add(child);
            BuildQueueEntry entry = BuildQueueEntry.Single(child, Options(child));
            entry.Id = batch.JobId;
            entry.Batch = true;
            BuildQueueCheckpoint.Enqueue(entry, jobs: batch.Children, batch: batch);
            child.State = BuildJobState.Succeeded;
            child.CompletedAt = DateTime.UtcNow;
            BuildQueueCheckpoint.Complete(child);
            return batch;
        }

#if UNITY_6000_0_OR_NEWER
        [TestCase(false)]
        [TestCase(true)]
        public void ProfileCheckpointFreezesOnlyItsEffectiveScenes(bool overridesGlobal)
        {
            string sourceScene = AssetDatabase
                .FindAssets("t:Scene")
                .Select(AssetDatabase.GUIDToAssetPath)
                .FirstOrDefault(path => path.StartsWith("Assets/", StringComparison.Ordinal));
            Assert.IsNotNull(sourceScene, "The owned test project must contain a preauthored scene.");
            string prefix = "Assets/__QueuedProfile_" + Guid.NewGuid().ToString("N");
            string profilePath = prefix + ".asset";
            string profileScene = prefix + "_Profile.unity";
            string globalScene = prefix + "_Global.unity";
            string changedGlobalScene = prefix + "_ChangedGlobal.unity";
            var ownedScenes = new List<string>();
            bool ownsProfilePath = false;
            EditorBuildSettingsScene[] originalScenes = EditorBuildSettings.scenes;
            var profile = ScriptableObject.CreateInstance<UnityEditor.Build.Profile.BuildProfile>();
            try
            {
                foreach (string scene in new[] { profileScene, globalScene, changedGlobalScene })
                {
                    Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(scene), "Preserve any preexisting scene asset.");
                    ownedScenes.Add(scene);
                    Assert.IsTrue(AssetDatabase.CopyAsset(sourceScene, scene), "Create an owned real scene for the profile fixture.");
                    Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(scene));
                }
                profile.overrideGlobalScenes = overridesGlobal;
                profile.scenes = new[] { new EditorBuildSettingsScene(profileScene, true) };
                EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(globalScene, true) };
                Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(profilePath), "Preserve any preexisting profile asset.");
                ownsProfilePath = true;
                AssetDatabase.CreateAsset(profile, profilePath);
                AssetDatabase.SaveAssets();
                BuildJob job = Job();
                BuildQueueChild child = BuildQueueChild.Capture(job, Options(job));
                BuildRunner.CaptureProfile(child, new BuildPlayerWithProfileOptions { buildProfile = profile });
                CollectionAssert.AreEqual(new[] { overridesGlobal ? profileScene : globalScene }, child.Options.scenes);
                EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(changedGlobalScene, true) };
                if (overridesGlobal)
                    Assert.DoesNotThrow(() => BuildQueueCheckpoint.ValidateProfile(child));
                else
                    StringAssert.Contains(
                        "scene settings changed",
                        Assert.Throws<InvalidOperationException>(() => BuildQueueCheckpoint.ValidateProfile(child)).Message
                    );
            }
            finally
            {
                EditorBuildSettings.scenes = originalScenes;
                if (ownsProfilePath)
                    AssetDatabase.DeleteAsset(profilePath);
                if (profile != null)
                    UnityEngine.Object.DestroyImmediate(profile);
                foreach (string scene in ownedScenes)
                    AssetDatabase.DeleteAsset(scene);
            }
        }

        [Test]
        public void PublicProfileSchedulingPreservesManifestAndIgnoresUnrelatedGlobalScenesAfterReload()
        {
            string scene = AssetDatabase
                .FindAssets("t:Scene")
                .Select(AssetDatabase.GUIDToAssetPath)
                .First(path => path.StartsWith("Assets/", StringComparison.Ordinal));
            string profilePath = "Assets/__QueuedProfile_" + Guid.NewGuid().ToString("N") + ".asset";
            EditorBuildSettingsScene[] originalScenes = EditorBuildSettings.scenes;
            var profile = ScriptableObject.CreateInstance<UnityEditor.Build.Profile.BuildProfile>();
            Func<BuildPlayerWithProfileOptions, BuildReport> originalBuilder = BuildRunner.BuildProfileForQueuedBuild;
            try
            {
                profile.overrideGlobalScenes = true;
                profile.scenes = new[] { new EditorBuildSettingsScene(scene, true) };
                EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene("Assets/__MissingUnrelatedGlobalScene.unity", true) };
                AssetDatabase.CreateAsset(profile, profilePath);
                AssetDatabase.SaveAssets();
                BuildRunner.BuildProfileForQueuedBuild = options =>
                {
                    invocations++;
                    Assert.AreEqual("Builds/bundles.manifest", options.assetBundleManifestPath);
                    Assert.AreEqual(profilePath, AssetDatabase.GetAssetPath(options.buildProfile));
                    throw new InvalidOperationException("controlled queued build invocation");
                };
                BuildJob original = Job();
                BuildRunner.ScheduleProfileBuild(
                    original,
                    new BuildPlayerWithProfileOptions
                    {
                        buildProfile = profile,
                        locationPathName = original.OutputPath,
                        assetBundleManifestPath = "Builds/bundles.manifest",
                        options = BuildOptions.Development,
                    }
                );
                SimulateDomainLoss();
                Restore();
                ExpectInvocationFailure();
                Advance();
                Advance();
                Advance();
                Assert.AreEqual(1, invocations);
                Assert.AreEqual("controlled queued build invocation", BuildJobStore.GetBuildJob(original.JobId).ErrorMessage);
            }
            finally
            {
                BuildRunner.BuildProfileForQueuedBuild = originalBuilder;
                EditorBuildSettings.scenes = originalScenes;
                AssetDatabase.DeleteAsset(profilePath);
                if (profile != null)
                    UnityEngine.Object.DestroyImmediate(profile);
            }
        }
#endif

        private static BuildJob Job() =>
            new BuildJob("queued-test-" + Guid.NewGuid().ToString("N"), EditorUserBuildSettings.activeBuildTarget, "Builds/UnusedQueuedTest.exe");

        private static BuildPlayerOptions Options(BuildJob job) =>
            BuildRunner.CreateBuildOptions(job.Target, job.OutputPath, Array.Empty<string>(), BuildOptions.None, 0);

        private static void ExpectInvocationFailure() => LogAssert.Expect(LogType.Error, new Regex("controlled queued build invocation"));

        private static void Restore() => typeof(BuildQueueCheckpoint).GetMethod("Restore", PrivateStatic).Invoke(null, null);

        private static EditorApplication.CallbackFunction[] Callbacks() =>
            EditorApplication.update?.GetInvocationList().Cast<EditorApplication.CallbackFunction>().ToArray()
            ?? Array.Empty<EditorApplication.CallbackFunction>();

        private static bool IsBuildCallback(EditorApplication.CallbackFunction callback) =>
            callback.Method.DeclaringType == typeof(BuildRunner) || callback.Method.DeclaringType?.DeclaringType == typeof(BuildRunner);

        private void DropCallbacks()
        {
            foreach (var callback in Callbacks().Except(previousCallbacks).Where(IsBuildCallback))
                EditorApplication.update -= callback;
        }

        private void Advance()
        {
            foreach (var callback in Callbacks().Except(previousCallbacks).Where(IsBuildCallback).ToArray())
                callback();
        }

        private void SimulateDomainLoss()
        {
            DropCallbacks();
            ForgetDomain();
        }

        private static void ForgetDomain()
        {
            Owners("NativeOwners").Clear();
            Owners("InvokedOwners").Clear();
            Jobs.Clear();
            Batches.Clear();
            typeof(BuildJobStore).GetField("_lastCompletedJob", PrivateStatic).SetValue(null, null);
            typeof(BuildQueueCheckpoint).GetField("entries", PrivateStatic).SetValue(null, null);
            ((HashSet<string>)typeof(BuildQueueCheckpoint).GetField("Scheduled", PrivateStatic).GetValue(null)).Clear();
            ((Dictionary<string, Action>)typeof(BuildQueueCheckpoint).GetField("Preparations", PrivateStatic).GetValue(null)).Clear();
        }
    }
}
