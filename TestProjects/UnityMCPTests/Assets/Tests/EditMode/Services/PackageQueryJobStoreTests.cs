using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class PackageQueryJobStoreTests
    {
        private string stored;
        private Func<string> reader;
        private Action<string> writer;
        private Func<long> clock;
        private Dictionary<string, JObject> original;
        private static Dictionary<string, JObject> Receipts =>
            (Dictionary<string, JObject>)typeof(PackageQueryJobStore).GetField("Receipts", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

        [SetUp]
        public void Setup()
        {
            original = new Dictionary<string, JObject>(Receipts);
            reader = PackageQueryJobStore.SessionReader;
            writer = PackageQueryJobStore.SessionWriter;
            clock = PackageQueryJobStore.Clock;
            Receipts.Clear();
            stored = "";
            PackageQueryJobStore.SessionReader = () => stored;
            PackageQueryJobStore.SessionWriter = value => stored = value;
            PackageQueryJobStore.Clock = () => 1234;
        }

        [TearDown]
        public void Teardown()
        {
            Receipts.Clear();
            foreach (var pair in original)
                Receipts.Add(pair.Key, pair.Value);
            PackageQueryJobStore.SessionReader = reader;
            PackageQueryJobStore.SessionWriter = writer;
            PackageQueryJobStore.Clock = clock;
        }

        [TestCase("list_packages")]
        [TestCase("search_packages")]
        public void PendingQueryRetainsIdentityAndOriginalAdmissionAfterRestore(string operation)
        {
            string id = PackageQueryJobStore.Begin(operation);
            Assert.NotNull(id);
            Reload();
            JObject receipt = PackageQueryJobStore.Get(id);
            Assert.AreEqual(id, (string)receipt["data"]["job_id"]);
            Assert.AreEqual(operation, (string)receipt["data"]["operation"]);
            Assert.AreEqual("unknown", (string)receipt["data"]["status"]);
            Assert.AreEqual(1234, (long)receipt["data"]["started_unix_ms"]);
            Assert.IsNull(receipt["data"]["finished_unix_ms"]);
            Assert.AreEqual("request_lost_after_reload", (string)receipt["data"]["recovery"]["reason"]);
            Assert.IsFalse((bool)receipt["data"]["recovery"]["retry_safe"]);
            PackageQueryJobStore.Persist();
            Reload();
            Assert.AreEqual("unknown", (string)PackageQueryJobStore.Get(id)["data"]["status"]);
        }

        [TestCase("list_packages", true)]
        [TestCase("search_packages", true)]
        [TestCase("list_packages", false)]
        [TestCase("search_packages", false)]
        public void CompletedQueryRetainsItsOutcomeAndResult(string operation, bool success)
        {
            string id = PackageQueryJobStore.Begin(operation);
            var response = new JObject
            {
                ["success"] = success,
                ["data"] = new JObject { ["count"] = 1, ["packages"] = new JArray(new JObject { ["name"] = "com.fixture.tool" }) },
            };
            if (!success)
                response["error"] = "Owned error";
            JObject completed = PackageQueryJobStore.Complete(id, operation, response);
            Reload();
            Assert.IsTrue(JToken.DeepEquals(completed, PackageQueryJobStore.Get(id)));
            Assert.AreEqual(success ? "succeeded" : "failed", (string)completed["data"]["status"]);
            Assert.AreEqual("com.fixture.tool", (string)completed["data"]["packages"][0]["name"]);
        }

        [Test]
        public void BoundedHistoryRetainsPendingAndLostQueries()
        {
            string pending = PackageQueryJobStore.Begin("list_packages");
            for (int i = 0; i < 20; i++)
            {
                string id = PackageQueryJobStore.Begin("search_packages");
                PackageQueryJobStore.Complete(id, "search_packages", new JObject { ["success"] = true });
            }
            Reload();
            Assert.NotNull(PackageQueryJobStore.Get(pending));
            Assert.AreEqual(11, Receipts.Count);
            for (int i = 0; i < 63; i++)
                Assert.NotNull(PackageQueryJobStore.Begin("list_packages"));
            Assert.IsNull(PackageQueryJobStore.Begin("search_packages"));
            Reload();
            Assert.AreEqual(74, Receipts.Count);
            Assert.NotNull(PackageQueryJobStore.Get(pending));
        }

        [Test]
        public void OversizedResultRetainsProvedCompletionWithExplicitUnavailableResult()
        {
            string id = PackageQueryJobStore.Begin("list_packages");
            var response = new JObject
            {
                ["success"] = true,
                ["data"] = new JObject { ["large"] = new string('x', 140 * 1024) },
            };
            var timer = Stopwatch.StartNew();
            PackageQueryJobStore.Complete(id, "list_packages", response);
            Assert.Less(timer.ElapsedMilliseconds, 5000, "Oversized payloads must be discarded before credential pattern scans.");
            Reload();
            var data = PackageQueryJobStore.Get(id)["data"];
            Assert.AreEqual("succeeded", (string)data["status"]);
            Assert.AreEqual("size_limit", (string)data["result_availability"]);
            Assert.IsNull(data["large"]);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void QueryRetentionNeverStoresOrReturnsCredentials(bool oversized)
        {
            string id = PackageQueryJobStore.Begin("search_packages");
            var response = new JObject
            {
                ["success"] = false,
                ["error"] = "https://user:secret@host/repo.git?token=secret",
                ["data"] = new JObject { ["payload"] = oversized ? new string('x', 140 * 1024) : "user:secret@host:repo.git#secret" },
            };
            var timer = Stopwatch.StartNew();
            JObject completed = PackageQueryJobStore.Complete(id, "search_packages", response);
            Assert.Less(timer.ElapsedMilliseconds, 5000);
            StringAssert.DoesNotContain("secret", completed.ToString());
            StringAssert.DoesNotContain("secret", stored);
            Assert.AreEqual(oversized ? "size_limit" : "available", (string)completed["data"]["result_availability"]);
            Reload();
            StringAssert.DoesNotContain("secret", PackageQueryJobStore.Get(id).ToString());
            Assert.AreEqual("failed", (string)PackageQueryJobStore.Get(id)["data"]["status"]);
        }

        [TestCase("{")]
        [TestCase("{\"version\":1,\"jobs\":[{}]}")]
        [TestCase("{\"version\":2,\"jobs\":[]}")]
        [TestCase("{\"version\":1,\"version\":1,\"jobs\":[]}")]
        [TestCase("{\"version\":1,\"jobs\":[]} false")]
        public void MalformedStorageDoesNotCreateReceipts(string text)
        {
            stored = text;
            Reload();
            Assert.AreEqual(0, Receipts.Count);
        }

        [Test]
        public void OversizedStorageDoesNotCreateReceipts()
        {
            stored = new string(' ', 2 * 1024 * 1024 + 1);
            Reload();
            Assert.AreEqual(0, Receipts.Count);
        }

        [Test]
        public void FailedAdmissionPersistenceDoesNotAdmitWork()
        {
            PackageQueryJobStore.SessionWriter = value => throw new InvalidOperationException("owned fixture");
            Assert.IsNull(PackageQueryJobStore.Begin("list_packages"));
            Assert.AreEqual(0, Receipts.Count);
        }

        private static void Reload()
        {
            Receipts.Clear();
            PackageQueryJobStore.Restore();
        }
    }
}
