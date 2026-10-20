using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageUIRenderLifecycleTests
    {
        private readonly List<UnityEngine.Object> objects = new();
        private readonly List<int> panelIds = new();
        private RenderTexture originalActive;
        private static readonly BindingFlags PrivateStatic = BindingFlags.Static | BindingFlags.NonPublic;
        private static Dictionary<int, RenderTexture> Cache =>
            (Dictionary<int, RenderTexture>)typeof(ManageUI).GetField("s_panelRTs", PrivateStatic).GetValue(null);
        private static Dictionary<int, (PanelSettings panel, RenderTexture previousTarget)> Bindings =>
            (Dictionary<int, (PanelSettings panel, RenderTexture previousTarget)>)typeof(ManageUI).GetField("s_panelBindings", PrivateStatic).GetValue(null);

        [SetUp]
        public void SetUp() => originalActive = RenderTexture.active;

        [TearDown]
        public void TearDown()
        {
            RenderTexture.active = originalActive;
            foreach (int id in panelIds)
            {
                if (Cache.TryGetValue(id, out var owned))
                {
                    Bindings.TryGetValue(id, out var binding);
                    ManageUI.FinishPanelRender(id, binding.panel, owned, false, true);
                }
                Cache.Remove(id);
                Bindings.Remove(id);
            }
            foreach (var value in objects)
                if (value != null)
                    UnityEngine.Object.DestroyImmediate(value);
            objects.Clear();
            panelIds.Clear();
        }

        private (int id, PanelSettings panel, RenderTexture owned, RenderTexture prior) Seed()
        {
            var panel = ScriptableObject.CreateInstance<PanelSettings>();
            var owned = new RenderTexture(16, 16, 0);
            var prior = new RenderTexture(4, 4, 0);
            objects.Add(panel);
            objects.Add(owned);
            objects.Add(prior);
            int id = panel.GetInstanceIDCompat();
            panelIds.Add(id);
            panel.targetTexture = owned;
            Cache.Add(id, owned);
            Bindings.Add(id, (panel, prior));
            return (id, panel, owned, prior);
        }

        private static void PruneCache() => typeof(ManageUI).GetMethod("PruneUICache", PrivateStatic).Invoke(null, null);

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator IdlePanelTargetsExpireAndRestoreBorrowedBinding()
        {
            var clock = typeof(ManageUI).GetField("s_renderCacheClock", PrivateStatic);
            Assert.IsNotNull(clock, "A panel cache needs idle lifetime ownership.");
            object previous = clock.GetValue(null);
            double now = 1000;
            try
            {
                clock.SetValue(null, (System.Func<double>)(() => now));
                var item = Seed();
                typeof(ManageUI).GetMethod("TouchPanelCache", PrivateStatic).Invoke(null, new object[] { item.id });
                now += 61;
                yield return null;
                yield return null;
                Assert.IsFalse(Cache.ContainsKey(item.id));
                Assert.IsFalse(Bindings.ContainsKey(item.id));
                Assert.IsTrue(item.owned == null);
                Assert.AreSame(item.prior, item.panel.targetTexture);
                Assert.IsFalse((bool)typeof(ManageUI).GetField("s_renderCleanupScheduled", PrivateStatic).GetValue(null));
            }
            finally
            {
                clock.SetValue(null, previous);
            }
        }

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator IdleCleanupProtectsAnActiveDeferredCapture()
        {
            var clock = typeof(ManageUI).GetField("s_renderCacheClock", PrivateStatic);
            var pending = typeof(ManageUI).GetField("s_pendingPanelCapture", PrivateStatic);
            object previousClock = clock.GetValue(null);
            object previousCapture = pending.GetValue(null);
            Assert.IsNull(previousCapture);
            double now = 1000;
            try
            {
                clock.SetValue(null, (System.Func<double>)(() => now));
                var item = Seed();
                var capture = System.Activator.CreateInstance(pending.FieldType, true);
                var fields = BindingFlags.Instance | BindingFlags.NonPublic;
                pending.FieldType.GetField("PanelId", fields).SetValue(capture, item.id);
                pending.FieldType.GetField("Panel", fields).SetValue(capture, item.panel);
                pending.FieldType.GetField("Target", fields).SetValue(capture, item.owned);
                pending.SetValue(null, capture);
                typeof(ManageUI).GetMethod("TouchPanelCache", PrivateStatic).Invoke(null, new object[] { item.id });
                now += 61;
                yield return null;
                yield return null;
                Assert.AreSame(item.owned, Cache[item.id]);
                Assert.IsTrue(item.owned != null);
                pending.SetValue(null, null);
                now += 2;
                yield return null;
                yield return null;
                Assert.IsFalse(Cache.ContainsKey(item.id));
            }
            finally
            {
                pending.SetValue(null, previousCapture);
                clock.SetValue(null, previousClock);
            }
        }

        [Test]
        public void DetachingLastDocumentReleasesCache_ButSharedSettingsStayWarm()
        {
            var item = Seed();
            var first = new GameObject("FirstCachedDocument");
            var second = new GameObject("SecondCachedDocument");
            objects.Add(first);
            objects.Add(second);
            first.AddComponent<UIDocument>().panelSettings = item.panel;
            second.AddComponent<UIDocument>().panelSettings = item.panel;
            foreach (var document in new[] { first, second })
            {
                var response = Newtonsoft.Json.Linq.JObject.FromObject(
                    ManageUI.HandleCommand(
                        new Newtonsoft.Json.Linq.JObject
                        {
                            ["action"] = "detach_ui_document",
                            ["target"] = document.GetInstanceIDCompat().ToString(),
                            ["component_type"] = "ui_document",
                        }
                    )
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(document == first, Cache.ContainsKey(item.id));
            }
            Assert.IsTrue(item.owned == null);
            Assert.AreSame(item.prior, item.panel.targetTexture);
        }

        [Test]
        public void DestroyedBorrowedPanelPrunesOnlyItsOwnedTarget()
        {
            var item = Seed();
            UnityEngine.Object.DestroyImmediate(item.panel);
            PruneCache();
            Assert.IsFalse(Cache.ContainsKey(item.id));
            Assert.IsFalse(Bindings.ContainsKey(item.id));
            Assert.IsTrue(item.owned == null);
            Assert.IsTrue(item.prior != null);
            PruneCache();
            Assert.IsTrue(item.prior != null);
        }

        [Test]
        public void DestroyedOwnedTargetPrunesSlotAndPreservesExternalBinding()
        {
            var item = Seed();
            var external = new RenderTexture(8, 8, 0);
            objects.Add(external);
            item.panel.targetTexture = external;
            UnityEngine.Object.DestroyImmediate(item.owned);
            PruneCache();
            Assert.IsFalse(Cache.ContainsKey(item.id));
            Assert.IsFalse(Bindings.ContainsKey(item.id));
            Assert.AreSame(external, item.panel.targetTexture);
            Assert.IsTrue(item.panel != null && item.prior != null && external != null);
        }

        [Test]
        public void EightLivePanelCachesRemainAndStillRejectNinthPanel()
        {
            var items = new List<(int id, PanelSettings panel, RenderTexture owned, RenderTexture prior)>();
            for (int i = 0; i < 8; i++)
                items.Add(Seed());
            var next = ScriptableObject.CreateInstance<PanelSettings>();
            objects.Add(next);
            PruneCache();
            var exception = Assert.Throws<TargetInvocationException>(() =>
                typeof(ManageUI).GetMethod("ValidateUICacheBudget", PrivateStatic).Invoke(null, new object[] { next.GetInstanceIDCompat(), 16, 16 })
            );
            StringAssert.Contains("at most 8 panels", exception.InnerException.Message);
            foreach (var item in items)
            {
                Assert.AreSame(item.owned, Cache[item.id]);
                Assert.AreSame(item.owned, item.panel.targetTexture);
                Assert.IsTrue(item.panel != null && item.owned != null && item.prior != null);
            }
        }

        [Test]
        public void ReplacementLiveBindingIsPreservedAfterOldPanelDestruction()
        {
            var item = Seed();
            UnityEngine.Object.DestroyImmediate(item.panel);
            var replacement = ScriptableObject.CreateInstance<PanelSettings>();
            var replacementPrior = new RenderTexture(4, 4, 0);
            objects.Add(replacement);
            objects.Add(replacementPrior);
            replacement.targetTexture = item.owned;
            Bindings[item.id] = (replacement, replacementPrior);
            PruneCache();
            Assert.AreSame(item.owned, Cache[item.id]);
            Assert.AreSame(replacement, Bindings[item.id].panel);
            Assert.AreSame(item.owned, replacement.targetTexture);
            Assert.IsTrue(item.owned != null && item.prior != null && replacementPrior != null);
        }

        [Test]
        public void FailedCaptureRestoresBindingAndKeepsBorrowedPanelCache()
        {
            var item = Seed();
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, false);
            Assert.AreSame(item.prior, item.panel.targetTexture);
            Assert.AreSame(item.owned, Cache[item.id]);
            Assert.IsTrue(item.owned != null);
            Assert.AreSame(originalActive, RenderTexture.active);
        }

        [Test]
        public void SuccessfulWarmupKeepsOwnedTargetAttached()
        {
            var item = Seed();
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, true, false);
            Assert.AreSame(item.owned, item.panel.targetTexture);
            Assert.AreSame(item.owned, Cache[item.id]);
        }

        [Test]
        public void TransientPanelReleasesOnlyOwnedCacheEvenDuringWarmup()
        {
            var item = Seed();
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, true, true);
            Assert.AreSame(item.prior, item.panel.targetTexture);
            Assert.IsFalse(Cache.ContainsKey(item.id));
            Assert.IsFalse(Bindings.ContainsKey(item.id));
            Assert.IsTrue(item.owned == null);
            Assert.IsTrue(item.panel != null && item.prior != null);
        }

        [Test]
        public void ExternalTargetReassignmentIsPreservedDuringRelease()
        {
            var item = Seed();
            var external = new RenderTexture(8, 8, 0);
            objects.Add(external);
            item.panel.targetTexture = external;
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.AreSame(external, item.panel.targetTexture);
            Assert.IsTrue(external != null && item.prior != null);
            Assert.IsFalse(Cache.ContainsKey(item.id));
        }

        [Test]
        public void ReplacedCacheEntryIsNotReleasedByOldCapture()
        {
            var item = Seed();
            var replacement = new RenderTexture(8, 8, 0);
            objects.Add(replacement);
            Cache[item.id] = replacement;
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.AreSame(replacement, Cache[item.id]);
            Assert.IsTrue(Bindings.ContainsKey(item.id));
            Assert.AreSame(item.owned, item.panel.targetTexture);
            Assert.IsTrue(item.owned != null && replacement != null);
        }

        [Test]
        public void DestroyedTransientPanelStillReleasesOwnedCache()
        {
            var item = Seed();
            UnityEngine.Object.DestroyImmediate(item.panel);
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.IsFalse(Cache.ContainsKey(item.id));
            Assert.IsFalse(Bindings.ContainsKey(item.id));
            Assert.IsTrue(item.owned == null);
            Assert.IsTrue(item.prior != null);
        }

        [Test]
        public void ExternalNullIsPreservedAfterOwnedTargetWasDestroyed()
        {
            var item = Seed();
            UnityEngine.Object.DestroyImmediate(item.owned);
            item.panel.targetTexture = null;
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.IsNull(item.panel.targetTexture);
            Assert.IsFalse(Cache.ContainsKey(item.id));
        }

        [Test]
        public void DifferentDestroyedTargetIsNotMistakenForOwnedWrapper()
        {
            var item = Seed();
            var external = new RenderTexture(8, 8, 0);
            objects.Add(external);
            item.panel.targetTexture = external;
            UnityEngine.Object.DestroyImmediate(item.owned);
            UnityEngine.Object.DestroyImmediate(external);
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.IsFalse(ReferenceEquals(item.panel.targetTexture, item.prior));
            Assert.IsFalse(Cache.ContainsKey(item.id));
        }

        [Test]
        public void DestroyedOwnedTargetStillRemovesMatchingTransientEntry()
        {
            var item = Seed();
            UnityEngine.Object.DestroyImmediate(item.owned);
            ManageUI.FinishPanelRender(item.id, item.panel, item.owned, false, true);
            Assert.IsFalse(Cache.ContainsKey(item.id));
            Assert.IsFalse(Bindings.ContainsKey(item.id));
            Assert.IsTrue(item.prior != null && item.panel != null);
            Assert.AreSame(item.prior, item.panel.targetTexture);
        }
    }
}
