using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Windows.Components.Activity;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class AdditionalActivityOwnerTests
    {
        private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.NonPublic;
        private Dictionary<string, PackageJob> savedPackages;
        private Func<string> reader;
        private Action<string> writer;
        private JObject registration;
        private int writes;
        private static Dictionary<string, PackageJob> Packages =>
            (Dictionary<string, PackageJob>)typeof(PackageJobManager).GetField("Jobs", Hidden).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            savedPackages = new Dictionary<string, PackageJob>(Packages);
            Packages.Clear();
            reader = PackageJobManager.SessionReader;
            writer = PackageJobManager.SessionWriter;
            registration = PackageJobManager.ResolveRegistration;
            PackageJobManager.SessionReader = () => "";
            PackageJobManager.SessionWriter = _ => writes++;
            PackageJobManager.ResolveRegistration = null;
            writes = 0;
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            Packages.Clear();
            foreach (var pair in savedPackages)
                Packages.Add(pair.Key, pair.Value);
            PackageJobManager.SessionReader = reader;
            PackageJobManager.SessionWriter = writer;
            PackageJobManager.ResolveRegistration = registration;
            OperationActivity.Invalidate();
        }

        private static void Store(AssetGenJob job)
        {
            var jobs = (Dictionary<string, AssetGenJob>)typeof(AssetGenJobManager).GetField("Jobs", Hidden).GetValue(null);
            jobs[job.JobId] = job;
            typeof(AssetGenJobManager).GetMethod("Persist", Hidden).Invoke(null, new object[] { job });
        }

        private static OperationActivityRow Find(string owner, string id)
        {
            var row = OperationActivity.Capture().SingleOrDefault(value => value.Owner == owner && value.Id == id);
            Assert.IsNotNull(row, "Retained owner activity must be visible without executing its work.");
            return row;
        }

        [Test]
        public void PackageUnknownNativeFailureAndExpiryStayDistinct()
        {
            long before = OperationActivity.Revision;
            string id = PackageJobManager.StartJob("add", "https://user:PRIVATE@git.invalid/repo.git");
            var running = Find("package", id);
            Assert.Greater(OperationActivity.Revision, before);
            Assert.IsTrue(running.Active);
            Assert.IsFalse(running.CanCancel);
            var job = PackageJobManager.GetJob(id);
            job.Status = PackageJobStatus.Unknown;
            job.RecoveryReason = "request_lost_after_reload";
            PackageJobManager.PersistToSessionState();
            var unknown = Find("package", id);
            Assert.AreEqual("unknown", unknown.State);
            Assert.IsTrue(unknown.Active);
            Assert.IsNull(unknown.Finished);
            StringAssert.Contains("retry", unknown.Detail);
            StringAssert.DoesNotContain("PRIVATE", unknown.Detail);
            int beforeRead = writes;
            long beforeRevision = OperationActivity.Revision;
            JObject evidence = JObject.FromObject(OperationActivity.Evidence(unknown));
            Assert.AreEqual(id, (string)evidence["job_id"]);
            StringAssert.DoesNotContain("PRIVATE", evidence.ToString());
            Assert.AreEqual("request_lost_after_reload", (string)evidence["recovery"]?["reason"]);
            Assert.AreEqual(beforeRead, writes);
            Assert.AreEqual(beforeRevision, OperationActivity.Revision);
            PackageJobManager.CompleteJob(id, false, "native error: https://user:PRIVATE@git.invalid/repo.git");
            var failed = Find("package", id);
            Assert.AreEqual("failed", failed.State);
            Assert.IsFalse(failed.Active);
            Assert.IsNotNull(failed.Finished);
            var failedEvidence = JObject.FromObject(OperationActivity.Evidence(failed));
            Assert.IsTrue((bool)failedEvidence["has_error"]);
            StringAssert.DoesNotContain("PRIVATE", failedEvidence.ToString());
            Assert.IsTrue(PackageJobManager.TryStartResolveJob(1, out var resolve));
            PackageJobManager.FinishResolveInvocation(resolve, true, 2);
            var resolveEvidence = JObject.FromObject(OperationActivity.Evidence(Find("package", resolve.JobId)));
            Assert.AreEqual("unknown", (string)resolveEvidence["resolve"]?["completion"]);
            Assert.AreEqual(resolve.JobId, (string)resolveEvidence["job_id"]);
            Packages.Remove(id);
            StringAssert.Contains("retained", JObject.FromObject(OperationActivity.Evidence(failed)).ToString());
        }

        [Test]
        public void GenerationRecoveryAndLocalCancellationDoNotClaimRemoteCompletion()
        {
            var unknown = new AssetGenJob
            {
                JobId = "unknown-generation",
                Kind = "model",
                Provider = "tripo",
                State = AssetGenJobState.Failed,
                RecoveryState = "outcome_unknown",
                RecoveryCode = "reload_submission_unknown",
                ProviderTaskId = "owned-receipt",
            };
            Store(unknown);
            var row = Find("generation", unknown.JobId);
            Assert.AreEqual("unknown", row.State);
            Assert.IsFalse(row.CanCancel);
            StringAssert.Contains("outcome_unknown", row.Detail);
            StringAssert.Contains("owned-receipt", JObject.FromObject(OperationActivity.Evidence(row)).ToString());
            var queued = new AssetGenJob
            {
                JobId = "queued-generation",
                Kind = "image",
                Provider = "fal",
                State = AssetGenJobState.Queued,
            };
            Store(queued);
            var active = Find("generation", queued.JobId);
            Assert.IsTrue(active.Active && active.CanCancel);
            long revision = OperationActivity.Revision;
            OperationActivity.Cancel(active);
            Assert.AreEqual(AssetGenJobState.Canceled, queued.State);
            Assert.Greater(OperationActivity.Revision, revision);
            var canceled = Find("generation", queued.JobId);
            Assert.IsFalse(canceled.CanCancel);
            StringAssert.Contains("provider", canceled.Cancellation.ToLowerInvariant());
            StringAssert.Contains("not confirmed", canceled.Cancellation);
        }

        [Test]
        public void ViewingAndDisposingDoesNotStartWorkAndPrunedEvidenceIsExplicit()
        {
            Store(new AssetGenJob { JobId = "first-result", State = AssetGenJobState.Done });
            var first = Find("generation", "first-result");
            Store(new AssetGenJob { JobId = "queued-view", State = AssetGenJobState.Queued });
            var runners = (System.Collections.IDictionary)typeof(AssetGenJobManager).GetField("Runners", Hidden).GetValue(null);
            int retained = AssetGenJobManager.RecentJobs(int.MaxValue).Count;
            int beforeWrites = writes;
            var root = new VisualElement();
            using (var section = new McpActivitySection(root))
            {
                section.SetVisible(true);
                for (int i = 0; i < 100; i++)
                    section.Tick();
                Assert.AreEqual(1, section.CaptureCount);
                Assert.Zero(runners.Count);
                Assert.IsFalse((bool)typeof(AssetGenJobManager).GetField("_ticking", Hidden).GetValue(null));
                Assert.AreEqual(retained, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
                Assert.AreEqual(beforeWrites, writes);
                section.SetVisible(false);
            }
            Assert.Zero(root.childCount);
            for (int i = 0; i < 100; i++)
                Store(new AssetGenJob { JobId = "history-" + i, State = AssetGenJobState.Done });
            Assert.IsNull(AssetGenJobManager.GetJob("first-result"));
            Assert.IsFalse(OperationActivity.Capture().Any(row => row.Owner == "generation" && row.Id == "first-result"));
            StringAssert.Contains("retained", JObject.FromObject(OperationActivity.Evidence(first)).ToString());
        }

        private sealed class PendingTransport : IHttpTransport
        {
            internal readonly TaskCompletionSource<HttpResult> Completion = new();
            internal int Calls;

            public Task<HttpResult> SendAsync(HttpRequestSpec request, CancellationToken token = default)
            {
                Calls++;
                return Completion.Task;
            }
        }

        [Test]
        public void ActiveOwnerInvalidatesOnlyChangedProgressAndCancellationIsLocal()
        {
            const string key = "MCPFORUNITY_TRIPO_API_KEY";
            string previousKey = Environment.GetEnvironmentVariable(key);
            var transport = new PendingTransport();
            try
            {
                Environment.SetEnvironmentVariable(key, "owned-activity-test-key");
                AssetGenJobManager.TransportOverrideForTests = transport;
                AssetGenJobManager.SkipModelVerificationForTests = true;
                var job = AssetGenJobManager.StartModelGeneration(
                    new ModelGenRequest
                    {
                        Provider = "tripo",
                        Mode = "text",
                        Prompt = "owned activity fixture",
                        OutputFolder = "Assets",
                        Name = "owned-activity",
                    }
                );
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
                Assert.AreEqual(1, transport.Calls);
                job.ProgressEvidence.Begin(AssetOperationPhase.Downloading);
                job.ProgressEvidence.ReportBytes(8000000000L, 16000000000L);
                long before = OperationActivity.Revision;
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
                Assert.Greater(OperationActivity.Revision, before);
                var row = Find("generation", job.JobId);
                StringAssert.Contains("8000000000", row.Detail);
                before = OperationActivity.Revision;
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
                Assert.AreEqual(before, OperationActivity.Revision, "Unchanged progress/elapsed observation must not invalidate rows every update.");
                OperationActivity.Evidence(row);
                OperationActivity.Cancel(row);
                Assert.IsFalse(transport.Completion.Task.IsCompleted, "A provider ignoring local cancellation may still be working.");
                Assert.AreEqual(1, transport.Calls, "Viewing and cancellation never resubmit.");
                var canceled = Find("generation", job.JobId);
                Assert.IsFalse(canceled.Active || canceled.CanCancel);
                StringAssert.Contains("not confirmed", canceled.Cancellation);
            }
            finally
            {
                transport.Completion.TrySetCanceled();
                AssetGenJobManager.ResetForTests();
                Environment.SetEnvironmentVariable(key, previousKey);
            }
        }

        [Test]
        public void GenerationRowsKeepLargeByteCountsAndDetachFromPayloads()
        {
            var job = new AssetGenJob
            {
                JobId = "large-download",
                State = AssetGenJobState.Running,
                Kind = "model",
                Provider = "tripo",
                ProgressEvidence = new AssetOperationProgress(),
                ArchiveSelection = new JObject { ["private"] = "PRIVATE_PAYLOAD" },
            };
            job.ProgressEvidence.Begin(AssetOperationPhase.Downloading);
            job.ProgressEvidence.ReportBytes(9000000000L, 16000000000L);
            Store(job);
            var row = Find("generation", job.JobId);
            StringAssert.Contains("9000000000", row.Detail);
            StringAssert.Contains("16000000000", row.Detail);
            StringAssert.DoesNotContain("PRIVATE_PAYLOAD", row.Detail);
            string captured = row.Detail;
            job.ProgressEvidence.ReportBytes(12000000000L, 16000000000L);
            Assert.AreEqual(captured, row.Detail);
            Assert.AreEqual("downloading", row.Phase);
            row.State = "caller mutation";
            Assert.AreEqual(AssetGenJobState.Running, job.State);
        }
    }
}
