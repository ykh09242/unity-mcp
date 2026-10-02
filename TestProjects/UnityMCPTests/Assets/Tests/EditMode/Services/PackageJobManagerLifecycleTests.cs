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
            foreach (var pair in _originalJobs) Jobs.Add(pair.Key, pair.Value);
            SessionState.SetString("MCPForUnity.PackageJobsV1", _originalSession);
        }

        [Test]
        public void CompletedHistory_IsBoundedAndRetainsNewestJobs()
        {
            for (int i = 0; i < 25; i++) Jobs.Add("done-" + i, new PackageJob
            {
                JobId = "done-" + i, Status = PackageJobStatus.Succeeded, LastUpdateUnixMs = i + 1
            });
            PackageJobManager.PersistToSessionState();
            Assert.AreEqual(10, Jobs.Count);
            Assert.IsFalse(Jobs.ContainsKey("done-14"));
            Assert.IsTrue(Jobs.ContainsKey("done-15"));
            Assert.IsTrue(Jobs.ContainsKey("done-24"));
        }

        [Test]
        public void HistoryPruning_NeverDropsInFlightRequestsEvenAboveHistoryLimit()
        {
            for (int i = 0; i < 12; i++) Jobs.Add("active-" + i, new PackageJob
            {
                JobId = "active-" + i, Status = PackageJobStatus.Running, LastUpdateUnixMs = i + 1
            });
            for (int i = 0; i < 20; i++) Jobs.Add("done-" + i, new PackageJob
            {
                JobId = "done-" + i, Status = PackageJobStatus.Failed, LastUpdateUnixMs = i + 100
            });
            PackageJobManager.PersistToSessionState();
            Assert.AreEqual(12, Jobs.Count);
            var persisted = JObject.Parse(SessionState.GetString("MCPForUnity.PackageJobsV1", string.Empty));
            Assert.AreEqual(12, ((JArray)persisted["jobs"]).Count);
            for (int i = 0; i < 12; i++) Assert.IsNotNull(PackageJobManager.GetJob("active-" + i));

            // Completing a request opens room for pruning; other pending requests survive.
            PackageJobManager.CompleteJob("active-0", true);
            Assert.AreEqual(11, Jobs.Count);
            Assert.IsNotNull(PackageJobManager.GetJob("active-11"));
        }
    }
}
