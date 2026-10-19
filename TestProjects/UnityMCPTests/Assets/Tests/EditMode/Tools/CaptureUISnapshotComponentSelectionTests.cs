using System;
using System.Collections;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Tools
{
    public class CaptureUISnapshotComponentSelectionTests
    {
        private sealed class ComponentWindow : EditorWindow { }

        private GameObject target;
        private PanelSettings settings;
        private ComponentWindow window;

        [SetUp]
        public void SetUp()
        {
            target = new GameObject("UI13_" + Guid.NewGuid().ToString("N"));
            settings = ScriptableObject.CreateInstance<PanelSettings>();
            settings.themeStyleSheet = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>("Assets/UI Toolkit/UnityThemes/UnityDefaultRuntimeTheme.tss");
            Assert.That(settings.themeStyleSheet, Is.Not.Null, "The owned preseeded native theme is required.");
        }

        [TearDown]
        public void TearDown()
        {
            if (window != null)
                window.Close();
            if (target != null)
                UnityEngine.Object.DestroyImmediate(target);
            if (settings != null)
                UnityEngine.Object.DestroyImmediate(settings);
        }

        private VisualElement DocumentRoot()
        {
            var document = target.AddComponent<UIDocument>();
            document.panelSettings = settings;
            var root = document.rootVisualElement;
            root.Clear();
            root.Add(new Label("Document marker") { name = "DocumentOnly" });
            root.Add(new TextField { name = "Input", value = "UI13_PRIVATE_INPUT" });
            return root;
        }

        private void Show(params VisualElement[] roots)
        {
            window = ScriptableObject.CreateInstance<ComponentWindow>();
            window.position = new Rect(0, 0, 480, 480);
            window.Show();
            foreach (var root in roots)
            {
                root.style.width = 320;
                root.style.height = 180;
                window.rootVisualElement.Add(root);
            }
        }

        private JObject Capture(string componentType = null, bool mapped = false, int limit = 200)
        {
            var request = new JObject
            {
                ["framework"] = "uitoolkit",
                ["target"] = target.GetInstanceIDCompat(),
                ["max_nodes"] = limit,
            };
            if (componentType != null)
                request["component_type"] = componentType;
            if (mapped)
                request["identity_map"] = new JArray(new JObject { ["element_path"] = "/0", ["id"] = "document-label" });
            return JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
        }

        [UnityTest]
        public IEnumerator ExplicitDocumentAndAutoReuseTheLiveRootWithoutChangingSources()
        {
            // Given
            var root = DocumentRoot();
            Show(root);
            yield return null;
            yield return null;
            Assert.That(root.panel, Is.Not.Null);
            Assert.That(root.worldBound.width, Is.GreaterThan(0));
            var components = target.GetComponents<Component>();
            var sources = components.Select(component => EditorJsonUtility.ToJson(component)).ToArray();
            var dirty = components.Select(component => EditorUtility.IsDirty(component)).ToArray();
            bool sceneDirty = target.scene.isDirty;
            var selection = Selection.objects;
            // When
            var legacy = Capture();
            var automatic = Capture("auto");
            var explicitDocument = Capture("ui_document");
            // Then
            foreach (var response in new[] { legacy, automatic, explicitDocument })
            {
                Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
                Assert.That(response.ToString(), Does.Not.Contain("UI13_PRIVATE_INPUT"));
                Assert.That(response["data"]["nodes"].Any(node => node["text"].Value<string>() == "Document marker"), Is.True);
                Assert.That(
                    response["data"]["nodes"][0]["geometry"]["bounds"].ToObject<float[]>(),
                    Is.EqualTo(new[] { root.worldBound.x, root.worldBound.y, root.worldBound.width, root.worldBound.height })
                );
            }
            Assert.That(legacy["data"]["evidence"]["component_type"], Is.Null);
            Assert.That(automatic["data"]["evidence"]["component_type"].Value<string>(), Is.EqualTo("ui_document"));
            Assert.That(JToken.DeepEquals(automatic["data"], explicitDocument["data"]), Is.True);
            ((JObject)explicitDocument["data"]["evidence"]).Remove("component_type");
            Assert.That(JToken.DeepEquals(legacy["data"], explicitDocument["data"]), Is.True);
            Assert.That(target.GetComponents<Component>(), Is.EqualTo(components));
            Assert.That(components.Select(component => EditorJsonUtility.ToJson(component)).ToArray(), Is.EqualTo(sources));
            Assert.That(components.Select(component => EditorUtility.IsDirty(component)).ToArray(), Is.EqualTo(dirty));
            Assert.That(target.scene.isDirty, Is.EqualTo(sceneDirty));
            Assert.That(Selection.objects, Is.EqualTo(selection));
        }

        [UnityTest]
        public IEnumerator ExplicitDocumentRetainsMappedPrivacyAndOverflowRefusal()
        {
            // Given
            var root = DocumentRoot();
            Show(root);
            yield return null;
            yield return null;
            // When
            var mapped = Capture("ui_document", true);
            var overflow = Capture("ui_document", false, 1);
            // Then
            Assert.That(mapped["success"].Value<bool>(), Is.True, mapped.ToString());
            Assert.That(mapped["data"]["schema_version"].Value<int>(), Is.EqualTo(3));
            Assert.That(mapped["data"]["evidence"]["component_type"].Value<string>(), Is.EqualTo("ui_document"));
            Assert.That(mapped["data"]["nodes"][1]["id"].Value<string>(), Does.EndWith("/@id:document-label"));
            Assert.That(mapped.ToString(), Does.Not.Contain("UI13_PRIVATE_INPUT"));
            Assert.That(overflow["success"].Value<bool>(), Is.False);
        }

        [TestCase("auto")]
        [TestCase("ui_document")]
        [TestCase("panel_renderer")]
        public void MissingSelectedComponentRefusesWithoutCreation(string componentType)
        {
            // Given
            var components = target.GetComponents<Component>();
            // When
            var response = Capture(componentType);
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(target.GetComponents<Component>(), Is.EqualTo(components));
        }

        [Test]
        public void MissingRendererNeverFallsBackToAnExistingDocument()
        {
            // Given
            DocumentRoot();
            var components = target.GetComponents<Component>();
            // When
            var response = Capture("panel_renderer");
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["error"].Value<string>(), Does.Contain("PanelRenderer"));
            Assert.That(target.GetComponents<Component>(), Is.EqualTo(components));
        }

        [TestCase("null")]
        [TestCase("true")]
        [TestCase("1")]
        [TestCase("\"UIDocument\"")]
        [TestCase("\"unknown\"")]
        public void InvalidSelectorRefusesBeforeTargetResolution(string json)
        {
            // Given
            var request = new JObject
            {
                ["framework"] = "uitoolkit",
                ["target"] = new JObject(),
                ["component_type"] = JToken.Parse(json),
            };
            // When
            var response = JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["error"].Value<string>(), Does.Contain("component_type"));
        }

        [Test]
        public void UgUISelectorRefusesBeforeTargetResolution()
        {
            // Given
            var request = new JObject
            {
                ["framework"] = "ugui",
                ["target"] = new JObject(),
                ["component_type"] = "auto",
            };
            // When
            var response = JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["error"].Value<string>(), Does.Contain("component_type"));
        }

        [UnityTest]
        public IEnumerator ActualPanelRendererSelectsItsOwnRootWhenInstalledContractSupportsColocation()
        {
            // Given: this test uses only Unity's real installed type, never a contract fake.
            var rendererType = UIComponentAdapter.PanelRendererType;
            if (rendererType == null)
                Assert.Ignore("Actual PanelRenderer is unavailable in this installed Unity editor.");
            try
            {
                UIComponentAdapter.ValidatePanelContract(rendererType);
            }
            catch (NotSupportedException exception)
            {
                Assert.Ignore("Actual installed PanelRenderer contract is unsupported: " + exception.Message);
            }
            var documentRoot = DocumentRoot();
            var renderer = target.AddComponent(rendererType);
            Assert.That(renderer, Is.Not.Null, "A supported installed renderer must attach for native coverage.");
            rendererType.GetProperty("panelSettings").SetValue(renderer, settings);
            var rendererRoot = new UIComponentAdapter(renderer).Root;
            Assert.That(rendererRoot, Is.Not.Null, "Supported renderer must synchronously expose its current root.");
            Assert.That(rendererRoot, Is.Not.SameAs(documentRoot));
            rendererRoot.Clear();
            rendererRoot.Add(new Label("Renderer marker") { name = "RendererOnly" });
            Show(documentRoot, rendererRoot);
            yield return null;
            yield return null;
            Assert.That(rendererRoot.worldBound.width, Is.GreaterThan(0));
            var sources = target.GetComponents<Component>().Select(component => EditorJsonUtility.ToJson(component)).ToArray();
            var dirty = target.GetComponents<Component>().Select(component => EditorUtility.IsDirty(component)).ToArray();
            bool sceneDirty = target.scene.isDirty;
            var selection = Selection.objects;
            var rendererBounds = rendererRoot.worldBound;
            // When
            var automatic = Capture("auto");
            var selected = Capture("panel_renderer");
            // Then
            Assert.That(automatic["success"].Value<bool>(), Is.True, automatic.ToString());
            Assert.That(selected["success"].Value<bool>(), Is.True, selected.ToString());
            Assert.That(automatic["data"]["evidence"]["component_type"].Value<string>(), Is.EqualTo("ui_document"));
            Assert.That(selected["data"]["evidence"]["component_type"].Value<string>(), Is.EqualTo("panel_renderer"));
            Assert.That(automatic["data"]["nodes"].Any(node => node["text"].Value<string>() == "Document marker"), Is.True);
            Assert.That(selected["data"]["nodes"].Any(node => node["text"].Value<string>() == "Renderer marker"), Is.True);
            Assert.That(selected["data"]["nodes"].Any(node => node["text"].Value<string>() == "Document marker"), Is.False);
            Assert.That(target.GetComponents<Component>().Select(component => EditorJsonUtility.ToJson(component)).ToArray(), Is.EqualTo(sources));
            Assert.That(
                selected["data"]["nodes"][0]["geometry"]["bounds"].ToObject<float[]>(),
                Is.EqualTo(new[] { rendererBounds.x, rendererBounds.y, rendererBounds.width, rendererBounds.height })
            );
            Assert.That(target.GetComponents<Component>().Select(component => EditorUtility.IsDirty(component)).ToArray(), Is.EqualTo(dirty));
            Assert.That(target.scene.isDirty, Is.EqualTo(sceneDirty));
            Assert.That(Selection.objects, Is.EqualTo(selection));
            // The installed public contract exposes the current root synchronously, not a receiver inventory.
            // Re-reading checks current-root identity; it cannot establish internal callback registration counts.
            Assert.That(new UIComponentAdapter(renderer).Root, Is.SameAs(rendererRoot));
        }
    }
}
