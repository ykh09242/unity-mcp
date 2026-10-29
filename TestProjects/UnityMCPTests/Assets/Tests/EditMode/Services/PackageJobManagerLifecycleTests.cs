using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class PackageJobManagerLifecycleTests
    {
        private Dictionary<string, PackageJob> _originalJobs;
        private string _originalSession;
        private System.Func<UnityEditor.PackageManager.PackageInfo[]> _originalRegistrationReader;
        private static Dictionary<string, PackageJob> Jobs =>
            (Dictionary<string, PackageJob>)typeof(PackageJobManager).GetField("Jobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            _originalJobs = new Dictionary<string, PackageJob>(Jobs);
            _originalSession = SessionState.GetString("MCPForUnity.PackageJobsV1", string.Empty);
            Jobs.Clear();
            _originalRegistrationReader = PackageJobManager.RegisteredPackagesReader;
        }

        [TearDown]
        public void TearDown()
        {
            Jobs.Clear();
            foreach (var pair in _originalJobs)
                Jobs.Add(pair.Key, pair.Value);
            SessionState.SetString("MCPForUnity.PackageJobsV1", _originalSession);
            PackageJobManager.RegisteredPackagesReader = _originalRegistrationReader;
        }

        [Test]
        public void CompletedHistory_IsBoundedAndRetainsNewestJobs()
        {
            for (int i = 0; i < 25; i++)
                Jobs.Add(
                    "done-" + i,
                    new PackageJob
                    {
                        JobId = "done-" + i,
                        Status = PackageJobStatus.Succeeded,
                        LastUpdateUnixMs = i + 1,
                    }
                );
            PackageJobManager.PersistToSessionState();
            Assert.AreEqual(10, Jobs.Count);
            Assert.IsFalse(Jobs.ContainsKey("done-14"));
            Assert.IsTrue(Jobs.ContainsKey("done-15"));
            Assert.IsTrue(Jobs.ContainsKey("done-24"));
        }

        [Test]
        public void HistoryPruning_NeverDropsInFlightRequestsEvenAboveHistoryLimit()
        {
            for (int i = 0; i < 12; i++)
                Jobs.Add(
                    "active-" + i,
                    new PackageJob
                    {
                        JobId = "active-" + i,
                        Status = PackageJobStatus.Running,
                        LastUpdateUnixMs = i + 1,
                    }
                );
            for (int i = 0; i < 20; i++)
                Jobs.Add(
                    "done-" + i,
                    new PackageJob
                    {
                        JobId = "done-" + i,
                        Status = PackageJobStatus.Failed,
                        LastUpdateUnixMs = i + 100,
                    }
                );
            PackageJobManager.PersistToSessionState();
            Assert.AreEqual(22, Jobs.Count);
            Assert.IsFalse(Jobs.ContainsKey("done-9"));
            Assert.IsTrue(Jobs.ContainsKey("done-10"));
            Assert.IsTrue(Jobs.ContainsKey("done-19"));
            var persisted = JObject.Parse(SessionState.GetString("MCPForUnity.PackageJobsV1", string.Empty));
            Assert.AreEqual(22, ((JArray)persisted["jobs"]).Count);
            for (int i = 0; i < 12; i++)
                Assert.IsNotNull(PackageJobManager.GetJob("active-" + i));

            // Terminal history has its own limit; completing a request retains its result.
            PackageJobManager.CompleteJob("active-0", true);
            Assert.AreEqual(21, Jobs.Count);
            Assert.IsNotNull(PackageJobManager.GetJob("active-0"));
            Assert.IsFalse(Jobs.ContainsKey("done-10"));
            Assert.IsNotNull(PackageJobManager.GetJob("active-11"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CompletingOneOfManyRunningJobsRetainsItsResult(bool success)
        {
            var ids = new List<string>();
            for (int i = 0; i < 12; i++)
                ids.Add(PackageJobManager.StartJob("add", "com.fixture.p" + i));
            Assert.AreEqual(12, Jobs.Count);
            PackageJobManager.CompleteJob(ids[0], success, error: success ? null : "owned-error", version: "1.2.3", name: "com.fixture.p0");
            Assert.AreEqual(12, Jobs.Count);
            var result = PackageJobManager.GetJob(ids[0]);
            Assert.IsNotNull(result);
            Assert.AreEqual(success ? PackageJobStatus.Succeeded : PackageJobStatus.Failed, result.Status);
            Assert.AreEqual("1.2.3", result.ResultVersion);
            foreach (string id in ids.GetRange(1, 11))
                Assert.AreEqual(PackageJobStatus.Running, PackageJobManager.GetJob(id).Status);
            var persisted = JObject.Parse(SessionState.GetString("MCPForUnity.PackageJobsV1", string.Empty));
            Assert.AreEqual(12, ((JArray)persisted["jobs"]).Count);
        }

        [Test]
        public void RetainedTerminalHistoryRestoresItsResult()
        {
            string id = PackageJobManager.StartJob("add", "com.fixture.restore");
            PackageJobManager.CompleteJob(id, false, error: "owned-error");
            Jobs.Clear();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            var restored = PackageJobManager.GetJob(id);
            Assert.IsNotNull(restored);
            Assert.AreEqual(PackageJobStatus.Failed, restored.Status);
            Assert.AreEqual("owned-error", restored.Error);
        }

        [TestCase("add")]
        [TestCase("embed")]
        [TestCase("remove")]
        public void LostRequestRemainsUnknownBeyondFormerTimeoutDespiteDesiredState(string operation)
        {
            const long now = 10_000_000;
            var job = new PackageJob
            {
                JobId = "recovery-fixture",
                Status = PackageJobStatus.Running,
                Operation = operation,
                Package = "com.fixture.tool@1.0.0",
                StartedUnixMs = now - 7_200_000,
            };
            var info = PackageInfoFixture();
            var observed = operation == "remove" ? new[] { info } : System.Array.Empty<UnityEditor.PackageManager.PackageInfo>();
            RecoverWithSnapshot(job, now, observed);
            Assert.AreEqual(PackageJobStatus.Unknown, job.Status);
            Assert.IsNull(job.FinishedUnixMs, "Losing observation is not native completion.");
            Assert.IsNull(job.Error, "An observer cannot invent a native error.");
            var unknown = JObject.FromObject(PackageJobManager.ToSerializable(job));
            Assert.AreEqual("request_lost_after_reload", (string)unknown["recovery"]?["reason"]);
            Assert.AreEqual(false, (bool?)unknown["recovery"]?["retry_safe"]);
            RecoverWithSnapshot(job, now + 1, operation == "remove" ? System.Array.Empty<UnityEditor.PackageManager.PackageInfo>() : new[] { info });
            Assert.AreEqual(PackageJobStatus.Unknown, job.Status);
            Assert.IsNull(job.FinishedUnixMs);
            Assert.IsNull(job.ResultVersion, "Observed registration must not masquerade as the Request result.");
            var recovered = JObject.FromObject(PackageJobManager.ToSerializable(job));
            Assert.AreEqual("request_lost_after_reload", (string)recovered["recovery"]?["reason"]);
            Assert.AreEqual(true, (bool?)recovered["package_observation"]?["desired_state_observed"]);
            Assert.AreEqual("none", (string)recovered["package_observation"]?["completion_effect"]);
        }

        [Test]
        public void GenuineNativeFailureCannotBeReconciledIntoSuccess()
        {
            var job = new PackageJob
            {
                JobId = "native-failure",
                Status = PackageJobStatus.Failed,
                Operation = "add",
                Package = "com.fixture.tool@1.0.0",
                Error = "owned-error",
            };
            RecoverWithSnapshot(job, 10_000_000, new[] { PackageInfoFixture() });
            Assert.AreEqual(PackageJobStatus.Failed, job.Status);
            Assert.AreEqual("owned-error", job.Error);
        }

        [TestCase("add")]
        [TestCase("embed")]
        [TestCase("remove")]
        public void UnknownMutationStatusStaysUnknownThroughPublicStatusAndSessionRestore(string operation)
        {
            PackageJobManager.RegisteredPackagesReader = () =>
                operation == "remove" ? System.Array.Empty<UnityEditor.PackageManager.PackageInfo>() : new[] { PackageInfoFixture() };
            string id = PackageJobManager.StartJob(operation, "com.fixture.tool@1.0.0");
            PackageJobManager.PersistToSessionState();
            Jobs.Clear();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            Assert.AreEqual(PackageJobStatus.Unknown, PackageJobManager.GetJob(id).Status);
            var status = JObject.FromObject(MCPForUnity.Editor.Tools.ManagePackages.HandleCommand(new JObject { ["action"] = "status", ["job_id"] = id }));
            Assert.AreEqual("unknown", (string)status["data"]?["status"]);
            Assert.AreEqual(true, (bool?)status["data"]?["package_observation"]?["desired_state_observed"]);
            PackageJobManager.PersistToSessionState();
            Jobs.Clear();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            Assert.AreEqual(PackageJobStatus.Unknown, PackageJobManager.GetJob(id).Status);
        }

        [TestCase("add")]
        [TestCase("embed")]
        [TestCase("remove")]
        public void AlreadyDesiredStateDoesNotConfirmLostRequest(string operation)
        {
            var job = new PackageJob
            {
                JobId = "preexisting",
                Operation = operation,
                Package = "com.fixture.tool@1.0.0",
                Status = PackageJobStatus.Running,
                StartedUnixMs = 123,
            };
            RecoverWithSnapshot(
                job,
                456,
                operation == "remove" ? System.Array.Empty<UnityEditor.PackageManager.PackageInfo>() : new[] { PackageInfoFixture() }
            );
            Assert.AreEqual(PackageJobStatus.Unknown, job.Status);
            Assert.AreEqual(123, job.StartedUnixMs);
            Assert.IsNull(job.FinishedUnixMs);
            Assert.IsTrue((bool)job.PackageObservation["desired_state_observed"]);
        }

        [Test]
        public void ConfirmedNativeSuccessRemainsSuccessfulAcrossRestore()
        {
            string id = PackageJobManager.StartJob("add", "com.fixture.tool@1.0.0");
            PackageJobManager.CompleteJob(id, true, version: "1.0.0", name: "com.fixture.tool");
            Jobs.Clear();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            Assert.AreEqual(PackageJobStatus.Succeeded, PackageJobManager.GetJob(id).Status);
            Assert.AreEqual("1.0.0", PackageJobManager.GetJob(id).ResultVersion);
        }

        private static UnityEditor.PackageManager.PackageInfo PackageInfoFixture()
        {
            var type = typeof(UnityEditor.PackageManager.PackageInfo);
            var value = (UnityEditor.PackageManager.PackageInfo)
                type.GetConstructor(BindingFlags.Instance | BindingFlags.NonPublic, null, System.Type.EmptyTypes, null).Invoke(null);
            foreach (
                var pair in new Dictionary<string, object>
                {
                    ["m_Name"] = "com.fixture.tool",
                    ["m_Version"] = "1.0.0",
                    ["m_Source"] = UnityEditor.PackageManager.PackageSource.Embedded,
                    ["m_PackageId"] = "com.fixture.tool@1.0.0",
                }
            )
                type.GetField(pair.Key, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(value, pair.Value);
            return value;
        }

        private static void RecoverWithSnapshot(PackageJob job, long now, UnityEditor.PackageManager.PackageInfo[] snapshot)
        {
            typeof(PackageJobManager)
                .GetMethod("TryRecoverJobWithSnapshot", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, new object[] { job, now, snapshot });
        }

        [Test]
        public void UnknownTerminalStatusIsExplicitAndNotReinterpretedAsRunning()
        {
            var parse = typeof(PackageJobManager).GetMethod("ParseStatus", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.AreEqual("Unknown", parse.Invoke(null, new object[] { "unknown" }).ToString());
        }
    }
}
