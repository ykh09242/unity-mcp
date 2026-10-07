using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    public class UIToolIntegrityTests
    {
        private string assetRoot;
        private string rootGuid;
        private bool ownsRoot;
        private readonly List<UnityEngine.Object> allocated = new();

        [SetUp]
        public void SetUp()
        {
            ownsRoot = false;
            assetRoot = null;
            rootGuid = null;
            allocated.Clear();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned Prefab Stage is open.");

            assetRoot = "Assets/__McpUIToolIntegrity_" + Guid.NewGuid().ToString("N");
            string physicalRoot = Path.Combine(Application.dataPath, Path.GetFileName(assetRoot));
            Assert.IsFalse(File.Exists(physicalRoot) || Directory.Exists(physicalRoot)
                || File.Exists(physicalRoot + ".meta") || AssetDatabase.IsValidFolder(assetRoot), "Owned root collision.");
            rootGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(assetRoot));
            Assert.IsNotEmpty(rootGuid);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(rootGuid));
            ownsRoot = true;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (UnityEngine.Object item in allocated)
                if (item != null && !AssetDatabase.Contains(item)) UnityEngine.Object.DestroyImmediate(item);
            allocated.Clear();
            if (!ownsRoot) return;
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(rootGuid), "Owned root identity changed; retain for diagnosis.");
            Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
            Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Failed to delete exact owned root.");
            ownsRoot = false;
        }

        [TestCase("referenceResolution")]
        [TestCase("colorClearValue")]
        public void CreateMalformedCompositeLeavesNoAssetOrNestedFolder(string property)
        {
            string path = assetRoot + "/Rejected/Panel.asset";
            var response = Send("create_panel_settings", path, new JObject
            {
                ["sortingOrder"] = 3,
                [property] = BadComposite(property)
            });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, Path.GetFileName(assetRoot), "Rejected")));
        }

        [Test]
        public void CreateMalformedLegacyResolutionLeavesNoAssetOrNestedFolder()
        {
            string path = assetRoot + "/Rejected/Panel.asset";
            var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "create_panel_settings", ["path"] = path,
                ["scale_mode"] = "ScaleWithScreenSize",
                ["reference_resolution"] = new JObject { ["width"] = 64, ["height"] = "bad" }
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, Path.GetFileName(assetRoot), "Rejected")));
        }

        [TestCase("referenceResolution")]
        [TestCase("colorClearValue")]
        public void UpdateMalformedLateCompositePreservesExistingState(string property)
        {
            string path = assetRoot + "/Panel.asset";
            PanelSettings panel = CreateOwnedPanel(path);
            panel.sortingOrder = 7;
            panel.clearColor = true;
            panel.referenceResolution = new Vector2Int(64, 32);
            EditorUtility.SetDirty(panel);
            AssetDatabase.SaveAssets();
            JObject before = Snapshot(panel);
            string guid = AssetDatabase.AssetPathToGUID(path);
            bool dirty = EditorUtility.IsDirty(panel);

            var response = Send("update_panel_settings", path, new JObject
            {
                ["sortingOrder"] = 3, ["clearColor"] = false,
                [property] = BadComposite(property)
            });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(panel)), "Panel changed after predictable rejection.");
            Assert.AreSame(panel, AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(panel));
        }

        [TestCase("create_panel_settings")]
        [TestCase("update_panel_settings")]
        public void RepeatedCompositeAliasesPreserveSequentialDefaults(string action)
        {
            string path = assetRoot + "/Panel.asset";
            if (action == "update_panel_settings") CreateOwnedPanel(path);
            var response = Send(action, path, new JObject
            {
                ["referenceResolution"] = new JObject { ["width"] = 64 },
                ["reference_resolution"] = new JObject { ["height"] = 32 },
                ["dynamicAtlasSettings"] = new JObject { ["minAtlasSize"] = 64 },
                ["dynamic_atlas_settings"] = new JObject { ["maxAtlasSize"] = 128 }
            });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            PanelSettings panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            Assert.IsNotNull(panel);
            Assert.AreEqual(new Vector2Int(64, 32), panel.referenceResolution);
            Assert.AreEqual(64, panel.dynamicAtlasSettings.minAtlasSize);
            Assert.AreEqual(128, panel.dynamicAtlasSettings.maxAtlasSize);
            CollectionAssert.AreEqual(new[] { "referenceResolution", "referenceResolution", "dynamicAtlasSettings", "dynamicAtlasSettings" },
                response["data"]["applied"].ToObject<string[]>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreatePreservesAnyOccupiedAssetAndGuid(bool panelAsset)
        {
            string path = assetRoot + "/Occupied.asset";
            UnityEngine.Object occupied;
            if (panelAsset) occupied = CreateOwnedPanel(path);
            else
            {
                occupied = new Texture2D(1, 1);
                allocated.Add(occupied);
                AssetDatabase.CreateAsset(occupied, path);
                AssetDatabase.SaveAssets();
            }
            Assert.IsTrue(AssetDatabase.Contains(occupied), "Collision setup must be persistent.");
            Assert.AreSame(occupied, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.IsNotEmpty(guid);

            var response = Send("create_panel_settings", path, new JObject { ["sortingOrder"] = 3 });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(occupied, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsTrue(AssetDatabase.Contains(occupied));
        }

        [Test]
        public void CreatePreservesUnimportedPhysicalFile()
        {
            string path = assetRoot + "/Unimported.asset";
            string physicalPath = Path.Combine(Application.dataPath, Path.GetFileName(assetRoot), "Unimported.asset");
            byte[] original = System.Text.Encoding.UTF8.GetBytes("unrelated unimported bytes");
            Assert.IsFalse(File.Exists(physicalPath) || File.Exists(physicalPath + ".meta"));
            File.WriteAllBytes(physicalPath, original);

            var response = Send("create_panel_settings", path, new JObject { ["sortingOrder"] = 3 });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(original, File.ReadAllBytes(physicalPath));
            Assert.IsFalse(File.Exists(physicalPath + ".meta"), "Rejected request must not import the unowned content.");
        }

        [Test]
        public void MetadataOnlyPathCanCreateNewPanel()
        {
            string path = assetRoot + "/MetadataOnly.asset";
            string physicalPath = Path.Combine(Application.dataPath, Path.GetFileName(assetRoot), "MetadataOnly.asset");
            Assert.IsFalse(File.Exists(physicalPath) || File.Exists(physicalPath + ".meta"));
            File.WriteAllText(physicalPath + ".meta", "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N")
                + "\nNativeFormatImporter:\n  externalObjects: {}\n  mainObjectFileID: 0\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n");

            var response = Send("create_panel_settings", path, new JObject { ["sortingOrder"] = 0 });

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            PanelSettings panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            Assert.IsNotNull(panel);
            Assert.IsTrue(AssetDatabase.Contains(panel));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(panel));
        }

        [Test]
        public void DefaultHelperNeverReturnsBorrowedPersistentAsset()
        {
            string path = assetRoot + "/Panel.asset";
            PanelSettings panel = CreateOwnedPanel(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var helper = typeof(ManageUI).GetMethod("CreateDefaultPanelSettings", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(helper);

            using var folders = new AssetFolderScope();
            object result = helper.Invoke(null, new object[] { path, folders, null });

            Assert.IsNull(result, "RenderUI cleanup must never receive a borrowed asset.");
            Assert.AreSame(panel, AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void CreateFileWriteFailurePreservesPreexistingFolderAndContent()
        {
            string parent = assetRoot + "/Existing";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Existing"));
            string parentGuid = AssetDatabase.AssetPathToGUID(parent);
            string path = parent + "/Occupied.uss";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(parent, "Occupied.uss"));
            string occupiedGuid = AssetDatabase.AssetPathToGUID(path);
            string sentinel = Path.Combine(Application.dataPath, path.Substring("Assets/".Length), "Retained.txt");
            File.WriteAllText(sentinel, "retain occupied output");

            var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "create", ["path"] = path, ["contents"] = "Label { color: red; }"
            }));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(parentGuid, AssetDatabase.AssetPathToGUID(parent));
            Assert.AreEqual(occupiedGuid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual("retain occupied output", File.ReadAllText(sentinel));
        }

        [Test]
        public void ValidCreatePersistsConfiguredZeroAndFalseValues()
        {
            string path = assetRoot + "/Panel.asset";
            var response = Send("create_panel_settings", path, new JObject
            {
                ["sortingOrder"] = 0, ["clearColor"] = false,
                ["referenceResolution"] = new JObject { ["width"] = 64, ["height"] = 32 }
            });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            Assert.IsNotNull(panel);
            Assert.IsTrue(AssetDatabase.Contains(panel));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(panel));
            Assert.AreEqual(0, panel.sortingOrder);
            Assert.IsFalse(panel.clearColor);
            Assert.AreEqual(new Vector2Int(64, 32), panel.referenceResolution);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ListRejectsMissingOrFileScopeWithoutReturningOutsideAssets(bool fileScope)
        {
            string panelPath = assetRoot + "/Panel.asset";
            CreateOwnedPanel(panelPath);
            var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "list", ["path"] = fileScope ? panelPath : assetRoot + "/Missing",
                ["filter_type"] = "PanelSettings"
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.That(response.Value<string>("error"), Does.Contain("folder"));
        }

        [Test]
        public void ListKeepsExistingFolderScopeAndPageBoundaries()
        {
            CreateOwnedPanel(assetRoot + "/Outside.asset");
            string scoped = assetRoot + "/Scoped";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Scoped"));
            CreateOwnedPanel(scoped + "/First.asset");
            CreateOwnedPanel(scoped + "/Second.asset");
            var paths = new HashSet<string>();
            for (int page = 1; page <= 3; page++)
            {
                var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
                {
                    ["action"] = "list", ["path"] = scoped, ["filterType"] = "PanelSettings",
                    ["pageSize"] = 1, ["pageNumber"] = page
                }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(2, response["data"].Value<int>("total"));
                var assets = (JArray)response["data"]["assets"];
                Assert.AreEqual(page < 3 ? 1 : 0, assets.Count);
                foreach (JToken asset in assets)
                {
                    string path = asset.Value<string>("path");
                    Assert.That(path, Does.StartWith(scoped + "/"));
                    Assert.IsTrue(paths.Add(path), "Page returned a duplicate.");
                }
            }
            Assert.AreEqual(2, paths.Count);
        }

        [TestCase("style", "string")]
        [TestCase("inline_style", "object")]
        [TestCase("inlineStyle", "null")]
        public void MalformedLateNumericStylePreservesEveryEarlierElementMutation(string alias, string badType)
        {
            var go = new GameObject("__McpUIElement_" + Guid.NewGuid().ToString("N"));
            allocated.Add(go);
            var document = go.AddComponent<UIDocument>();
            Assert.IsNotNull(document.rootVisualElement);
            var label = new Label("original") { name = "owned-label", tooltip = "before" };
            label.style.width = 12;
            label.style.height = 13;
            label.AddToClassList("keep");
            document.rootVisualElement.Add(label);
            JToken invalid = badType == "object" ? (JToken)new JObject { ["x"] = 1 }
                : badType == "null" ? JValue.CreateNull() : new JValue("bad");

            var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "modify_visual_element", ["target"] = go.name,
                ["element_name"] = label.name, ["text"] = "changed",
                ["add_classes"] = new JArray("new"), ["remove_classes"] = new JArray("keep"),
                [alias] = new JObject { ["width"] = 64, ["height"] = invalid },
                ["enabled"] = false, ["visible"] = false, ["tooltip"] = "after"
            }));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("original", label.text);
            CollectionAssert.AreEquivalent(new[] { "keep" }, label.GetClasses());
            Assert.AreEqual(12f, label.style.width.value.value);
            Assert.AreEqual(13f, label.style.height.value.value);
            Assert.IsTrue(label.enabledSelf);
            Assert.AreEqual("before", label.tooltip);
        }

        [Test]
        public void ValidMixedElementMutationPreservesAliasesSkipsAndApplicationOrder()
        {
            var go = new GameObject("__McpUIElement_" + Guid.NewGuid().ToString("N"));
            allocated.Add(go);
            var document = go.AddComponent<UIDocument>();
            Assert.IsNotNull(document.rootVisualElement);
            var label = new Label("original") { name = "owned-label" };
            document.rootVisualElement.Add(label);
            var response = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "modify_visual_element", ["target"] = go.name,
                ["elementName"] = label.name, ["text"] = "", ["addClasses"] = new JArray("new"),
                ["inlineStyle"] = new JObject
                {
                    ["width"] = 64, ["HEIGHT"] = "32", ["border-radius"] = 4,
                    ["display"] = "None", ["unsupported"] = 1, ["color"] = "not-a-color"
                },
                ["enabled"] = false, ["visible"] = true, ["tooltip"] = ""
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("", label.text);
            Assert.IsTrue(label.ClassListContains("new"));
            Assert.AreEqual(64f, label.style.width.value.value);
            Assert.AreEqual(32f, label.style.height.value.value);
            Assert.AreEqual(4f, label.style.borderBottomRightRadius.value.value);
            Assert.AreEqual(DisplayStyle.Flex, label.style.display.value);
            Assert.IsFalse(label.enabledSelf);
            Assert.AreEqual("", label.tooltip);
            Assert.AreEqual(1, ((JArray)response["data"]["skipped"]).Count);
            CollectionAssert.AreEqual(new[] { "text=''", "+class 'new'", "width=64", "height=32", "borderRadius=4",
                "display=None", "enabled=False", "visible=True", "tooltip=''" },
                response["data"]["modifications"].ToObject<string[]>());
        }

        private PanelSettings CreateOwnedPanel(string path)
        {
            PanelSettings panel = ScriptableObject.CreateInstance<PanelSettings>();
            allocated.Add(panel);
            AssetDatabase.CreateAsset(panel, path);
            AssetDatabase.SaveAssets();
            Assert.IsTrue(AssetDatabase.Contains(panel));
            Assert.AreSame(panel, AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            return panel;
        }

        private static JObject Send(string action, string path, JObject settings) =>
            JObject.FromObject(ManageUI.HandleCommand(new JObject { ["action"] = action, ["path"] = path, ["settings"] = settings }));

        private static JObject BadComposite(string property) => property == "referenceResolution"
            ? new JObject { ["width"] = 64, ["height"] = "bad" }
            : new JObject { ["r"] = 0, ["g"] = "bad" };

        private static JObject Snapshot(PanelSettings panel) => JObject.FromObject(new
        {
            panel.scaleMode, panel.screenMatchMode, panel.match, panel.referenceDpi, panel.fallbackDpi,
            panel.sortingOrder, panel.targetDisplay, panel.clearColor, panel.clearDepthStencil,
            resolution = new[] { panel.referenceResolution.x, panel.referenceResolution.y },
            color = new[] { panel.colorClearValue.r, panel.colorClearValue.g, panel.colorClearValue.b, panel.colorClearValue.a },
            atlas = new { panel.dynamicAtlasSettings.minAtlasSize, panel.dynamicAtlasSettings.maxAtlasSize,
                panel.dynamicAtlasSettings.maxSubTextureSize, panel.dynamicAtlasSettings.activeFilters }
        });
    }
}
