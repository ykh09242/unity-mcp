using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>Real temporary files only; no native runner, preferences or user Library access.</summary>
    public class TestJobResultStoreTests
    {
        internal const string JobId = "1234567890abcdef1234567890abcdef";
        internal const string SessionId = "abcdef1234567890abcdef1234567890";
        private string root;
        private object store;
        private static readonly string ReloadNonce = Guid.NewGuid().ToString("N");
        private const string Probe = "MCPForUnity.Play02ReloadProbe.";

        private static void ObserveReload() => SessionState.SetString(Probe + "witness", SessionState.GetString(Probe + "before", ""));

        private const string JobsKey = "MCPForUnity.TestJobsV1";
        private const string CurrentKey = "MCPForUnity.CurrentTestJobIdV1";
        private const BindingFlags ManagerFlags = BindingFlags.Static | BindingFlags.NonPublic;

        [UnityTest, Explicit("QA disposable project only; requires an exact UNITY_MCP_PLAY02_RELOAD_PROJECT opt-in.")]
        public IEnumerator ActualDomainReload_RetainsPublicPageAndExport()
        {
            // Test Framework resumes only the iterator program counter, not local values.
            try
            {
                PrepareReloadProbe(root);
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                VerifyReloadProbe();
            }
            finally
            {
                CleanupReloadProbe();
            }
        }

        private static void PrepareReloadProbe(string temporary)
        {
            string project = Directory.GetParent(Application.dataPath).FullName;
            Assert.AreEqual(
                project,
                Environment.GetEnvironmentVariable("UNITY_MCP_PLAY02_RELOAD_PROJECT"),
                "Only QA may opt its disposable project into an actual reload."
            );
            Assert.IsFalse(TestJobManager.HasRunningJob, "A reload probe must not replace a live MCP job.");
            SessionState.SetString(Probe + "jobs", SessionState.GetString(JobsKey, ""));
            SessionState.SetString(Probe + "current", SessionState.GetString(CurrentKey, ""));
            SessionState.SetString(Probe + "root", temporary);
            SessionState.SetString(Probe + "before", ReloadNonce);
            SessionState.SetString(Probe + "witness", "");
            SessionState.SetBool(Probe + "active", true);
            var manager = typeof(TestJobManager);
            manager.GetField("ResultStoreOverride", ManagerFlags).SetValue(null, NewStore(temporary));
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", ManagerFlags).GetValue(null);
            jobs.Clear();
            jobs.Add(
                JobId,
                new TestJob
                {
                    JobId = JobId,
                    Mode = "EditMode",
                    Status = TestJobStatus.Running,
                }
            );
            manager.GetField("_currentJobId", ManagerFlags).SetValue(null, JobId);
            TestJobManager.FinalizeCurrentJobFromRunFinished(Result());
            AssemblyReloadEvents.beforeAssemblyReload += ObserveReload;
        }

        private static void VerifyReloadProbe()
        {
            Assert.AreNotEqual(SessionState.GetString(Probe + "before", ""), ReloadNonce, "A managed static reset is insufficient evidence.");
            Assert.AreEqual(SessionState.GetString(Probe + "before", ""), SessionState.GetString(Probe + "witness", "missing"));
            typeof(TestJobManager).GetField("ResultStoreOverride", ManagerFlags).SetValue(null, NewStore(SessionState.GetString(Probe + "root", "")));
            JObject page = JObject.FromObject(
                MCPForUnity.Editor.Tools.GetTestJob.HandleCommand(
                    new JObject
                    {
                        ["job_id"] = JobId,
                        ["includeDetails"] = true,
                        ["page_size"] = 73,
                    }
                )
            );
            Assert.IsTrue((bool)page["success"]);
            Assert.AreEqual(1000, (int)page["data"]["result_page"]["total_count"]);
            Assert.AreEqual(73, ((JArray)page["data"]["result_page"]["items"]).Count);
            Assert.AreEqual(100, (int)page["data"]["result"]["summary"]["failed"]);
            Assert.AreEqual("stack<&>", (string)page["data"]["result_page"]["items"][0]["stackTrace"]);
            StringAssert.Contains("한국어", (string)page["data"]["result_page"]["items"][0]["message"]);
            JObject exported = JObject.FromObject(
                MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(new JObject { ["job_id"] = JobId, ["format"] = "junit" })
            );
            Assert.IsTrue((bool)exported["success"]);
            StringAssert.Contains("testsuite", (string)exported["data"]["content"]);
        }

        private static void CleanupReloadProbe()
        {
            if (!SessionState.GetBool(Probe + "active", false))
                return;
            AssemblyReloadEvents.beforeAssemblyReload -= ObserveReload;
            var manager = typeof(TestJobManager);
            manager.GetField("ResultStoreOverride", ManagerFlags).SetValue(null, null);
            SessionState.SetString(JobsKey, SessionState.GetString(Probe + "jobs", ""));
            SessionState.SetString(CurrentKey, SessionState.GetString(Probe + "current", ""));
            ((Dictionary<string, TestJob>)manager.GetField("Jobs", ManagerFlags).GetValue(null)).Clear();
            manager.GetMethod("TryRestoreFromSessionState", ManagerFlags).Invoke(null, null);
            string temporary = SessionState.GetString(Probe + "root", "");
            if (Directory.Exists(temporary))
                Directory.Delete(temporary, true);
            foreach (string name in new[] { "jobs", "current", "root", "before", "witness" })
                SessionState.EraseString(Probe + name);
            SessionState.EraseBool(Probe + "active");
        }

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "mcp-test-results-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        internal static object NewStore(string path)
        {
            Type type = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.TestJobResultStore");
            Assert.NotNull(type, "A bounded test-result store must exist before completed details can survive reload.");
            return Activator.CreateInstance(type, new object[] { path, SessionId });
        }

        internal static object Call(object target, string method, params object[] args)
        {
            var member = target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.NotNull(member, method);
            try
            {
                return member.Invoke(target, args);
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException;
            }
        }

        internal static TestRunResult Result(int count = 1000, string message = "failed < & > \" ' 한국어\nnext")
        {
            var rows = Enumerable
                .Range(0, count)
                .Select(i => new TestRunTestResult(
                    "Test" + i,
                    "Fixture.Test" + i,
                    i % 10 == 0 ? "Failed" : "Passed",
                    0.01,
                    i % 10 == 0 ? message : null,
                    "stack<&>",
                    "output<&>\n"
                ))
                .ToList();
            return new TestRunResult(new TestRunSummary(count, count - count / 10, count / 10, 0, 10.0, "Failed"), rows);
        }

        private JObject Save(TestRunResult value = null)
        {
            store = NewStore(root);
            return (JObject)Call(store, "Save", JobId, "EditMode", value ?? Result(), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        }

        [TestCase("unmarked")]
        [TestCase("invalid_marker")]
        [TestCase("unknown_file")]
        public void CleanupPreservesUnmarkedInvalidOrUnknownGeneration(string kind)
        {
            string generation = Guid.NewGuid().ToString("N");
            string directory = Path.Combine(root, "Library", "MCPForUnity", "TestJobResults", generation);
            Directory.CreateDirectory(directory);
            File.WriteAllText(Path.Combine(directory, "keep.txt"), "user-owned bytes");
            if (kind != "unmarked")
                File.WriteAllText(
                    Path.Combine(directory, ".owner.json"),
                    kind == "invalid_marker"
                        ? "{broken}"
                        : new JObject
                        {
                            ["version"] = 1,
                            ["session"] = SessionId,
                            ["job_id"] = JobId,
                            ["generation"] = generation,
                        }.ToString()
                );
            NewStore(root);
            Assert.AreEqual("user-owned bytes", File.ReadAllText(Path.Combine(directory, "keep.txt")));
        }

        [Test]
        public void JobQuotaIncludesManifestAndOwnershipBytes()
        {
            store = NewStore(root);
            var row = new TestRunTestResult("Test", "Fixture.Test", "Passed", 0.01, new string('x', 12000), new string('x', 12000), new string('x', 12000));
            var empty = new TestRunTestResult("Test", "Fixture.Test", "Passed", 0.01, "", "", "");
            var encode = store.GetType().GetMethod("Row", BindingFlags.Static | BindingFlags.NonPublic);
            int size = System.Text.Encoding.UTF8.GetByteCount(
                ((JObject)encode.Invoke(null, new object[] { row })).ToString(Newtonsoft.Json.Formatting.None) + "\n"
            );
            int baseSize = System.Text.Encoding.UTF8.GetByteCount(
                ((JObject)encode.Invoke(null, new object[] { empty })).ToString(Newtonsoft.Json.Formatting.None) + "\n"
            );
            int count = (32 * 1024 * 1024 - 512) / size;
            int remaining = 32 * 1024 * 1024 - 512 - count * size;
            var rows = Enumerable.Repeat(row, count).ToList();
            if (remaining >= baseSize)
            {
                int padding = remaining - baseSize;
                string message = new string('x', Math.Min(12000, padding));
                padding -= message.Length;
                string stack = new string('x', Math.Min(12000, padding));
                padding -= stack.Length;
                rows.Add(new TestRunTestResult("Test", "Fixture.Test", "Passed", 0.01, message, stack, new string('x', padding)));
            }
            var result = new TestRunResult(new TestRunSummary(rows.Count, rows.Count, 0, 0, 0, "Passed"), rows);
            Assert.Throws<InvalidDataException>(() => Call(store, "Save", JobId, "EditMode", result, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()));
            Assert.IsEmpty(Directory.GetFiles(root, "manifest.json", SearchOption.AllDirectories));
        }

        [TestCase("corrupt_row")]
        [TestCase("corrupt_artifact")]
        [TestCase("newer_version")]
        [TestCase("oversized")]
        [TestCase("deep")]
        [TestCase("duplicate")]
        [TestCase("legacy")]
        public void RecoveryBoundsAndIsolatesOptionalDetailCorruption(string kind)
        {
            var manager = typeof(TestJobManager);
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", ManagerFlags).GetValue(null);
            var backup = new Dictionary<string, TestJob>(jobs);
            object current = manager.GetField("_currentJobId", ManagerFlags).GetValue(null);
            string oldJobs = SessionState.GetString(JobsKey, ""),
                oldCurrent = SessionState.GetString(CurrentKey, "");
            JObject Row(string id) =>
                new JObject
                {
                    ["job_id"] = id,
                    ["status"] = "failed",
                    ["mode"] = "EditMode",
                    ["started_unix_ms"] = 1,
                    ["finished_unix_ms"] = 2,
                    ["last_update_unix_ms"] = 2,
                    ["completed_tests"] = 10,
                    ["summary"] = JObject.FromObject(new TestRunSummary(10, 9, 1, 0, 1.25, "Failed")),
                    ["completion_kind"] = "completed",
                };
            JObject first = Row("first"),
                middle = Row("middle"),
                last = Row("last");
            if (kind == "corrupt_row")
                middle["completed_tests"] = "bad";
            if (kind == "corrupt_artifact")
                middle["artifact"] = "invalid optional detail descriptor";
            var envelope = new JObject { ["version"] = kind == "newer_version" ? 999 : 2, ["jobs"] = new JArray(first, middle, last) };
            if (kind == "oversized")
                envelope["padding"] = new string('x', 49 * 1024);
            string encoded = envelope.ToString(Newtonsoft.Json.Formatting.None);
            if (kind == "legacy")
            {
                envelope.Remove("version");
                encoded = envelope.ToString(Newtonsoft.Json.Formatting.None);
            }
            if (kind == "duplicate")
                encoded = encoded.Replace("\"version\":2", "\"version\":2,\"version\":2");
            if (kind == "deep")
                encoded = encoded.Substring(0, encoded.Length - 1) + ",\"padding\":" + new string('[', 40) + "0" + new string(']', 40) + "}";
            try
            {
                jobs.Clear();
                manager.GetField("_currentJobId", ManagerFlags).SetValue(null, null);
                SessionState.SetString(JobsKey, encoded);
                manager.GetMethod("TryRestoreFromSessionState", ManagerFlags).Invoke(null, null);
                if (kind == "oversized" || kind == "newer_version" || kind == "duplicate" || kind == "deep")
                    Assert.IsEmpty(jobs);
                else
                {
                    Assert.IsTrue(jobs.ContainsKey("first"));
                    Assert.IsTrue(jobs.ContainsKey("last"));
                    if (kind == "corrupt_artifact")
                    {
                        Assert.AreEqual(TestJobStatus.Failed, jobs["middle"].Status);
                        Assert.AreEqual(1, jobs["middle"].RetainedSummary.Failed);
                        Assert.IsNull(jobs["middle"].ResultArtifact);
                        Assert.AreEqual("corrupt", jobs["middle"].ResultAvailability);
                    }
                }
            }
            finally
            {
                jobs.Clear();
                foreach (var item in backup)
                    jobs.Add(item.Key, item.Value);
                manager.GetField("_currentJobId", ManagerFlags).SetValue(null, current);
                SessionState.SetString(JobsKey, oldJobs);
                SessionState.SetString(CurrentKey, oldCurrent);
            }
        }

        [Test]
        public void EleventhCompletionEvictsOldestArtifact()
        {
            store = NewStore(root);
            JObject first = null;
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            for (int index = 1; index <= 11; index++)
            {
                JObject saved = (JObject)Call(store, "Save", index.ToString("x32"), "EditMode", Result(10), now - 100 + index);
                if (index == 1)
                    first = saved;
            }
            Assert.Throws<FileNotFoundException>(() => Call(store, "ReadPage", 1.ToString("x32"), first, 10, null, false));
            Assert.AreEqual(10, Directory.GetFiles(root, "manifest.json", SearchOption.AllDirectories).Length);
        }

        [Test]
        public void ExpiredArtifactIsUnavailableWithoutRefreshingOnRead()
        {
            JObject descriptor = Save();
            store
                .GetType()
                .GetProperty("Clock", BindingFlags.Instance | BindingFlags.NonPublic)
                .SetValue(store, new Func<long>(() => (long)descriptor["completed_unix_ms"] + 86400001));
            Assert.Throws<FileNotFoundException>(() => Call(store, "ReadPage", JobId, descriptor, 10, null, false));
        }

        [Test]
        public void LeafQuotaFailurePublishesNoPartialManifest()
        {
            store = NewStore(root);
            Assert.Throws<InvalidDataException>(() => Call(store, "Save", JobId, "EditMode", Result(10001), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()));
            Assert.IsEmpty(Directory.GetFiles(root, "manifest.json", SearchOption.AllDirectories));
        }

        [Test]
        public void ThousandResults_PageAcrossFreshStoreWithoutLossOrWholeResultRead()
        {
            JObject descriptor = Save();
            store = NewStore(root);
            var names = new List<string>();
            string cursor = null;
            do
            {
                JObject page = (JObject)Call(store, "ReadPage", JobId, descriptor, 73, cursor, false);
                Assert.AreEqual(1000, (int)page["total_count"]);
                Assert.LessOrEqual(((JArray)page["items"]).Count, 73);
                names.AddRange(page["items"].Select(item => (string)item["fullName"]));
                cursor = (string)page["next_cursor"];
                Assert.AreEqual(cursor != null, (bool)page["has_more"]);
            } while (cursor != null);
            CollectionAssert.AreEqual(Enumerable.Range(0, 1000).Select(i => "Fixture.Test" + i), names);
            Assert.LessOrEqual((long)store.GetType().GetProperty("MaximumReadBytes").GetValue(store), 1024 * 1024);
        }

        [Test]
        public void FailedOnlyPagesKeepStackAndOutput()
        {
            JObject descriptor = Save();
            JObject page = (JObject)Call(store, "ReadPage", JobId, descriptor, 100, null, true);
            Assert.AreEqual(100, (int)page["total_count"]);
            Assert.AreEqual(100, ((JArray)page["items"]).Count);
            Assert.AreEqual("stack<&>", (string)page["items"][0]["stackTrace"]);
            Assert.AreEqual("output<&>\n", (string)page["items"][0]["output"]);
        }

        [Test]
        public void CorruptShardDoesNotReturnAnApparentlyValidPage()
        {
            JObject descriptor = Save();
            string shard = Directory.GetFiles(root, "*.jsonl", SearchOption.AllDirectories).First();
            File.WriteAllText(shard, "{broken}");
            Assert.Throws<InvalidDataException>(() => Call(store, "ReadPage", JobId, descriptor, 100, null, false));
        }

        [Test]
        public void CursorCannotChangeSelectionOrJob()
        {
            JObject descriptor = Save();
            JObject page = (JObject)Call(store, "ReadPage", JobId, descriptor, 10, null, false);
            Assert.Throws<ArgumentException>(() => Call(store, "ReadPage", JobId, descriptor, 10, (string)page["next_cursor"], true));
            Assert.Throws<ArgumentException>(() => Call(store, "ReadPage", "../../escape", descriptor, 10, null, false));
        }

        [Test]
        public void LongOptionalFieldsAreExplicitlyTruncatedAndByteBounded()
        {
            JObject descriptor = Save(Result(10, new string('한', 100000)));
            JObject page = (JObject)Call(store, "ReadPage", JobId, descriptor, 100, null, false);
            Assert.IsNotEmpty((JArray)page["items"][0]["truncated_fields"]);
            Assert.AreEqual(1, (int)descriptor["truncated_result_count"]);
            Assert.Less(System.Text.Encoding.UTF8.GetByteCount(page.ToString()), 512 * 1024);
        }

        [Test]
        public void FlatLayoutBindsLogicalJobAndSessionWithoutDeepPhysicalPaths()
        {
            JObject descriptor = Save(Result(10));
            Assert.AreEqual(JobId, (string)descriptor["job_id"]);
            Assert.AreEqual(SessionId, (string)descriptor["session"]);
            string manifest = Directory.GetFiles(root, "manifest.json", SearchOption.AllDirectories).Single();
            string expected = Path.Combine(root, "Library", "MCPForUnity", "TestJobResults", (string)descriptor["generation"], "manifest.json");
            Assert.AreEqual(expected, manifest);
            Assert.Less(manifest.Length, root.Length + 90);
            Console.WriteLine("PLAY02 path proof root=" + root.Length + " manifest=" + manifest.Length + " old_nested_manifest=" + (manifest.Length + 66));
            object foreign = Activator.CreateInstance(store.GetType(), new object[] { root, "fedcba0987654321fedcba0987654321" });
            Assert.Throws<ArgumentException>(() => Call(foreign, "ReadPage", JobId, descriptor, 10, null, false));
            Assert.Throws<ArgumentException>(() => Call(store, "ReadPage", "fedcba0987654321fedcba0987654321", descriptor, 10, null, false));
        }

        [TestCase(10.0)]
        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(-1.0)]
        public void CompletionSummarySurvivesManagerPersistenceWithoutResultObject(double duration)
        {
            JObject descriptor = Save();
            var manager = typeof(TestJobManager);
            var flags = BindingFlags.NonPublic | BindingFlags.Static;
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", flags).GetValue(null);
            var backup = new Dictionary<string, TestJob>(jobs);
            string oldJobs = SessionState.GetString("MCPForUnity.TestJobsV1", "");
            string oldCurrent = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", "");
            object oldStore = null;
            var storeField = manager.GetField("ResultStoreOverride", flags);
            try
            {
                Assert.NotNull(storeField, "Persistence tests must inject a temp-root store before finalization.");
                oldStore = storeField.GetValue(null);
                storeField.SetValue(null, store);
                var job = new TestJob
                {
                    JobId = JobId,
                    Mode = "EditMode",
                    Status = TestJobStatus.Running,
                };
                jobs.Clear();
                jobs.Add(JobId, job);
                var current = manager.GetField("_currentJobId", flags);
                object old = current.GetValue(null);
                try
                {
                    current.SetValue(null, JobId);
                    TestRunResult original = Result();
                    TestJobManager.FinalizeCurrentJobFromRunFinished(
                        new TestRunResult(new TestRunSummary(1000, 900, 100, 0, duration, "Failed"), original.Results)
                    );
                    jobs.Clear();
                    manager.GetMethod("TryRestoreFromSessionState", flags).Invoke(null, null);
                    Assert.NotNull(jobs[JobId].RetainedSummary);
                    if (double.IsNaN(duration))
                        Assert.IsTrue(double.IsNaN(jobs[JobId].RetainedSummary.DurationSeconds));
                    else
                        Assert.AreEqual(duration, jobs[JobId].RetainedSummary.DurationSeconds);
                    JObject payload = JObject.FromObject(TestJobManager.ToSerializable(jobs[JobId], false, true));
                    Assert.AreEqual(100, (int)payload["result"]["summary"]["failed"]);
                    Assert.AreEqual("stack<&>", (string)payload["result"]["results"][0]["stackTrace"]);
                    Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(SessionState.GetString("MCPForUnity.TestJobsV1", "")), 48 * 1024);
                    File.WriteAllText(
                        Directory
                            .GetFiles(root, "manifest.json", SearchOption.AllDirectories)
                            .Single(path => new DirectoryInfo(Path.GetDirectoryName(path)).Name == (string)jobs[JobId].ResultArtifact["generation"]),
                        "{corrupt}"
                    );
                    payload = JObject.FromObject(TestJobManager.ToSerializable(jobs[JobId], false, true));
                    Assert.AreEqual(100, (int)payload["result"]["summary"]["failed"]);
                    Assert.AreEqual("corrupt", (string)payload["result_availability"]);
                }
                finally
                {
                    current.SetValue(null, old);
                }
            }
            finally
            {
                if (storeField != null)
                    storeField.SetValue(null, oldStore);
                jobs.Clear();
                foreach (var item in backup)
                    jobs.Add(item.Key, item.Value);
                SessionState.SetString("MCPForUnity.TestJobsV1", oldJobs);
                SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", oldCurrent);
            }
        }
    }
}
