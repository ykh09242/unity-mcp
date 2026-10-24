using System;
using System.IO;
using System.Reflection;
using System.Runtime.ExceptionServices;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class PlayModeOptionsDurabilityTests
    {
        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;
        private bool enabled;
        private EnterPlayModeOptions options;
        private string marker;
        private string ownedDirectory;
        private FieldInfo overridePath;
        private bool captured;
        private bool markerOwned;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            markerOwned = false;
            marker = null;
            ownedDirectory = null;
            overridePath = null;
            if (PlayModeOptionsGuard.IsPending)
                Assert.Ignore("Another run owns pending settings recovery.");
            enabled = EditorSettings.enterPlayModeOptionsEnabled;
            options = EditorSettings.enterPlayModeOptions;
            captured = true;
            overridePath = typeof(PlayModeOptionsGuard).GetField("MarkerPathForTests", Flags);
            if (overridePath != null)
            {
                ownedDirectory = Path.Combine(Path.GetTempPath(), "TcrSettings_" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(ownedDirectory);
                marker = Path.Combine(ownedDirectory, "marker.json");
                overridePath.SetValue(null, marker);
            }
            else
                marker = Path.GetFullPath((string)typeof(PlayModeOptionsGuard).GetField("MarkerPath", Flags).GetValue(null));
            Assert.IsFalse(File.Exists(marker), "Only an unoccupied fixture marker may be used.");
            Assert.IsFalse(Directory.Exists(marker));
            markerOwned = true;
            ClearSession();
        }

        private static void ClearSession()
        {
            SessionState.EraseBool("MCPForUnity.PlayModeOptions.PendingRestore");
            SessionState.EraseBool("MCPForUnity.PlayModeOptions.OriginalEnabled");
            SessionState.EraseInt("MCPForUnity.PlayModeOptions.OriginalOptions");
            SessionState.EraseString("MCPForUnity.PlayModeOptions.Record.v2");
        }

        [TearDown]
        public void TearDown()
        {
            PlayModeOptionsGuard.SettingsBoundaryForTests = null;
            PlayModeOptionsGuard.FlushForTests = null;
            PlayModeOptionsGuard.DeleteMarkerForTests = null;
            if (!captured)
                return;
            EditorSettings.enterPlayModeOptions = options;
            EditorSettings.enterPlayModeOptionsEnabled = enabled;
            ClearSession();
            if (markerOwned && marker != null)
            {
                if (File.Exists(marker))
                    File.Delete(marker);
                if (Directory.Exists(marker))
                    Directory.Delete(marker);
            }
            overridePath?.SetValue(null, null);
            if (ownedDirectory != null && Path.GetFileName(ownedDirectory).StartsWith("TcrSettings_", StringComparison.Ordinal))
                Directory.Delete(ownedDirectory, true);
        }

        private static void SetApplied()
        {
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
        }

        [Test]
        public void RestoreDoesNotOverwriteSettingsChangedByAnotherOwner()
        {
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            SetApplied();
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableSceneReload;
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableSceneReload, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending, "A conflicted record must not reapply on later reloads.");
        }

        [Test]
        public void MarkerWriteFailurePreventsSettingsMutation()
        {
            Directory.CreateDirectory(marker);
            EditorSettings.enterPlayModeOptionsEnabled = false;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
            MethodInfo admission = typeof(PlayModeOptionsGuard).GetMethod("Apply", Flags);
            Assert.Catch<IOException>(() =>
            {
                try
                {
                    admission.Invoke(null, new object[] { false, EnterPlayModeOptions.None });
                }
                catch (TargetInvocationException ex)
                {
                    ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
                }
            });
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
        }

        [Test]
        public void DiskRecordRestoresWithoutSessionStateAndThenBecomesInactive()
        {
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            SetApplied();
            ClearSession();
            Assert.IsTrue(PlayModeOptionsGuard.IsPending);
            PlayModeOptionsGuard.Restore();
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
            ClearSession();
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
        }

        [Test]
        public void ForeignOwnerRecordIsRetainedWithoutChangingSettings()
        {
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            JObject record = JObject.Parse(File.ReadAllText(marker));
            record["Owner"] = new string('f', 64);
            File.WriteAllText(marker, record.ToString());
            ClearSession();
            SetApplied();
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
            Assert.IsTrue(File.Exists(marker));
        }

        [Test]
        public void UnboundLegacyMarkerCannotRestoreAcrossProcessRestart()
        {
            File.WriteAllText(marker, "0\n0");
            SetApplied();
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
            Assert.IsTrue(File.Exists(marker), "Unknown legacy evidence remains available for explicit inspection.");
        }

        [Test]
        public void IncompleteRecordCannotAuthorizeRestore()
        {
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            JObject record = JObject.Parse(File.ReadAllText(marker));
            record.Remove("OriginalEnabled");
            File.WriteAllText(marker, record.ToString());
            ClearSession();
            SetApplied();
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
            Assert.IsTrue(File.Exists(marker));
        }

        private sealed class InterruptedSettingsException : Exception { }

        private static void Admit()
        {
            try
            {
                typeof(PlayModeOptionsGuard).GetMethod("Apply", Flags).Invoke(null, new object[] { false, EnterPlayModeOptions.None });
            }
            catch (TargetInvocationException ex)
            {
                ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            }
        }

        [TestCase("before-apply-enabled")]
        [TestCase("after-apply-enabled")]
        [TestCase("before-apply-options")]
        [TestCase("after-apply-options")]
        [TestCase("before-restore-options")]
        [TestCase("after-restore-options")]
        [TestCase("before-restore-enabled")]
        [TestCase("after-restore-enabled")]
        public void InterruptedSettingTransitionRestoresFromDisk(string boundary)
        {
            EditorSettings.enterPlayModeOptionsEnabled = false;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
            if (boundary.Contains("restore"))
                Admit();
            PlayModeOptionsGuard.SettingsBoundaryForTests = current =>
            {
                if (current == boundary)
                    throw new InterruptedSettingsException();
            };
            Assert.Throws<InterruptedSettingsException>(() =>
            {
                if (boundary.Contains("apply"))
                    Admit();
                else
                    PlayModeOptionsGuard.Restore();
            });
            PlayModeOptionsGuard.SettingsBoundaryForTests = null;
            ClearSession();
            PlayModeOptionsGuard.Restore();
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
        }

        [Test]
        public void UnjournaledMixedTupleIsPreservedAsConflict()
        {
            EditorSettings.enterPlayModeOptionsEnabled = false;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            EditorSettings.enterPlayModeOptionsEnabled = true;
            // Unity versions differ: this setter can also enable both option flags.
            var externalOptions = EditorSettings.enterPlayModeOptions;
            ClearSession();
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(externalOptions, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
        }

        [Test]
        public void FlushFailurePreventsSettingsAdmission()
        {
            EditorSettings.enterPlayModeOptionsEnabled = false;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
            PlayModeOptionsGuard.FlushForTests = stream => throw new IOException("Injected flush failure");
            Assert.Throws<IOException>(Admit);
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(File.Exists(marker));
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
            Assert.IsNotEmpty(Directory.GetFiles(ownedDirectory, "*.tmp"), "Unconfirmed staging evidence is retained.");
        }

        [Test]
        public void DeleteFailureLeavesInactiveCompletionAcrossRestart()
        {
            EditorSettings.enterPlayModeOptionsEnabled = false;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
            Admit();
            PlayModeOptionsGuard.DeleteMarkerForTests = path => throw new IOException("Injected delete failure");
            PlayModeOptionsGuard.Restore();
            Assert.IsTrue(File.Exists(marker));
            Assert.AreEqual("complete", (string)JObject.Parse(File.ReadAllText(marker))["Status"]);
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            ClearSession();
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableSceneReload;
            PlayModeOptionsGuard.RestoreIfIdle();
            Assert.AreEqual(EnterPlayModeOptions.DisableSceneReload, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
        }

        [Test]
        public void PendingOriginalCannotBeReplacedByAnotherRun()
        {
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            string original = File.ReadAllText(marker);
            Assert.Catch<IOException>(() => PlayModeOptionsGuard.Save(true, EnterPlayModeOptions.DisableSceneReload));
            Assert.AreEqual(original, File.ReadAllText(marker));
        }
    }
}
