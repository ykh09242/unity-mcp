using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
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
