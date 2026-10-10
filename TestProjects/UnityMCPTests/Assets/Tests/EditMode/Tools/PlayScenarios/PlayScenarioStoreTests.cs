using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public class PlayScenarioStoreTests
    {
        private string root;
        private PlayScenarioStore store;
        private string Definitions => Path.Combine(root, "ProjectSettings/MCPForUnity/PlayScenarios");
        private string Reports => Path.Combine(root, "Library/MCPForUnity/PlayScenarioRuns");

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-play-scenario-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            store = new PlayScenarioStore(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private static PlayScenarioDefinition Definition(string name = "menu-start") => PlayScenarioDefinition.Parse(PlayScenarioDefinitionTests.Valid(name));

        private static PlayScenarioRun Run(string jobId) =>
            new PlayScenarioRun
            {
                JobId = jobId,
                Scenario = Definition(),
                RepeatCount = 1,
                Status = "succeeded",
                Phase = "finished",
                StartedUnixMs = 100,
                DeadlineUnixMs = 1000,
                FinishedUnixMs = 500,
            };

        [Test]
        public void RoundTripReplacementListingAndDeleteAreDetached()
        {
            CollectionAssert.IsEmpty(store.List());
            store.Save(Definition("z"));
            store.Save(Definition("a"));
            CollectionAssert.AreEqual(new[] { "a", "z" }, store.List());
            var value = store.Get("a");
            value.PollIntervalMs = 700;
            Assert.AreEqual(250, store.Get("a").PollIntervalMs);
            store.Save(value);
            Assert.AreEqual(700, store.Get("a").PollIntervalMs);
            Assert.AreEqual(0, Directory.GetFiles(Definitions, "*.tmp").Length);
            Assert.IsTrue(store.Delete("a"));
            Assert.IsFalse(store.Delete("a"));
            Assert.Throws<ArgumentException>(() => store.Get("../a"));
            Assert.Throws<ArgumentException>(() => store.Delete("../a"));
        }

        [Test]
        public void DefinitionCountAndByteLimitsPreserveExistingFiles()
        {
            for (int i = 0; i < 100; i++)
                store.Save(Definition("scenario-" + i));
            Assert.Throws<InvalidOperationException>(() => store.Save(Definition("overflow")));
            store.Save(Definition("scenario-0"));
            Assert.AreEqual(100, store.List().Count);
            File.WriteAllText(Path.Combine(Definitions, "scenario-0.json"), new string(' ', 65537));
            Assert.Throws<InvalidDataException>(() => store.Get("scenario-0"));
            var huge = Definition("scenario-1");
            huge.Steps = Enumerable
                .Range(0, 32)
                .Select(_ => new PlayScenarioStep
                {
                    Name = "load",
                    Action = "load_scene",
                    Scene = "Assets/" + new string('a', 4000) + ".unity",
                })
                .ToList();
            Assert.Throws<ArgumentException>(() => store.Save(huge));
            Assert.AreEqual(4, store.Get("scenario-1").Steps.Count);
            Assert.AreEqual(0, Directory.GetFiles(Definitions, "*.tmp").Length);
        }

        [Test]
        public void RejectsStoredUnknownFieldsDuplicateKeysAndMismatchedName()
        {
            store.Save(Definition());
            string path = Path.Combine(Definitions, "menu-start.json");
            var value = PlayScenarioDefinitionTests.Valid();
            value["extra"] = 1;
            File.WriteAllText(path, value.ToString());
            Assert.Throws<ArgumentException>(() => store.Get("menu-start"));
            File.WriteAllText(path, "{\"name\":\"a\",\"name\":\"b\"}");
            Assert.Throws<JsonReaderException>(() => store.Get("menu-start"));
            File.WriteAllText(path, PlayScenarioDefinitionTests.Valid("different").ToString());
            Assert.Throws<InvalidDataException>(() => store.Get("menu-start"));
            CollectionAssert.AreEqual(new[] { "menu-start" }, store.List());
        }

        [Test]
        public void ReportsRoundTripRetainTwentyAndRejectUnboundedPayloads()
        {
            for (int i = 0; i < 25; i++)
            {
                string jobId = i.ToString("x32");
                var run = Run(jobId);
                string relative = store.SaveReport(run);
                Assert.AreEqual(relative, run.ReportPath);
                Assert.AreEqual(relative, store.GetReport(jobId).ReportPath);
            }
            Assert.AreEqual(20, Directory.GetFiles(Reports, "*.json").Length);
            var latest = Run(24.ToString("x32"));
            store.SaveReport(latest);
            Assert.AreEqual(20, Directory.GetFiles(Reports, "*.json").Length);
            latest.Error = new string('a', 2 * 1024 * 1024);
            Assert.Throws<ArgumentException>(() => store.SaveReport(latest));
            Assert.IsNull(store.GetReport(latest.JobId).Error);
            Assert.AreEqual(0, Directory.GetFiles(Reports, "*.tmp").Length);
            Assert.Throws<ArgumentException>(() => store.GetReport("../x"));
        }

        [Test]
        public void StoredReportValidationRejectsCoercionUnknownFieldsAndMismatch()
        {
            var run = Run(Guid.NewGuid().ToString("N"));
            store.SaveReport(run);
            string path = Path.Combine(Reports, run.JobId + ".json");
            var value = JObject.FromObject(run);
            value["repeat_count"] = "1";
            File.WriteAllText(path, value.ToString());
            Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
            value = JObject.FromObject(run);
            value["surprise"] = true;
            File.WriteAllText(path, value.ToString());
            Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
            value = JObject.FromObject(run);
            value["job_id"] = Guid.NewGuid().ToString("N");
            File.WriteAllText(path, value.ToString());
            Assert.Throws<InvalidDataException>(() => store.GetReport(run.JobId));
            run.Logs = Enumerable.Range(0, 51).Select(_ => new PlayScenarioLog()).ToList();
            Assert.Throws<ArgumentException>(() => store.SaveReport(run));
        }

        [Test]
        public void ExactByteLimitReadsAndTrailingContentIsRejected()
        {
            store.Save(Definition());
            string path = Path.Combine(Definitions, "menu-start.json");
            string json = File.ReadAllText(path);
            File.WriteAllText(path, json + new string(' ', 65536 - System.Text.Encoding.UTF8.GetByteCount(json)), new System.Text.UTF8Encoding(false));
            Assert.AreEqual(65536, new FileInfo(path).Length);
            Assert.AreEqual(4, store.Get("menu-start").Steps.Count);
            File.WriteAllText(path, json + " {}");
            Assert.Catch(() => store.Get("menu-start"));
        }

        [Test]
        public void FailedAtomicCommitRemovesItsTemporaryFile()
        {
            store.Save(Definition());
            Directory.CreateDirectory(Path.Combine(Definitions, "blocked.json"));
            Assert.Catch(() => store.Save(Definition("blocked")));
            Assert.AreEqual(0, Directory.GetFiles(Definitions, "*.tmp").Length);
            Assert.AreEqual(4, store.Get("menu-start").Steps.Count);
        }

        [Test]
        public void LinkedDefinitionFileIsRejectedWithoutTouchingItsTarget()
        {
            store.Save(Definition());
            string target = Path.Combine(root, "borrowed.json");
            File.WriteAllText(target, "borrowed");
            string link = Path.Combine(Definitions, "linked.json");
            try
            {
                // Reflection keeps this test compilable on Unity's older .NET profile.
                var create = typeof(File).GetMethod("CreateSymbolicLink", new[] { typeof(string), typeof(string) });
                if (create == null)
                    Assert.Ignore("This runtime does not expose symbolic-link creation.");
                create.Invoke(null, new object[] { link, target });
            }
            catch (Exception error)
            {
                Assert.Ignore("Symbolic-link creation unavailable: " + error.Message);
            }
            try
            {
                Assert.Throws<InvalidOperationException>(() => store.Get("linked"));
                Assert.Throws<InvalidOperationException>(() => store.Save(Definition("linked")));
                Assert.Throws<InvalidOperationException>(() => store.Delete("linked"));
                Assert.AreEqual("borrowed", File.ReadAllText(target));
            }
            finally
            {
                File.Delete(link);
            }
        }
    }
}
