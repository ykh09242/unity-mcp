using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Xml;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class TestJobResultExportTests
    {
        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        public void PublicExportOfUnfinishedJobPreservesLifecycleAndPendingAdmission(bool pendingAdmission, bool contradictoryArtifact)
        {
            const BindingFlags privateStatic = BindingFlags.NonPublic | BindingFlags.Static;
            const string jobsKey = "MCPForUnity.TestJobsV1";
            const string currentKey = "MCPForUnity.CurrentTestJobIdV1";
            var manager = typeof(TestJobManager);
            var runner = typeof(TestRunnerService);
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", privateStatic).GetValue(null);
            var originalJobs = new Dictionary<string, TestJob>(jobs);
            object originalCurrent = manager.GetField("_currentJobId", privateStatic).GetValue(null);
            object originalPersist = manager.GetField("_lastPersistUnixMs", privateStatic).GetValue(null);
            object originalStore = manager.GetField("ResultStoreOverride", privateStatic).GetValue(null);
            object originalOwner = runner.GetField("_activeRunOwner", privateStatic).GetValue(null);
            var originalStatus = typeof(TestRunStatus)
                .GetFields(privateStatic)
                .Where(field => !field.IsInitOnly)
                .ToDictionary(field => field, field => field.GetValue(null));
            string originalSessionJobs = SessionState.GetString(jobsKey, "");
            string originalSessionCurrent = SessionState.GetString(currentKey, "");
            string root = Path.Combine(Path.GetTempPath(), "mcp-readonly-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            object admission = null;
            object admissions = runner.GetField("PendingRunAdmissions", privateStatic).GetValue(null);
            using var cancellation = new CancellationTokenSource();
            try
            {
                // The regression exercises real pending cancellation, without giving RED a
                // native run owner whose retirement could restore global Editor preferences.
                runner.GetField("_activeRunOwner", privateStatic).SetValue(null, null);
                object store = TestJobResultStoreTests.NewStore(root);
                manager.GetField("ResultStoreOverride", privateStatic).SetValue(null, store);
                long started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 60_000;
                var job = new TestJob
                {
                    JobId = TestJobResultStoreTests.JobId,
                    Status = TestJobStatus.Running,
                    Mode = "EditMode",
                    StartedUnixMs = started,
                    LastUpdateUnixMs = started,
                    InitTimeoutMs = 1,
                    TotalTests = contradictoryArtifact ? 10 : null,
                };
                if (contradictoryArtifact)
                {
                    job.CompletionKind = "completed";
                    job.ResultArtifact = (JObject)TestJobResultStoreTests.Call(store, "Save", job.JobId, job.Mode, TestJobResultStoreTests.Result(10), started);
                }
                jobs.Clear();
                jobs.Add(job.JobId, job);
                manager.GetField("_currentJobId", privateStatic).SetValue(null, job.JobId);
                TestRunStatus.MarkStarted(UnityEditor.TestTools.TestRunner.Api.TestMode.EditMode);
                long? runningSince = TestRunStatus.StartedUnixMs;
                if (pendingAdmission)
                {
                    Type type = runner.GetNestedType("PendingRunAdmission", BindingFlags.NonPublic);
                    admission = Activator.CreateInstance(type, nonPublic: true);
                    type.GetField("JobId", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(admission, job.JobId);
                    type.GetField("Cancellation", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(admission, cancellation);
                    admissions.GetType().GetMethod("Add").Invoke(admissions, new[] { admission });
                }
                string before = JObject.FromObject(job).ToString();
                string[] filesBefore = Directory.GetFiles(root, "*", SearchOption.AllDirectories);

                JObject response = JObject.FromObject(
                    MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(new JObject { ["job_id"] = job.JobId, ["format"] = "json" })
                );

                Assert.IsFalse((bool)response["success"], "Only a terminal, completed job can be exported.");
                Assert.AreEqual("Test result details are unavailable.", (string)response["error"]);
                Assert.AreEqual(before, JObject.FromObject(job).ToString(), "Export must not auto-fail an expired running job or repair unfinished metadata.");
                Assert.AreEqual(job.JobId, TestJobManager.CurrentJobId);
                Assert.IsTrue(TestRunStatus.IsRunning);
                Assert.AreEqual(runningSince, TestRunStatus.StartedUnixMs);
                Assert.IsNull(TestRunStatus.FinishedUnixMs);
                Assert.IsFalse(cancellation.IsCancellationRequested, "An export query must not retire a pending test admission.");
                Assert.AreEqual(originalSessionJobs, SessionState.GetString(jobsKey, ""));
                Assert.AreEqual(originalSessionCurrent, SessionState.GetString(currentKey, ""));
                Assert.AreEqual(originalPersist, manager.GetField("_lastPersistUnixMs", privateStatic).GetValue(null));
                CollectionAssert.AreEquivalent(
                    filesBefore,
                    Directory.GetFiles(root, "*", SearchOption.AllDirectories),
                    "A refused unfinished export must not create cache artifacts."
                );
            }
            finally
            {
                if (admission != null)
                    admissions.GetType().GetMethod("Remove").Invoke(admissions, new[] { admission });
                runner.GetField("_activeRunOwner", privateStatic).SetValue(null, originalOwner);
                manager.GetField("ResultStoreOverride", privateStatic).SetValue(null, originalStore);
                manager.GetField("_currentJobId", privateStatic).SetValue(null, originalCurrent);
                manager.GetField("_lastPersistUnixMs", privateStatic).SetValue(null, originalPersist);
                jobs.Clear();
                foreach (var entry in originalJobs)
                    jobs.Add(entry.Key, entry.Value);
                foreach (var entry in originalStatus)
                    entry.Key.SetValue(null, entry.Value);
                SessionState.SetString(jobsKey, originalSessionJobs);
                SessionState.SetString(currentKey, originalSessionCurrent);
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void ReplacedCachedExportCannotAcquireANewTrustedDigestAfterRebind()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                object store = TestJobResultStoreTests.NewStore(root);
                JObject descriptor = (JObject)
                    TestJobResultStoreTests.Call(
                        store,
                        "Save",
                        TestJobResultStoreTests.JobId,
                        "EditMode",
                        TestJobResultStoreTests.Result(10),
                        DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    );
                Type exporter = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.TestJobResultExport");
                exporter.GetMethod("Read").Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, descriptor, "json", null });
                JObject restored = JObject.Parse(descriptor.ToString());
                File.WriteAllText(Directory.GetFiles(root, "export.json", SearchOption.AllDirectories)[0], "{\"replaced\":true}");
                store = TestJobResultStoreTests.NewStore(root);
                var error = Assert.Throws<TargetInvocationException>(() =>
                    exporter.GetMethod("Read").Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, restored, "json", null })
                );
                Assert.IsInstanceOf<InvalidDataException>(error.InnerException);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase("clean")]
        [TestCase("contradictory")]
        [TestCase("malformed")]
        [TestCase("persistence_retry")]
        public void PublicExportDigestSurvivesSessionStateRestoreAndRefusesReplacement(string attribution)
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var manager = typeof(TestJobManager);
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", flags).GetValue(null);
            var backup = new Dictionary<string, TestJob>(jobs);
            object oldStore = manager.GetField("ResultStoreOverride", flags).GetValue(null);
            object oldCurrent = manager.GetField("_currentJobId", flags).GetValue(null);
            var persistTime = manager.GetField("_lastPersistUnixMs", flags);
            object oldPersist = persistTime.GetValue(null);
            string oldJobs = SessionState.GetString("MCPForUnity.TestJobsV1", ""),
                oldKey = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", "");
            try
            {
                manager.GetField("ResultStoreOverride", flags).SetValue(null, TestJobResultStoreTests.NewStore(root));
                jobs.Clear();
                jobs.Add(
                    TestJobResultStoreTests.JobId,
                    new TestJob
                    {
                        JobId = TestJobResultStoreTests.JobId,
                        Status = TestJobStatus.Running,
                        Mode = "EditMode",
                    }
                );
                manager.GetField("_currentJobId", flags).SetValue(null, TestJobResultStoreTests.JobId);
                TestJobManager.FinalizeCurrentJobFromRunFinished(TestJobResultStoreTests.Result(10));
                if (attribution == "malformed" || attribution == "contradictory")
                {
                    JObject saved = JObject.Parse(SessionState.GetString("MCPForUnity.TestJobsV1", ""));
                    JToken artifact = saved["jobs"][0]["artifact"];
                    artifact["status"] = attribution == "contradictory" ? new JValue("succeeded") : new JObject { ["bad"] = true };
                    artifact["started_unix_ms"] = attribution == "contradictory" ? new JValue(999999) : new JValue("unknown");
                    artifact["finished_unix_ms"] = attribution == "contradictory" ? new JValue(1) : new JArray("bad");
                    SessionState.SetString("MCPForUnity.TestJobsV1", saved.ToString(Newtonsoft.Json.Formatting.None));
                    jobs.Clear();
                    manager.GetMethod("TryRestoreFromSessionState", flags).Invoke(null, null);
                }
                var parameters = new JObject { ["job_id"] = TestJobResultStoreTests.JobId, ["format"] = "json" };
                if (attribution == "persistence_retry")
                {
                    var job = jobs[TestJobResultStoreTests.JobId];
                    string availability = job.ResultAvailability;
                    job.ResultAvailability = new string('x', 4097);
                    try
                    {
                        for (int attempt = 0; attempt < 2; attempt++)
                        {
                            JObject result = JObject.FromObject(MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(parameters));
                            Assert.IsFalse((bool)result["success"]);
                            Assert.AreEqual("Test result export metadata could not be retained.", (string)result["error"]);
                        }
                        Assert.IsNull(JObject.Parse(SessionState.GetString("MCPForUnity.TestJobsV1", ""))["jobs"][0]["artifact"]["exports"]);
                    }
                    finally
                    {
                        job.ResultAvailability = availability;
                    }
                }
                JObject first = JObject.FromObject(MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(parameters));
                Assert.IsTrue((bool)first["success"]);
                JObject exported = JObject.Parse((string)first["data"]["content"]);
                Assert.AreEqual("failed", (string)exported["status"]);
                Assert.AreEqual(jobs[TestJobResultStoreTests.JobId].StartedUnixMs, (long)exported["started_unix_ms"]);
                Assert.AreEqual(jobs[TestJobResultStoreTests.JobId].FinishedUnixMs, (long?)exported["finished_unix_ms"]);
                Assert.AreEqual(1, (int)exported["summary"]["failed"]);
                persistTime.SetValue(null, 0L);
                JObject repeated = JObject.FromObject(MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(parameters));
                Assert.IsTrue((bool)repeated["success"]);
                Assert.AreEqual(0L, persistTime.GetValue(null), "An unchanged digest must not rewrite session history.");
                jobs.Clear();
                manager.GetField("ResultStoreOverride", flags).SetValue(null, TestJobResultStoreTests.NewStore(root));
                manager.GetMethod("TryRestoreFromSessionState", flags).Invoke(null, null);
                Assert.AreEqual((string)first["data"]["sha256"], (string)jobs[TestJobResultStoreTests.JobId].ResultArtifact["exports"]["json"]["sha256"]);
                File.WriteAllText(Directory.GetFiles(root, "export.json", SearchOption.AllDirectories)[0], "{\"replaced\":true}");
                JObject refused = JObject.FromObject(MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(parameters));
                Assert.IsFalse((bool)refused["success"]);
                Assert.AreEqual(TestJobStatus.Failed, jobs[TestJobResultStoreTests.JobId].Status);
                Assert.AreEqual(1, jobs[TestJobResultStoreTests.JobId].RetainedSummary.Failed);
            }
            finally
            {
                manager.GetField("ResultStoreOverride", flags).SetValue(null, oldStore);
                manager.GetField("_currentJobId", flags).SetValue(null, oldCurrent);
                persistTime.SetValue(null, oldPersist);
                jobs.Clear();
                foreach (var entry in backup)
                    jobs.Add(entry.Key, entry.Value);
                SessionState.SetString("MCPForUnity.TestJobsV1", oldJobs);
                SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", oldKey);
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void LegacyDigestAuthenticatesUtf8PagesAndRejectsUnrequestedBlockOnColdBind()
        {
            const int chunkBytes = 65536;
            const string job = TestJobResultStoreTests.JobId;
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var store = new TestJobResultStore(root, TestJobResultStoreTests.SessionId);
                JObject descriptor = store.Save(job, "EditMode", TestJobResultStoreTests.Result(10), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                // A legacy SessionState descriptor contains only the original whole digest and byte length.
                string source = new string('a', chunkBytes - 1) + "💠" + new string('b', 7 * chunkBytes);
                byte[] original = Encoding.UTF8.GetBytes(source);
                string digest = TestJobResultStore.Hash(original);
                descriptor["exports"] = new JObject
                {
                    ["json"] = new JObject
                    {
                        ["sha256"] = digest,
                        ["source_sha256"] = descriptor["sha256"],
                        ["total_bytes"] = original.Length,
                    },
                };
                string path = store.ExportPath(job, descriptor, "json");
                File.WriteAllBytes(path, original);
                JObject Read(string cursor) => TestJobResultExport.Read(store, job, descriptor, "json", cursor);
                JObject first = Read(null);
                string next = (string)first["next_cursor"];
                Assert.AreEqual(new string('a', chunkBytes - 1), (string)first["content"]);
                StringAssert.EndsWith(":" + (chunkBytes - 1), next);
                Assert.AreEqual(2L * original.Length + chunkBytes, store.ExportHashBytes, "One binding pass feeds the whole and block digests.");

                // Replace a same-length file, leaving its first block and timestamp unchanged.
                byte[] changed = (byte[])original.Clone();
                changed[chunkBytes + 10] ^= 1;
                DateTime timestamp = File.GetLastWriteTimeUtc(path);
                File.Delete(path);
                File.WriteAllBytes(path, changed);
                File.SetLastWriteTimeUtc(path, timestamp);
                Assert.IsTrue(JToken.DeepEquals(first, Read(null)), "Warm reads authenticate only blocks overlapping their output.");
                Assert.Throws<InvalidDataException>(() => Read(next), "No changed bytes may escape a warm chunk.");
                store = new TestJobResultStore(root, TestJobResultStoreTests.SessionId);
                descriptor = JObject.Parse(descriptor.ToString());
                Assert.Throws<InvalidDataException>(() => Read(null), "A new binding rejects changes anywhere before serving the first chunk.");

                File.WriteAllBytes(path, original);
                Assert.IsTrue(JToken.DeepEquals(first, Read(null)));
                JObject second = Read(next);
                StringAssert.StartsWith("💠", (string)second["content"]);
                Assert.IsTrue(JToken.DeepEquals(second, Read(next)), "Retrying a non-aligned byte cursor must be idempotent.");
                long before = store.ExportHashBytes;
                string cursor = null;
                var content = new StringBuilder();
                do
                {
                    long prior = store.ExportHashBytes;
                    JObject chunk = Read(cursor);
                    Assert.LessOrEqual(store.ExportHashBytes - prior, 81920, "Hash at most five 16 KiB blocks per warm request.");
                    Assert.AreEqual(digest, (string)chunk["sha256"]);
                    content.Append((string)chunk["content"]);
                    cursor = (string)chunk["next_cursor"];
                } while (cursor != null);
                Assert.LessOrEqual(store.ExportHashBytes - before, 1.25 * original.Length + 16384);
                Assert.AreEqual(source, content.ToString());
                Assert.AreEqual(digest, TestJobResultStore.Hash(Encoding.UTF8.GetBytes(content.ToString())));
                descriptor["exports"]["json"]["source_sha256"] = new string('0', 64);
                Assert.Throws<InvalidDataException>(() => Read(next), "Warm reads must still authenticate the source descriptor.");
                store = new TestJobResultStore(root, TestJobResultStoreTests.SessionId);
                Assert.Throws<InvalidDataException>(() => Read(null), "A mismatched source digest must not authenticate a cold block index.");
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase("json")]
        [TestCase("junit")]
        public void NearCapExportRefusesBeforeTemporaryWritesExceedRemainingBytes(string format)
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                object store = TestJobResultStoreTests.NewStore(root);
                JObject descriptor = (JObject)
                    TestJobResultStoreTests.Call(
                        store,
                        "Save",
                        TestJobResultStoreTests.JobId,
                        "EditMode",
                        TestJobResultStoreTests.Result(),
                        DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    );
                string directory = Path.GetDirectoryName(Directory.GetFiles(root, "manifest.json", SearchOption.AllDirectories)[0]);
                long existing = Directory.GetFiles(directory).Sum(file => new FileInfo(file).Length);
                const long remaining = 32768;
                string reserved = Path.Combine(directory, format == "json" ? "export.junit" : "export.json");
                using (var file = new FileStream(reserved, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    file.SetLength(32 * 1024 * 1024 - existing - remaining);
                Type exporter = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.TestJobResultExport");
                var error = Assert.Throws<TargetInvocationException>(() =>
                    exporter.GetMethod("Read").Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, descriptor, format, null })
                );
                Assert.IsInstanceOf<InvalidDataException>(error.InnerException);
                StringAssert.Contains(
                    "BoundedWrite.Write",
                    error.InnerException.StackTrace,
                    "Admission must stop the real writer, not reject only after the temporary export was generated."
                );
                long highWater = (long)store.GetType().GetProperty("MaximumExportTemporaryBytes").GetValue(store);
                Assert.Greater(highWater, 0, "Observe actual temporary FileStream writes before refusal.");
                Assert.LessOrEqual(highWater, remaining);
                Assert.IsFalse(File.Exists(Path.Combine(directory, "export." + format)));
                Assert.IsEmpty(Directory.GetFiles(directory, "*.tmp"));
                Assert.LessOrEqual(Directory.GetFiles(directory).Sum(file => new FileInfo(file).Length), 32 * 1024 * 1024);
                JObject page = (JObject)TestJobResultStoreTests.Call(store, "ReadPage", TestJobResultStoreTests.JobId, descriptor, 1, null, false);
                Assert.AreEqual(1000, (int)page["total_count"]);
                Assert.IsNull(descriptor["exports"], "A refused export must not publish trusted digest metadata.");
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(-1.0)]
        public void JUnitInvalidDurationsAreExplicitlySanitized(double duration)
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                object store = TestJobResultStoreTests.NewStore(root);
                var result = new TestRunResult(
                    new TestRunSummary(1, 1, 0, 0, duration, "Passed"),
                    new[] { new TestRunTestResult("Test", "Fixture.Test", "Passed", duration, null, null, null) }
                );
                JObject descriptor = (JObject)
                    TestJobResultStoreTests.Call(
                        store,
                        "Save",
                        TestJobResultStoreTests.JobId,
                        "EditMode",
                        result,
                        DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    );
                Type exporter = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.TestJobResultExport");
                JObject chunk = (JObject)
                    exporter.GetMethod("Read").Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, descriptor, "junit", null });
                var document = new XmlDocument { XmlResolver = null };
                document.LoadXml((string)chunk["content"]);
                Assert.AreEqual("0", document.DocumentElement.GetAttribute("time"));
                Assert.AreEqual("0", ((XmlElement)document.SelectSingleNode("//testcase")).GetAttribute("time"));
                Assert.AreEqual("true", ((XmlElement)document.SelectSingleNode("//property[@name='xml_duration_sanitized']")).GetAttribute("value"));
                Assert.AreEqual(1, (int)descriptor["truncated_result_count"]);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase("json")]
        [TestCase("junit")]
        public void ExportChunks_RoundTripUnicodeMarkupAndFailureEvidence(string format)
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-test-export-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                object store = TestJobResultStoreTests.NewStore(root);
                JObject descriptor = (JObject)
                    TestJobResultStoreTests.Call(
                        store,
                        "Save",
                        TestJobResultStoreTests.JobId,
                        "EditMode",
                        TestJobResultStoreTests.Result(1000, "< & > \" ' 한국어\n]]>\u0001"),
                        DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    );
                descriptor["status"] = "failed";
                descriptor["started_unix_ms"] = (long)descriptor["completed_unix_ms"] - 1000;
                descriptor["finished_unix_ms"] = descriptor["completed_unix_ms"];
                Type exporter = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.TestJobResultExport");
                Assert.NotNull(exporter);
                string cursor = null;
                var text = new StringBuilder();
                string digest = null;
                long total = 0;
                do
                {
                    JObject chunk = (JObject)
                        exporter
                            .GetMethod("Read", BindingFlags.Public | BindingFlags.Static)
                            .Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, descriptor, format, cursor });
                    Assert.LessOrEqual(Encoding.UTF8.GetByteCount((string)chunk["content"]), 65536);
                    digest ??= (string)chunk["sha256"];
                    Assert.AreEqual(digest, (string)chunk["sha256"]);
                    total = (long)chunk["total_bytes"];
                    text.Append((string)chunk["content"]);
                    cursor = (string)chunk["next_cursor"];
                } while (cursor != null);
                byte[] reconstructed = Encoding.UTF8.GetBytes(text.ToString());
                Assert.AreEqual(total, reconstructed.LongLength);
                Assert.AreEqual(digest, TestJobResultStore.Hash(reconstructed));
                Assert.LessOrEqual(
                    ((TestJobResultStore)store).ExportHashBytes,
                    3.25 * total + 16384,
                    "Hash the whole result and its blocks once, then only requested blocks."
                );
                if (format == "json")
                {
                    JObject value = JObject.Parse(text.ToString());
                    Assert.AreEqual(1000, ((JArray)value["results"]).Count);
                    Assert.AreEqual(100, (int)value["summary"]["failed"]);
                    Assert.AreEqual("failed", (string)value["status"]);
                    Assert.AreEqual((long)descriptor["started_unix_ms"], (long)value["started_unix_ms"]);
                    Assert.AreEqual((long)descriptor["finished_unix_ms"], (long)value["finished_unix_ms"]);
                    Assert.AreEqual((string)descriptor["generation"], (string)value["generation"]);
                    Assert.AreEqual(0, (int)value["truncated_result_count"]);
                    StringAssert.Contains("한국어", (string)value["results"][0]["message"]);
                }
                else
                {
                    var document = new XmlDocument { XmlResolver = null };
                    using (
                        var reader = XmlReader.Create(
                            new StringReader(text.ToString()),
                            new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null }
                        )
                    )
                        document.Load(reader);
                    Assert.AreEqual(1000, document.SelectNodes("//testcase").Count);
                    Assert.AreEqual(100, document.SelectNodes("//failure").Count);
                    StringAssert.Contains("< & > \" ' 한국어\n]]>", document.SelectSingleNode("//failure").InnerText);
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }
}
