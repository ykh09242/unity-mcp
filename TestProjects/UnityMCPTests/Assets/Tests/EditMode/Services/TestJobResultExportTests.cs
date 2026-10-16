using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Xml;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class TestJobResultExportTests
    {
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
                if (attribution != "clean")
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
                JObject first = JObject.FromObject(MCPForUnity.Editor.Tools.ExportTestJob.HandleCommand(parameters));
                Assert.IsTrue((bool)first["success"]);
                JObject exported = JObject.Parse((string)first["data"]["content"]);
                Assert.AreEqual("failed", (string)exported["status"]);
                Assert.AreEqual(jobs[TestJobResultStoreTests.JobId].StartedUnixMs, (long)exported["started_unix_ms"]);
                Assert.AreEqual(jobs[TestJobResultStoreTests.JobId].FinishedUnixMs, (long?)exported["finished_unix_ms"]);
                Assert.AreEqual(1, (int)exported["summary"]["failed"]);
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
                jobs.Clear();
                foreach (var entry in backup)
                    jobs.Add(entry.Key, entry.Value);
                SessionState.SetString("MCPForUnity.TestJobsV1", oldJobs);
                SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", oldKey);
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
                do
                {
                    JObject chunk = (JObject)
                        exporter
                            .GetMethod("Read", BindingFlags.Public | BindingFlags.Static)
                            .Invoke(null, new object[] { store, TestJobResultStoreTests.JobId, descriptor, format, cursor });
                    Assert.LessOrEqual(Encoding.UTF8.GetByteCount((string)chunk["content"]), 65536);
                    text.Append((string)chunk["content"]);
                    cursor = (string)chunk["next_cursor"];
                } while (cursor != null);
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
