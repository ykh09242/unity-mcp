using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    public class EditorPreferenceLeaseTests
    {
        private string root;
        private Dictionary<string, int> preferences;
        private Dictionary<int, long> live;
        private int refreshes;

        [SetUp]
        public void SetUp()
        {
            root = SafePathUtility.ResolveWithinRoot(Application.temporaryCachePath, "McpPreferenceLeaseTests/" + Guid.NewGuid().ToString("N"));
            preferences = new Dictionary<string, int> { ["ApplicationIdleTime"] = 4, ["InteractionMode"] = 0 };
            live = new Dictionary<int, long> { [101] = 1001, [202] = 2002 };
            refreshes = 0;
        }

        [TearDown]
        public void TearDown()
        {
            SafePathUtility.ResolveWithinRoot(Application.temporaryCachePath, root);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private EditorPreferenceLease Lease(int pid, Action<string, int> setter = null, Action refresher = null) =>
            new EditorPreferenceLease(
                root,
                pid,
                live[pid],
                (id, start) => live.TryGetValue(id, out var current) && current == start,
                (key, fallback) => preferences.TryGetValue(key, out int value) ? value : fallback,
                setter ?? ((key, value) => preferences[key] = value),
                refresher ?? (() => refreshes++)
            );

        private void AssertPreferences(int idle, int mode)
        {
            Assert.AreEqual(idle, preferences["ApplicationIdleTime"]);
            Assert.AreEqual(mode, preferences["InteractionMode"]);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void ConcurrentEditors_OnlyLastOwnerRestoresOriginalPreferences(bool firstFinishesFirst)
        {
            var a = Lease(101);
            var b = Lease(202);
            a.Acquire();
            b.Acquire();
            Assert.AreEqual(2, refreshes, "A joining Editor must apply the shared preferences to its own interaction loop.");
            for (int i = 0; i < 30; i++)
            {
                a.Acquire();
                b.Acquire();
            }
            Assert.AreEqual(2, refreshes, "Maintenance must not refresh unchanged interaction settings repeatedly.");
            (firstFinishesFirst ? a : b).Release();
            AssertPreferences(0, 1);
            (firstFinishesFirst ? b : a).Release();
            AssertPreferences(4, 0);
            Assert.IsFalse(a.Exists);
            Assert.IsFalse(File.Exists(Path.Combine(root, "state.stage")));
        }

        [Test]
        public void FormerOwner_RefreshesLocalEditorWhenPeerRestoresSharedPreferences()
        {
            var a = Lease(101);
            var b = Lease(202);
            a.Acquire();
            b.Acquire();
            a.Release();
            b.Release();
            Assert.AreEqual(3, refreshes);
            a.Recover();
            Assert.AreEqual(4, refreshes, "The first Editor must observe the final shared restoration too.");
            a.Recover();
            Assert.AreEqual(4, refreshes, "Idle cleanup should refresh only once.");
        }

        [Test]
        public void NewRunAfterPeerRestoration_RefreshesItsEditorAgain()
        {
            var a = Lease(101);
            var b = Lease(202);
            a.Acquire();
            b.Acquire();
            a.Release();
            b.Release();
            a.Acquire();
            Assert.AreEqual(4, refreshes, "A new acquisition must refresh even before the idle maintenance tick.");
            a.Release();
            AssertPreferences(4, 0);
        }

        [Test]
        public void ForcedTerminationAndReusedPid_DoNotRetainOrStealOwnership()
        {
            Lease(101).Acquire();
            Lease(202).Acquire();
            live.Remove(101);
            var survivor = Lease(202);
            survivor.Recover();
            AssertPreferences(0, 1);
            live[202] = 9999;
            Lease(202).Recover();
            AssertPreferences(4, 0);
            Assert.IsFalse(survivor.Exists);
        }

        [Test]
        public void ManualChanges_ArePreservedUntilAllOwnersRelease()
        {
            var a = Lease(101);
            var b = Lease(202);
            a.Acquire();
            preferences["ApplicationIdleTime"] = 12;
            preferences["InteractionMode"] = 2;
            b.Acquire();
            a.Acquire();
            a.Release();
            b.Release();
            AssertPreferences(12, 2);
            Assert.IsFalse(a.Exists);
        }

        [Test]
        public void ManualChangeInPeer_RefreshesLastOwnersLocalInteractionLoop()
        {
            int firstRefreshes = 0;
            int lastRefreshes = 0;
            var a = Lease(101, refresher: () => firstRefreshes++);
            var b = Lease(202, refresher: () => lastRefreshes++);
            a.Acquire();
            b.Acquire();
            preferences["ApplicationIdleTime"] = 12;
            preferences["InteractionMode"] = 2;
            a.Release();
            b.Release();
            AssertPreferences(12, 2);
            Assert.AreEqual(2, lastRefreshes, "The last Editor must apply the user's changed values locally.");
            b.Recover();
            Assert.AreEqual(2, lastRefreshes);
            a.Recover();
            Assert.AreEqual(2, firstRefreshes);
        }

        [TestCase("ApplicationIdleTime")]
        [TestCase("InteractionMode")]
        public void TerminationAtApplyBoundary_RestoresOnlyDurablyOwnedValues(string boundary)
        {
            var a = Lease(
                101,
                (key, value) =>
                {
                    preferences[key] = value;
                    if (key == boundary)
                        throw new IOException("simulated termination");
                }
            );
            Assert.Throws<IOException>(() => a.Acquire());
            live.Remove(101);
            Lease(202).Recover();
            AssertPreferences(4, 0);
            Assert.IsFalse(a.Exists);
        }

        [TestCase("ApplicationIdleTime")]
        [TestCase("InteractionMode")]
        public void TerminationAtRestoreBoundary_RestartCompletesRecovery(string boundary)
        {
            Lease(101).Acquire();
            var a = Lease(
                101,
                (key, value) =>
                {
                    preferences[key] = value;
                    if (key == boundary)
                        throw new IOException("simulated termination");
                }
            );
            Assert.Throws<IOException>(() => a.Release());
            live.Remove(101);
            Lease(202).Recover();
            AssertPreferences(4, 0);
            Assert.IsFalse(a.Exists);
        }

        [Test]
        public void RecreatedOwner_ReusesOriginalsAndRefreshesItsOwnEditor()
        {
            Lease(101).Acquire();
            var afterReload = Lease(101);
            afterReload.Acquire();
            Assert.AreEqual(2, refreshes);
            afterReload.Release();
            AssertPreferences(4, 0);
        }

        [Test]
        public void BusyLease_ChangesNoPreferencesAndSucceedsOnRetry()
        {
            Directory.CreateDirectory(root);
            var a = Lease(101);
            using (ExclusiveFileLease.Acquire(Path.Combine(root, "state.lock")))
                Assert.Throws<IOException>(() => a.Acquire());
            AssertPreferences(4, 0);
            Assert.IsFalse(a.Exists);
            a.Acquire();
            AssertPreferences(0, 1);
            a.Release();
            AssertPreferences(4, 0);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void LegacySessionAdoption_PreservesManualChangesAndOriginals(bool manuallyChanged)
        {
            preferences["ApplicationIdleTime"] = manuallyChanged ? 9 : 0;
            preferences["InteractionMode"] = manuallyChanged ? 2 : 1;
            var a = Lease(101);
            a.Acquire(4, 0);
            a.Release();
            AssertPreferences(manuallyChanged ? 9 : 4, manuallyChanged ? 2 : 0);
        }

        [Test]
        public void MalformedRecoveryMetadata_DoesNotReplacePreferencesOrEvidence()
        {
            Directory.CreateDirectory(root);
            const string damaged = "{\"Version\":1,\"Owners\":[]}";
            File.WriteAllText(Path.Combine(root, "state.json"), damaged);
            Assert.Catch(() => Lease(101).Acquire());
            AssertPreferences(4, 0);
            Assert.AreEqual(damaged, File.ReadAllText(Path.Combine(root, "state.json")));
        }

        [Test]
        public void IdleRecovery_DoesNotCreateDirectoriesOrPreferences()
        {
            var a = Lease(101);
            a.Recover();
            a.Release();
            Assert.IsFalse(Directory.Exists(root));
            AssertPreferences(4, 0);
        }
    }
}
