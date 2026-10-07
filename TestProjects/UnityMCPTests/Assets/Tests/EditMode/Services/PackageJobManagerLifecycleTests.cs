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
        private static Dictionary<string, PackageJob> Jobs =>
            (Dictionary<string, PackageJob>)typeof(PackageJobManager).GetField("Jobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            _originalJobs = new Dictionary<string, PackageJob>(Jobs);
            _originalSession = SessionState.GetString("MCPForUnity.PackageJobsV1", string.Empty);
            Jobs.Clear();
        }

        [TearDown]
        public void TearDown()
        {
            Jobs.Clear();
            foreach (var pair in _originalJobs)
                Jobs.Add(pair.Key, pair.Value);
            SessionState.SetString("MCPForUnity.PackageJobsV1", _originalSession);
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
    }
}
