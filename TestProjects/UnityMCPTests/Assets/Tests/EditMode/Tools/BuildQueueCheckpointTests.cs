using System;
using System.IO;
using MCPForUnity.Editor.Tools.Build;
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
    }
}
