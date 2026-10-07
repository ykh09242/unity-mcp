using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class BuildJobLifecycleIntegrityTests
    {
        private readonly HashSet<EditorApplication.CallbackFunction> originalCallbacks = new HashSet<EditorApplication.CallbackFunction>();
        private readonly HashSet<EditorApplication.CallbackFunction> ownedCallbacks = new HashSet<EditorApplication.CallbackFunction>();
        private readonly Dictionary<string, BuildJob> originalJobs = new Dictionary<string, BuildJob>();
        private readonly Dictionary<string, BatchJob> originalBatches = new Dictionary<string, BatchJob>();
        private BuildJob originalLastCompleted;
        private bool capturedStore;

        private static Dictionary<string, BuildJob> Jobs =>
            (Dictionary<string, BuildJob>)typeof(BuildJobStore).GetField("_buildJobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
        private static Dictionary<string, BatchJob> Batches =>
            (Dictionary<string, BatchJob>)typeof(BuildJobStore).GetField("_batchJobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
        private static readonly FieldInfo LastCompleted = typeof(BuildJobStore).GetField("_lastCompletedJob", BindingFlags.Static | BindingFlags.NonPublic);

        private static bool IsRunnerCallback(EditorApplication.CallbackFunction callback)
        {
            Type owner = callback.Method.DeclaringType;
            return owner == typeof(BuildRunner) || (owner != null && owner.DeclaringType == typeof(BuildRunner));
        }

        [SetUp]
        public void SetUp()
        {
            capturedStore = false;
            originalCallbacks.Clear();
            ownedCallbacks.Clear();
            originalJobs.Clear();
            originalBatches.Clear();
            foreach (var callback in CurrentCallbacks())
                originalCallbacks.Add(callback);
            if (
                BuildPipeline.isBuildingPlayer
                || originalCallbacks.Any(IsRunnerCallback)
                || Jobs.Values.Any(job => job.State == BuildJobState.Pending || job.State == BuildJobState.Building)
                || Batches.Values.Any(batch => batch.State == BuildJobState.Pending || batch.State == BuildJobState.Building)
            )
                Assert.Ignore("An unowned build or runner callback is active.");

            foreach (var pair in Jobs)
                originalJobs.Add(pair.Key, pair.Value);
            foreach (var pair in Batches)
                originalBatches.Add(pair.Key, pair.Value);
            originalLastCompleted = BuildJobStore.LastCompletedJob;
            capturedStore = true;
            Jobs.Clear();
            Batches.Clear();
            LastCompleted.SetValue(null, null);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                CaptureCallbacks();
                foreach (var callback in ownedCallbacks)
                    EditorApplication.update -= callback;
            }
            finally
            {
                if (capturedStore)
                {
                    Jobs.Clear();
                    Batches.Clear();
                    foreach (var pair in originalJobs)
                        Jobs.Add(pair.Key, pair.Value);
                    foreach (var pair in originalBatches)
                        Batches.Add(pair.Key, pair.Value);
                    LastCompleted.SetValue(null, originalLastCompleted);
                }
                capturedStore = false;
                ownedCallbacks.Clear();
                originalCallbacks.Clear();
                originalJobs.Clear();
                originalBatches.Clear();
                originalLastCompleted = null;
            }
        }

        private static IEnumerable<EditorApplication.CallbackFunction> CurrentCallbacks()
        {
            return EditorApplication.update == null
                ? Enumerable.Empty<EditorApplication.CallbackFunction>()
                : EditorApplication.update.GetInvocationList().Cast<EditorApplication.CallbackFunction>();
        }

        private void CaptureCallbacks()
        {
            foreach (var callback in CurrentCallbacks())
            {
                if (originalCallbacks.Contains(callback))
                    continue;
                if (IsRunnerCallback(callback))
                    ownedCallbacks.Add(callback);
            }
        }

        private void AdvanceOwnedCallbacks()
        {
            CaptureCallbacks();
            var callbacks = CurrentCallbacks().Where(ownedCallbacks.Contains).ToArray();
            foreach (var callback in callbacks)
            {
                callback();
                CaptureCallbacks();
            }
        }

        private BuildJob Child()
        {
            string id = "mcp-build-lifecycle-" + Guid.NewGuid().ToString("N");
            return new BuildJob(id, BuildTarget.StandaloneWindows64, "unused-output");
        }

        [TestCase(BuildJobState.Pending)]
        [TestCase(BuildJobState.Building)]
        public void StatusWithoutIdFindsActiveBuildBeforeAnyCompletion(BuildJobState state)
        {
            BuildJob active = Child();
            active.State = state;
            BuildJobStore.AddBuildJob(active);
            AssertStatus(new JObject { ["action"] = "status" }, active.JobId, true);
        }

        [Test]
        public void StatusWithoutIdPrefersActiveBuildAndPreservesExplicitId()
        {
            BuildJob completed = Child();
            completed.State = BuildJobState.Succeeded;
            BuildJobStore.AddBuildJob(completed);
            BuildJobStore.SetLastCompleted(completed);
            BuildJob active = Child();
            BuildJobStore.AddBuildJob(active);
            AssertStatus(new JObject { ["action"] = "status" }, active.JobId, true);
            AssertStatus(new JObject { ["action"] = "status", ["job_id"] = completed.JobId }, completed.JobId, false);
            active.State = BuildJobState.Failed;
            AssertStatus(new JObject { ["action"] = "status" }, completed.JobId, false);
        }

        [TestCase(BuildJobState.Pending)]
        [TestCase(BuildJobState.Building)]
        public void StatusWithoutIdPrefersActiveBatchOverItsChild(BuildJobState state)
        {
            BuildJob child = Child();
            BuildJobStore.AddBuildJob(child);
            var batch = new BatchJob("local-active-batch") { State = state };
            batch.Children.Add(child);
            BuildJobStore.AddBatchJob(batch);
            AssertStatus(new JObject { ["action"] = "status" }, batch.JobId, true);
            AssertStatus(new JObject { ["action"] = "status", ["job_id"] = child.JobId }, child.JobId, true);
        }

        private static void AssertStatus(JObject request, string jobId, bool pending)
        {
            JObject response = JObject.FromObject(MCPForUnity.Editor.Tools.ManageBuild.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(jobId, response["data"].Value<string>("job_id"));
            Assert.AreEqual(pending ? "pending" : null, response.Value<string>("_mcp_status"));
        }

        [Test]
        public void CreatorExceptionFailsChildAndCompletesWithoutBuildCallbacks()
        {
            var batch = new BatchJob("local-batch") { State = BuildJobState.Building };
            BuildJob child = Child();
            batch.Children.Add(child);
            try
            {
                Assert.DoesNotThrow(() =>
                    BuildRunner.ScheduleNextBatchBuild(batch, index => throw new InvalidOperationException("controlled preparation failure"))
                );
            }
            finally
            {
                CaptureCallbacks();
            }
            Assert.AreEqual(BuildJobState.Failed, child.State);
            Assert.AreEqual(default(DateTime), child.StartedAt);
            Assert.IsTrue(child.CompletedAt.HasValue);
            Assert.AreEqual("controlled preparation failure", child.ErrorMessage);
            Assert.AreSame(child, BuildJobStore.LastCompletedJob);
            AdvanceOwnedCallbacks();
            Assert.AreEqual(BuildJobState.Failed, batch.State);
            Assert.IsFalse(CurrentCallbacks().Any(ownedCallbacks.Contains));
            JObject status = JObject.FromObject(child.ToStatusResponse());
            Assert.IsNull(status["duration_seconds"]);
            Assert.IsNotNull(status["completed_at"]);
        }

        [Test]
        public void CreatorFailuresAdvanceWithoutRecursivePreparation()
        {
            var batch = new BatchJob("local-chain") { State = BuildJobState.Building };
            for (int i = 0; i < 3; i++)
                batch.Children.Add(Child());
            int calls = 0;
            Func<int, BuildJob> prepare = index =>
            {
                calls++;
                throw new InvalidOperationException("controlled preparation failure");
            };
            try
            {
                Assert.DoesNotThrow(() => BuildRunner.ScheduleNextBatchBuild(batch, prepare));
            }
            finally
            {
                CaptureCallbacks();
            }
            Assert.AreEqual(1, calls);
            AdvanceOwnedCallbacks();
            Assert.AreEqual(2, calls);
            AdvanceOwnedCallbacks();
            Assert.AreEqual(3, calls);
            AdvanceOwnedCallbacks();
            Assert.AreEqual(BuildJobState.Failed, batch.State);
            Assert.IsTrue(batch.Children.All(child => child.State == BuildJobState.Failed));
            Assert.IsFalse(CurrentCallbacks().Any(ownedCallbacks.Contains));
        }

        [Test]
        public void CancelledBatchSkipsOnlyFollowingChildren()
        {
            var batch = new BatchJob("local-cancel") { State = BuildJobState.Cancelled, CurrentIndex = 0 };
            BuildJob completed = Child();
            completed.State = BuildJobState.Succeeded;
            batch.Children.Add(completed);
            batch.Children.Add(Child());
            batch.Children.Add(Child());
            BuildRunner.ScheduleNextBatchBuild(batch, index => throw new AssertionException("Must not prepare another build"));
            Assert.AreEqual(BuildJobState.Succeeded, completed.State);
            Assert.AreEqual(BuildJobState.Skipped, batch.Children[1].State);
            Assert.AreEqual(BuildJobState.Skipped, batch.Children[2].State);
            Assert.AreEqual(BuildJobState.Cancelled, batch.State);
            Assert.AreEqual(3, JObject.FromObject(batch.ToStatusResponse()).Value<int>("completed"));
            Assert.IsFalse(CurrentCallbacks().Except(originalCallbacks).Any());
        }

        [Test]
        public void EmptyBatchCompletesWithoutPreparingAnything()
        {
            var batch = new BatchJob("local-empty") { State = BuildJobState.Building };
            BuildRunner.ScheduleNextBatchBuild(batch, index => throw new AssertionException("Must not prepare a build"));
            Assert.AreEqual(BuildJobState.Succeeded, batch.State);
            Assert.AreEqual(0, JObject.FromObject(batch.ToStatusResponse()).Value<int>("total"));
            Assert.IsFalse(CurrentCallbacks().Except(originalCallbacks).Any());
        }

        [TestCase(BuildJobState.Failed)]
        [TestCase(BuildJobState.Cancelled)]
        [TestCase(BuildJobState.Skipped)]
        public void NeverStartedCompletionHasNoFictitiousDuration(BuildJobState state)
        {
            var job = Child();
            job.State = state;
            job.CompletedAt = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);
            JObject status = JObject.FromObject(job.ToStatusResponse());
            Assert.IsNull(status["started_at"]);
            Assert.IsNull(status["duration_seconds"]);
            Assert.IsNotNull(status["completed_at"]);
        }

        [Test]
        public void CompletedBuildRetainsActualDuration()
        {
            var job = Child();
            job.State = BuildJobState.Succeeded;
            job.StartedAt = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);
            job.CompletedAt = job.StartedAt.AddSeconds(32);
            JObject status = JObject.FromObject(job.ToStatusResponse());
            Assert.AreEqual(32, status.Value<double>("duration_seconds"));
            Assert.IsNotNull(status["started_at"]);
        }

        [Test]
        public void RetainedBatchKeepsOriginalChildrenAndSkippedSummary()
        {
            var batch = new BatchJob("local-history") { State = BuildJobState.Cancelled };
            BuildJob old = Child();
            old.State = BuildJobState.Succeeded;
            BuildJob anchor = Child();
            anchor.State = BuildJobState.Succeeded;
            BuildJob skipped = Child();
            skipped.State = BuildJobState.Skipped;
            batch.Children.AddRange(new[] { old, anchor, skipped });
            BuildJobStore.AddBuildJob(old);
            BuildJobStore.AddBuildJob(anchor);
            BuildJobStore.AddBatchJob(batch);
            for (int i = 0; i < 51; i++)
                BuildJobStore.AddBuildJob(Child());
            BuildJobStore.SetLastCompleted(anchor);
            Assert.IsNull(BuildJobStore.GetBuildJob(old.JobId));
            Assert.AreSame(anchor, BuildJobStore.GetBuildJob(anchor.JobId));
            Assert.AreSame(batch, BuildJobStore.GetBatchJob(batch.JobId));
            CollectionAssert.AreEqual(new[] { old, anchor, skipped }, batch.Children);
            JObject status = JObject.FromObject(batch.ToStatusResponse());
            Assert.AreEqual(3, status.Value<int>("total"));
            Assert.AreEqual(3, status.Value<int>("completed"));
        }

        [Test]
        public void TerminalBatchExpiresWholeOnlyWhenNoChildRemains()
        {
            var batch = new BatchJob("local-expiry") { State = BuildJobState.Succeeded };
            BuildJob old = Child();
            old.State = BuildJobState.Succeeded;
            batch.Children.Add(old);
            BuildJobStore.AddBuildJob(old);
            BuildJobStore.AddBatchJob(batch);
            for (int i = 0; i < 51; i++)
                BuildJobStore.AddBuildJob(Child());
            BuildJob latest = Child();
            latest.State = BuildJobState.Succeeded;
            BuildJobStore.AddBuildJob(latest);
            BuildJobStore.SetLastCompleted(latest);
            Assert.IsNull(BuildJobStore.GetBuildJob(old.JobId));
            Assert.IsNull(BuildJobStore.GetBatchJob(batch.JobId));
            Assert.AreEqual(1, batch.Children.Count);
            Assert.AreSame(old, batch.Children[0]);
            Assert.AreSame(latest, BuildJobStore.LastCompletedJob);
        }
    }
}
