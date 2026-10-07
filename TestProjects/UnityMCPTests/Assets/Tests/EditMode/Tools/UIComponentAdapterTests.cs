using System;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    // A contract fake, not a substitute for Unity 6.5 runtime validation.
    public sealed class UIAdapterTestPanel : MonoBehaviour
    {
        public delegate void ReloadCallback(UIAdapterTestPanel renderer, VisualElement root, int version);
        public VisualTreeAsset visualTreeAsset { get; set; }
        public PanelSettings panelSettings { get; set; }
        public int sortingOrder { get; set; }
        public VisualElement CurrentRoot;
        public int Registrations;
        public bool ThrowOnRegister;

        public void RegisterUIReloadCallback(ReloadCallback callback)
        {
            Registrations++;
            if (ThrowOnRegister)
                throw new InvalidOperationException("registration failed");
            if (CurrentRoot != null)
                callback(this, CurrentRoot, 1);
        }

        public void UnregisterUIReloadCallback(ReloadCallback callback)
        {
            Registrations--;
        }
    }

    public class UIComponentAdapterTests
    {
        private GameObject _go;

        [SetUp]
        public void SetUp()
        {
            _go = new GameObject("UIAdapterTest_" + Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            UnityEngine.Object.DestroyImmediate(_go);
        }

        [Test]
        public void Auto_PreservesExistingUIDocument()
        {
            var document = _go.AddComponent<UIDocument>();
            Assert.AreEqual(typeof(UIDocument), UIComponentAdapter.ResolveType(_go, "auto", true));
            Assert.AreSame(document, UIComponentAdapter.Resolve(_go, null).Component);
        }

        [Test]
        public void Auto_NewAttachmentPrefersAvailableRenderer()
        {
            Assert.AreEqual(UIComponentAdapter.PanelRendererType ?? typeof(UIDocument), UIComponentAdapter.ResolveType(_go, "auto", true));
        }

        [Test]
        public void ExplicitUIDocument_DoesNotRequirePanelRenderer()
        {
            Assert.AreEqual(typeof(UIDocument), UIComponentAdapter.ResolveType(_go, "ui_document", true));
        }

        [Test]
        public void UnavailableRenderer_ReturnsActionableErrorWithoutAddingAComponent()
        {
            if (UIComponentAdapter.PanelRendererType != null)
                Assert.Ignore("This editor has PanelRenderer.");
            var result = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_visual_tree",
                        ["target"] = _go.name,
                        ["componentType"] = "panel_renderer",
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.That(result.Value<string>("error"), Does.Contain("unavailable"));
            Assert.AreEqual(1, _go.GetComponents<Component>().Length);
        }

        [TestCase("get_visual_tree")]
        [TestCase("detach_ui_document")]
        [TestCase("modify_visual_element")]
        [TestCase("render_ui")]
        public void InvalidSelector_IsRejectedAcrossComponentActions(string action)
        {
            var result = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = _go.name,
                        ["componentType"] = "unknown",
                        ["elementName"] = "label",
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.That(result.Value<string>("error"), Does.Contain("component_type"));
        }

        [Test]
        public void ReloadCallback_ReturnsCurrentRootAndAlwaysUnregisters()
        {
            var fake = _go.AddComponent<UIAdapterTestPanel>();
            UIComponentAdapter.ValidatePanelContract(typeof(UIAdapterTestPanel));
            var adapter = new UIComponentAdapter(fake);
            Assert.IsNull(adapter.Root);
            Assert.AreEqual(0, fake.Registrations);
            fake.CurrentRoot = new VisualElement();
            Assert.AreSame(fake.CurrentRoot, adapter.Root);
            Assert.AreEqual(0, fake.Registrations);
            var replacement = new VisualElement();
            fake.CurrentRoot = replacement;
            Assert.AreSame(replacement, adapter.Root, "Root must not be cached across UI reloads.");
            Assert.AreEqual(0, fake.Registrations);
            fake.ThrowOnRegister = true;
            Assert.Throws<TargetInvocationException>(() =>
            {
                var root = adapter.Root;
            });
            Assert.AreEqual(0, fake.Registrations);
        }

        [Test]
        public void ReflectionConfiguration_AssignsAssetsSettingsAndSortOrder()
        {
            var fake = _go.AddComponent<UIAdapterTestPanel>();
            var source = ScriptableObject.CreateInstance<VisualTreeAsset>();
            var settings = ScriptableObject.CreateInstance<PanelSettings>();
            try
            {
                var adapter = new UIComponentAdapter(fake);
                adapter.Configure(source, settings, 17);
                Assert.AreSame(source, adapter.SourceAsset);
                Assert.AreSame(settings, adapter.PanelSettings);
                Assert.AreEqual(17, fake.sortingOrder);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
                UnityEngine.Object.DestroyImmediate(settings);
            }
        }

        [Test]
        public void UnsupportedReflectionContract_FailsBeforeUse()
        {
            var error = Assert.Throws<NotSupportedException>(() => UIComponentAdapter.ValidatePanelContract(typeof(Transform)));
            Assert.That(error.Message, Does.Contain("visualTreeAsset"));
        }
    }
}
