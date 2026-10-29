using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    public class BuildQueueCheckpointTests
    {
        private string stored;
        private bool writing;

        private BuildQueueJournal Journal(Action<string> write = null) =>
            new BuildQueueJournal(() => stored, write ?? (value => stored = value), () => writing, value => writing = value, "owned-test-project");

        [SetUp]
        public void SetUp()
        {
            stored = "";
            writing = false;
        }

        [Test]
        public void FrozenOptionsAndOriginalJobIdRoundTripAcrossNewJournal()
        {
            var options = new BuildPlayerOptions
            {
                scenes = new[] { "Assets/Original.unity" },
                target = BuildTarget.StandaloneWindows64,
                locationPathName = "Builds/Original.exe",
                extraScriptingDefines = new[] { "ORIGINAL" },
                options = BuildOptions.Development,
            };
            BuildQueueEntry entry = BuildQueueEntry.Single(new BuildJob("original-job", options.target, options.locationPathName), options);
            Journal().Save(new[] { entry });
            options.scenes[0] = "Assets/Changed.unity";
            BuildQueueEntry restored = Journal().Load()[0];
            Assert.AreEqual("original-job", restored.Id);
            Assert.AreEqual("queued", restored.Children[0].Phase);
            Assert.AreEqual("Assets/Original.unity", restored.Children[0].Options.scenes[0]);
            Assert.AreEqual("ORIGINAL", restored.Children[0].Options.extraScriptingDefines[0]);
            Assert.AreEqual("Builds/Original.exe", restored.Children[0].Options.locationPathName);
        }

        [Test]
        public void InvokedAndCancelledPhasesAreNeverMadeQueuedByReload()
        {
            var entry = BuildQueueEntry.Single(new BuildJob("same-job", BuildTarget.StandaloneWindows64, "Builds/test.exe"), new BuildPlayerOptions());
            entry.Children[0].Phase = "native_started";
            Journal().Save(new[] { entry });
            Assert.AreEqual("native_started", Journal().Load()[0].Children[0].Phase);
            entry.Children[0].Phase = "terminal";
            entry.Children[0].State = BuildJobState.Cancelled;
            Journal().Save(new[] { entry });
            Assert.AreEqual(BuildJobState.Cancelled, Journal().Load()[0].Children[0].State);
        }

        [Test]
        public void FailedWritePoisonsRecoveryEvenWhenPreviousQueuedBytesRemain()
        {
            var entry = BuildQueueEntry.Single(new BuildJob("same-job", BuildTarget.StandaloneWindows64, "Builds/test.exe"), new BuildPlayerOptions());
            Journal().Save(new[] { entry });
            string queued = stored;
            entry.Children[0].Phase = "native_started";
            Assert.Throws<IOException>(() => Journal(value => throw new IOException("controlled persistence failure")).Save(new[] { entry }));
            Assert.AreEqual(queued, stored);
            Assert.IsTrue(writing);
            Assert.Throws<InvalidDataException>(() => Journal().Load());
        }

        [Test]
        public void CorruptionOrWrongProjectNeverBecomesAnEmptyQueue()
        {
            stored = "{corrupt";
            Assert.Throws<InvalidDataException>(() => Journal().Load());
            stored = new JObject
            {
                ["version"] = 1,
                ["project"] = "another-project",
                ["entries"] = new JArray(),
            }.ToString();
            Assert.Throws<InvalidDataException>(() => Journal().Load());
        }

        [Test]
        public void ValidJsonWithDamagedPhaseDoesNotResumeAsQueued()
        {
            var entry = BuildQueueEntry.Single(new BuildJob("same-job", BuildTarget.StandaloneWindows64, "Builds/test.exe"), new BuildPlayerOptions());
            entry.Children[0].Phase = "native_started";
            Journal().Save(new[] { entry });
            stored = stored.Replace("native_started", "queued");
            Assert.Throws<InvalidDataException>(() => Journal().Load());
        }

        [Test]
        public void BatchRoundTripPreservesCompletedChildAndCurrentCursor()
        {
            var entry = BuildQueueEntry.Single(new BuildJob("first", BuildTarget.StandaloneWindows64, "Builds/one.exe"), new BuildPlayerOptions());
            entry.Id = "original-batch";
            entry.Batch = true;
            entry.CurrentIndex = 1;
            entry.Children[0].State = BuildJobState.Succeeded;
            entry.Children[0].Phase = "terminal";
            entry.Children.Add(
                BuildQueueEntry.Single(new BuildJob("second", BuildTarget.StandaloneWindows64, "Builds/two.exe"), new BuildPlayerOptions()).Children[0]
            );
            Journal().Save(new[] { entry });
            BuildQueueEntry restored = Journal().Load()[0];
            Assert.AreEqual("original-batch", restored.Id);
            Assert.AreEqual(1, restored.CurrentIndex);
            Assert.AreEqual(BuildJobState.Succeeded, restored.Children[0].State);
            Assert.AreEqual("second", restored.Children[1].JobId);
        }

        [Test]
        public void TerminalHistoryDoesNotConsumeActiveCapacity()
        {
            var entries = Enumerable.Range(0, 40).Select(index => Entry("terminal-" + index)).ToList();
            foreach (BuildQueueEntry entry in entries)
            {
                entry.Children[0].Phase = "terminal";
                entry.Children[0].State = BuildJobState.Cancelled;
            }
            entries.Add(Entry("new-active"));
            Journal().Save(entries);
            Assert.AreEqual(41, Journal().Load().Count);
        }

        [TestCase("queued")]
        [TestCase("native_started")]
        public void ActiveOrUncertainCapacityStillFailsClosed(string phase)
        {
            var entries = Enumerable.Range(0, 32).Select(index => Entry("active-" + index)).ToList();
            foreach (BuildQueueEntry entry in entries)
                entry.Children[0].Phase = phase;
            Journal().Save(entries);
            string accepted = stored;
            entries.Add(Entry("overflow"));
            Assert.Throws<InvalidDataException>(() => Journal().Save(entries));
            Assert.AreEqual(accepted, stored);
        }

        [Test]
        public void CompactedTerminalParentAndChildIdsRemainExactAfterReload()
        {
            var entries = Enumerable.Range(0, 80).Select(index => Settled("terminal-" + index)).ToList();
            entries[0].Batch = true;
            entries[0].Id = "original-batch";
            var journal = Journal();
            var retained = journal.Save(entries);
            Assert.AreEqual(BuildQueueJournal.MaxRecentTerminalEntries, retained.Count);
            journal = Journal();
            retained = journal.Load();
            Assert.IsTrue(journal.ContainsRetainedId("original-batch"));
            Assert.IsTrue(journal.ContainsRetainedId("terminal-0"));
            Assert.IsFalse(journal.ContainsRetainedId("new-job"));
            Assert.Throws<InvalidDataException>(() => journal.Save(retained.Concat(new[] { Entry("original-batch") })));
            Assert.Throws<InvalidDataException>(() => journal.Save(retained.Concat(new[] { Entry("terminal-0") })));
        }

        [Test]
        public void AdmissionReservesHistoryCapacityForCompletion()
        {
            var journal = Journal();
            var entries = Enumerable.Range(0, BuildQueueJournal.MaxHistoryEntries - 1).Select(index => Settled("terminal-" + index)).ToList();
            entries.Add(Entry("last-admitted"));
            entries = journal.Save(entries);
            BuildQueueEntry active = entries.Single(entry => entry.Id == "last-admitted");
            active.Children[0].Phase = "terminal";
            active.Children[0].State = BuildJobState.Failed;
            active.CurrentIndex = 1;
            Assert.DoesNotThrow(() => entries = journal.Save(entries));
            string accepted = stored;
            StringAssert.Contains(
                "history capacity",
                Assert.Throws<InvalidDataException>(() => journal.Save(entries.Concat(new[] { Entry("overflow") }))).Message
            );
            Assert.AreEqual(accepted, stored);
        }

        [Test]
        public void ByteCapacityRejectsAdmissionWithoutReplacingAcceptedCheckpoint()
        {
            var journal = Journal();
            var entries = journal.Save(new[] { Entry("accepted") });
            string accepted = stored;
            BuildQueueEntry oversized = Entry("oversized");
            oversized.Children[0].OutputPath = new string('x', 1048576);
            Assert.Throws<InvalidDataException>(() => journal.Save(entries.Concat(new[] { oversized })));
            Assert.AreEqual(accepted, stored);
            Assert.IsFalse(writing);
        }

        [Test]
        public void VersionOneCheckpointMigratesWithoutChangingDeadlineOrNativePhase()
        {
            BuildQueueEntry entry = Entry("legacy");
            entry.Children[0].Phase = "native_started";
            Journal().Save(new[] { entry });
            JObject old = JObject.Parse(stored);
            old["version"] = 1;
            old.Remove("history");
            using (SHA256 hash = SHA256.Create())
                old["sha256"] = BitConverter
                    .ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(old["entries"].ToString(Formatting.None))))
                    .Replace("-", "")
                    .ToLowerInvariant();
            stored = old.ToString(Formatting.None);
            var journal = Journal();
            var restored = journal.Load();
            Assert.AreEqual(entry.DeadlineUnixMs, restored[0].DeadlineUnixMs);
            Assert.AreEqual("native_started", restored[0].Children[0].Phase);
            journal.Save(restored);
            Assert.AreEqual(2, (int)JObject.Parse(stored)["version"]);
        }

        [Test]
        public void BatchReportMetadataAndProfileManifestRoundTrip()
        {
            BuildQueueEntry entry = Settled("batch-report");
            entry.Batch = true;
            entry.ReportId = "br-original";
            entry.ReportPersistenceError = "build_report_persistence_unavailable";
            entry.Children[0].Options.assetBundleManifestPath = "Builds/bundles.manifest";
            Journal().Save(new[] { entry });
            BuildQueueEntry restored = Journal().Load()[0];
            Assert.AreEqual(entry.ReportId, restored.ReportId);
            Assert.AreEqual(entry.ReportPersistenceError, restored.ReportPersistenceError);
            Assert.AreEqual("Builds/bundles.manifest", restored.Children[0].Options.assetBundleManifestPath);
        }

        private static BuildQueueEntry Settled(string id)
        {
            BuildQueueEntry entry = Entry(id);
            entry.CurrentIndex = 1;
            entry.Children[0].Phase = "terminal";
            entry.Children[0].State = BuildJobState.Succeeded;
            return entry;
        }

        private static BuildQueueEntry Entry(string id) =>
            BuildQueueEntry.Single(
                new BuildJob(id, BuildTarget.StandaloneWindows64, "Builds/test.exe"),
                new BuildPlayerOptions { scenes = Array.Empty<string>() }
            );
    }
}
