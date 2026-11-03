using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests
{
    public sealed class PlayScenarioBuildJournalTests
    {
        private string project;
        private ScriptingImplementation backend;
        private string folder;

        [SetUp]
        public void SetUp()
        {
            project = Path.GetDirectoryName(Application.dataPath);
            if (PlayScenarioBuildJournal.Pending(project) || !string.IsNullOrEmpty(SessionState.GetString(PlayScenarioBuildJournal.ActiveKey, "")))
                Assert.Ignore("Recovery tests require a project without prior scenario build ownership.");
            backend = ScriptingImplementation.Mono2x;
        }

        private PlayScenarioBuildJournal Open() => new(project, () => backend, value => backend = value);

        [Test]
        public void BuildOutputCannotEncloseOrEnterJournalStorage()
        {
            string directory = Path.Combine(project, PlayScenarioBuildJournal.DirectoryName);
            foreach (string output in new[] { directory, Path.Combine(directory, "output"), Path.GetDirectoryName(directory) })
            {
                var error = Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBuild.BuildFrozen(null, Array.Empty<string>(), output));
                StringAssert.Contains("overlap", error.Message);
            }
        }

        private void Prepare(PlayScenarioBuildJournal journal)
        {
            journal.Begin(ScriptingImplementation.IL2CPP);
            Assert.IsTrue(PlayScenarioBuildJournal.Pending(project));
            Assert.AreEqual(ScriptingImplementation.Mono2x, backend, "The journal must precede the backend mutation.");
            journal.ApplyBackend();
            folder = journal.AssetFolder;
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            journal.Capture("");
            AssetDatabase.CreateFolder(folder, "Resources");
            journal.Capture("Resources");
            string asset = folder + "/" + PlayScenarioBuildJournal.Resource;
            File.WriteAllText(Path.Combine(project, asset), "{}");
            AssetDatabase.ImportAsset(asset, ImportAssetOptions.ForceSynchronousImport);
            journal.Capture(PlayScenarioBuildJournal.Resource, "{}");
        }

        [Test]
        public void ReopenedJournalRestoresBackendAndRemovesOnlyRecordedAssets()
        {
            string other = "Assets/__MCPScenarioBuild_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(other));
            try
            {
                using (var owner = Open())
                    Prepare(owner);
                Assert.AreEqual(ScriptingImplementation.IL2CPP, backend);
                using var reopened = Open();
                reopened.Recover();
                reopened.Recover();
                Assert.AreEqual(ScriptingImplementation.Mono2x, backend);
                Assert.IsFalse(AssetDatabase.IsValidFolder(folder));
                Assert.IsFalse(File.Exists(Path.Combine(project, folder + ".meta")));
                Assert.IsTrue(AssetDatabase.IsValidFolder(other));
                Assert.IsFalse(PlayScenarioBuildJournal.Pending(project));
            }
            finally
            {
                AssetDatabase.DeleteAsset(other);
            }
        }

        [TestCase("changed_content")]
        [TestCase("foreign_child")]
        [TestCase("changed_identity")]
        public void ReplacedAssetsPreventLaterBuildAndRetainManualBackendChoice(string conflict)
        {
            using (var owner = Open())
                Prepare(owner);
            string resource = Path.Combine(project, folder + "/" + PlayScenarioBuildJournal.Resource);
            string sidecar = resource + ".meta";
            string extra = Path.Combine(project, folder, "other.txt");
            byte[] meta = File.ReadAllBytes(sidecar);
            switch (conflict)
            {
                case "changed_content":
                    File.WriteAllText(resource, "changed");
                    break;
                case "foreign_child":
                    File.WriteAllText(extra, "foreign");
                    break;
                case "changed_identity":
                    File.WriteAllText(
                        sidecar,
                        File.ReadAllText(sidecar)
                            .Replace(AssetDatabase.AssetPathToGUID(folder + "/" + PlayScenarioBuildJournal.Resource), Guid.NewGuid().ToString("N"))
                    );
                    break;
            }
            // Simulate a separately chosen value, without mutating the real project backend.
            backend = (ScriptingImplementation)99;
            using var reopened = Open();
            try
            {
                Assert.Throws<IOException>(() => reopened.Begin(ScriptingImplementation.Mono2x));
                Assert.Throws<IOException>(() => reopened.Recover());
                Assert.IsTrue(PlayScenarioBuildJournal.Pending(project));
                Assert.AreEqual((ScriptingImplementation)99, backend);
                Assert.IsTrue(File.Exists(resource));
                if (conflict == "changed_content")
                    Assert.AreEqual("changed", File.ReadAllText(resource));
                if (conflict == "foreign_child")
                    Assert.AreEqual("foreign", File.ReadAllText(extra));
                if (conflict == "changed_identity")
                    CollectionAssert.AreNotEqual(meta, File.ReadAllBytes(sidecar));
            }
            finally
            {
                File.WriteAllText(resource, "{}");
                File.WriteAllBytes(sidecar, meta);
                if (File.Exists(extra))
                    File.Delete(extra);
                // A settled restoration must not claim the old selected value again on retry.
                backend = ScriptingImplementation.IL2CPP;
                reopened.Recover();
            }
            Assert.AreEqual(ScriptingImplementation.IL2CPP, backend);
            Assert.IsFalse(PlayScenarioBuildJournal.Pending(project));
        }

        [Test]
        public void LiveLeaseAndReloadedSessionRefuseRecoveryUntilProjectReopen()
        {
            string active;
            using (var owner = Open())
            {
                Prepare(owner);
                Assert.Throws<IOException>(() =>
                {
                    using var competing = Open();
                });
                active = SessionState.GetString(PlayScenarioBuildJournal.ActiveKey, "");
                Assert.IsNotEmpty(active);
            }
            using var reloaded = Open();
            SessionState.SetString(PlayScenarioBuildJournal.ActiveKey, active);
            try
            {
                Assert.Throws<IOException>(() => reloaded.Recover());
                Assert.AreEqual(ScriptingImplementation.IL2CPP, backend);
                Assert.IsTrue(AssetDatabase.IsValidFolder(folder));
            }
            finally
            {
                SessionState.EraseString(PlayScenarioBuildJournal.ActiveKey);
                reloaded.Recover();
            }
            Assert.IsFalse(PlayScenarioBuildJournal.Pending(project));
        }

        [Test]
        public void CopiedJournalRefusesAnotherProjectBeforeRestoration()
        {
            using (var owner = Open())
                Prepare(owner);
            string other = Path.Combine(Path.GetTempPath(), "mcp-scenario-journal-test-" + Guid.NewGuid().ToString("N"));
            string evidence = Path.Combine(other, PlayScenarioBuildJournal.DirectoryName);
            Directory.CreateDirectory(evidence);
            try
            {
                File.Copy(Path.Combine(project, PlayScenarioBuildJournal.DirectoryName, "state.json"), Path.Combine(evidence, "state.json"));
                using var copied = new PlayScenarioBuildJournal(other, () => backend, value => backend = value);
                Assert.Throws<IOException>(() => copied.Recover());
                Assert.AreEqual(ScriptingImplementation.IL2CPP, backend);
                Assert.IsTrue(AssetDatabase.IsValidFolder(folder));
            }
            finally
            {
                File.Delete(Path.Combine(evidence, "state.json"));
                File.Delete(Path.Combine(evidence, "owner.lock"));
                Directory.Delete(evidence);
                Directory.Delete(Path.GetDirectoryName(evidence));
                Directory.Delete(Path.Combine(other, "Library"));
                Directory.Delete(other);
                using var reopened = Open();
                reopened.Recover();
            }
        }

        [Test]
        public void RecoveryResumesAfterPartialAssetCleanup()
        {
            using (var owner = Open())
                Prepare(owner);
            Assert.IsTrue(AssetDatabase.DeleteAsset(folder + "/" + PlayScenarioBuildJournal.Resource));
            using var reopened = Open();
            reopened.Recover();
            Assert.IsFalse(AssetDatabase.IsValidFolder(folder));
            Assert.AreEqual(ScriptingImplementation.Mono2x, backend);
            Assert.IsFalse(PlayScenarioBuildJournal.Pending(project));
        }
    }
}
