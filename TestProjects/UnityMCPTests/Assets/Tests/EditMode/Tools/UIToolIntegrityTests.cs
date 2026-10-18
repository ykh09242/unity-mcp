using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Xml;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
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
            Assert.IsFalse(
                File.Exists(physicalRoot) || Directory.Exists(physicalRoot) || File.Exists(physicalRoot + ".meta") || AssetDatabase.IsValidFolder(assetRoot),
                "Owned root collision."
            );
            rootGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(assetRoot));
            Assert.IsNotEmpty(rootGuid);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(rootGuid));
            ownsRoot = true;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (UnityEngine.Object item in allocated)
                if (item != null && !AssetDatabase.Contains(item))
                    UnityEngine.Object.DestroyImmediate(item);
            allocated.Clear();
            if (!ownsRoot)
                return;
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(rootGuid), "Owned root identity changed; retain for diagnosis.");
            Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
            Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Failed to delete exact owned root.");
            ownsRoot = false;
        }

        private static string DecodeUIText(JObject parameters)
        {
            var decode = typeof(ManageUI).GetMethod("GetDecodedContents", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(decode);
            return (string)decode.Invoke(null, new object[] { new ToolParams(parameters) });
        }

        [TestCase(false, "true")]
        [TestCase(false, "1")]
        [TestCase(false, "[1]")]
        [TestCase(false, "{x:1}")]
        [TestCase(true, "true")]
        [TestCase(true, "1234")]
        [TestCase(true, "[1]")]
        [TestCase(true, "{x:1}")]
        public void ConsumedUITextRejectsNonStringTokensBeforeFileWork(bool encoded, string json)
        {
            var request = new JObject { ["contentsEncoded"] = encoded, [encoded ? "encodedContents" : "contents"] = JToken.Parse(json) };
            var error = Assert.Throws<TargetInvocationException>(() => DecodeUIText(request));
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            StringAssert.Contains("must be a string", error.InnerException.Message);
        }

        [TestCase("null")]
        [TestCase("''")]
        [TestCase("'text'")]
        [TestCase("'2026-10-09T12:00:00Z'")]
        public void UITextRetainsStringDateAndNullConversion(string json)
        {
            var request = JObject.Parse("{contents:" + json + "}");
            Assert.AreEqual(new ToolParams(request).Get("contents"), DecodeUIText(request));
        }

        [Test]
        public void EncodedUITextPreservesSelectedAliasAndIgnoresUnusedContents()
        {
            var request = new JObject
            {
                ["contents_encoded"] = false,
                ["contentsEncoded"] = true,
                ["encoded_contents"] = "Lg==",
                ["contents"] = new JObject(),
            };
            Assert.AreEqual(".", DecodeUIText(request));
            request["contents_encoded"] = false;
            request["contentsEncoded"] = false;
            request["contents"] = ".foo{}";
            request["encoded_contents"] = new JObject();
            Assert.AreEqual(".foo{}", DecodeUIText(request));
        }

        [TestCase("<ui:UXML xmlns:ui='UnityEngine.UIElements' name=\"a>b\"></ui:UXML>")]
        [TestCase("<ui:UXML xmlns:ui='UnityEngine.UIElements' name='a>b'></ui:UXML>")]
        [TestCase("<!-- <ui:UXML> --><ui:UXML xmlns:ui='UnityEngine.UIElements'/>")]
        [TestCase("<!-- editor-extension-mode --><ui:UXML xmlns:ui='UnityEngine.UIElements'/>")]
        [TestCase("<?probe <ui:UXML> ?><ui:UXML xmlns:ui='UnityEngine.UIElements'/>")]
        [TestCase("<ui:UXML xmlns:ui='UnityEngine.UIElements'><ui:Label editor-extension-mode='True'/></ui:UXML>")]
        [TestCase("<?xml version='1.0'?>\r\n<UXML name='a>b'/>")]
        public void UxmlModeInsertionTargetsActualRootAndKeepsXmlWellFormed(string contents)
        {
            var ensure = typeof(ManageUI).GetMethod("EnsureEditorExtensionMode", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(ensure);
            string result = (string)ensure.Invoke(null, new object[] { contents });
            var document = new XmlDocument { XmlResolver = null };
            Assert.DoesNotThrow(() => document.LoadXml(result));
            Assert.AreEqual("False", document.DocumentElement.GetAttribute("editor-extension-mode"));
            if (document.DocumentElement.HasAttribute("name"))
                Assert.AreEqual("a>b", document.DocumentElement.GetAttribute("name"));
        }

        [Test]
        public void ExistingRootModeRetainsOriginalText()
        {
            var ensure = typeof(ManageUI).GetMethod("EnsureEditorExtensionMode", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(ensure);
            foreach (string mode in new[] { "True", "" })
            {
                string contents = "<UXML editor-extension-mode='" + mode + "'/>";
                Assert.AreEqual(contents, ensure.Invoke(null, new object[] { contents }));
            }
        }

        private static object ParseUxmlInfo(string contents, string stylesheet)
        {
            var parse = typeof(ManageUI).GetMethod("ReadUxmlContent", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(parse);
            object[] args = { contents, new List<string>(), null, stylesheet };
            Assert.IsNull(parse.Invoke(null, args));
            Assert.IsNotNull(args[2]);
            return args[2];
        }

        [TestCase("<UXML><Style src=\"project://database/Assets/A&amp;B.uss\"/></UXML>", true)]
        [TestCase("<UXML><Style src='project://database/Assets/A&amp;B.uss'/></UXML>", true)]
        [TestCase("<UXML xmlns='UnityEngine.UIElements'><Style src='Assets/A&amp;B.uss'/></UXML>", true)]
        [TestCase("<!-- src=\"project://database/Assets/A&amp;B.uss\" --><UXML/>", false)]
        [TestCase("<UXML><Group><Style src='project://database/Assets/A&amp;B.uss'/></Group></UXML>", false)]
        [TestCase("<UXML xmlns:other='urn:other'><other:Style src='project://database/Assets/A&amp;B.uss'/></UXML>", false)]
        [TestCase("<UXML><Label src='project://database/Assets/A&amp;B.uss'/></UXML>", false)]
        public void StylesheetRecognitionUsesActualDirectStyleElements(string contents, bool expected)
        {
            object info = ParseUxmlInfo(contents, "Assets/A&B.uss");
            Assert.AreEqual(expected, info.GetType().GetField("StylesheetLinked").GetValue(info));
        }

        [TestCase("<UXML xmlns='UnityEngine.UIElements'></UXML>", "Style")]
        [TestCase("<ui:UXML xmlns:ui='UnityEngine.UIElements'></ui:UXML>", "ui:Style")]
        public void StyleInsertionPreservesRealNamespaceBindings(string contents, string expectedTag)
        {
            object info = ParseUxmlInfo(contents, "Assets/A&B.uss");
            string tagName = (string)info.GetType().GetField("StyleTagName").GetValue(info);
            Assert.AreEqual(expectedTag, tagName);
            var create = typeof(ManageUI).GetMethod("CreateStylesheetTag", BindingFlags.Static | BindingFlags.NonPublic);
            var find = typeof(ManageUI).GetMethod("FindUxmlBodyStart", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(create);
            Assert.IsNotNull(find);
            string tag = (string)create.Invoke(null, new object[] { "Assets/A&B.uss", tagName });
            int index = (int)find.Invoke(null, new object[] { contents });
            Assert.Greater(index, 0);
            var document = new XmlDocument { XmlResolver = null };
            Assert.DoesNotThrow(() => document.LoadXml(contents.Insert(index, tag)));
            var style = (XmlElement)document.DocumentElement.FirstChild;
            Assert.AreEqual("project://database/Assets/A&B.uss", style.GetAttribute("src"));
        }

        [TestCase("QQ==")]
        [TestCase("/w==")]
        [TestCase("7aCA")]
        [TestCase("7ZWc6riA8J+YgA==")]
        public void ReadUITextPreservesUtf8ReplacementAndEncodedResponse(string sourceBase64)
        {
            string path = assetRoot + "/Unimported.uss";
            byte[] source = Convert.FromBase64String(sourceBase64);
            File.WriteAllBytes(AssetPathUtility.GetFullAssetPath(path), source);
            string expected = System.Text.Encoding.UTF8.GetString(source);
            byte[] encoded = System.Text.Encoding.UTF8.GetBytes(expected);
            JObject response = JObject.FromObject(ManageUI.HandleCommand(new JObject { ["action"] = "read", ["path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"));
            var data = (JObject)response["data"];
            Assert.AreEqual(expected, data.Value<string>("contents"));
            Assert.AreEqual(Convert.ToBase64String(encoded), data.Value<string>("encodedContents"));
            Assert.AreEqual(encoded.Length, data.Value<int>("lengthBytes"));
            Assert.IsTrue(data.Value<bool>("contentsEncoded"));
        }

        private static Action<PanelSettings> PreparePanelProperties(JObject settings, List<string> changes)
        {
            var prepare = typeof(ManageUI).GetMethod("PreparePanelSettingsProperties", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(prepare);
            Assert.AreEqual(2, prepare.GetParameters().Length, "Preparation must not require an allocated PanelSettings target.");
            return (Action<PanelSettings>)prepare.Invoke(null, new object[] { settings, changes });
        }

        [TestCase("clearColor", "{}")]
        [TestCase("referenceResolution", "{width:64,height:'bad'}")]
        public void PanelPropertiesRejectMalformedInputsWithoutAllocatingTarget(string key, string json)
        {
            var prepare = typeof(ManageUI).GetMethod("PreparePanelSettingsProperties", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(prepare);
            Assert.AreEqual(2, prepare.GetParameters().Length);
            int count = UnityEngine.Resources.FindObjectsOfTypeAll<PanelSettings>().Length;
            var error = Assert.Throws<TargetInvocationException>(() =>
                PreparePanelProperties(new JObject { ["sortingOrder"] = 3, [key] = JToken.Parse(json) }, new List<string>())
            );
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            Assert.AreEqual(count, UnityEngine.Resources.FindObjectsOfTypeAll<PanelSettings>().Length);
        }

        [Test]
        public void PreparedPanelPropertiesReadEachTargetsDefaultsAndPreserveOrderedAliases()
        {
            var changes = new List<string>();
            var apply = PreparePanelProperties(
                new JObject
                {
                    ["referenceResolution"] = new JObject { ["width"] = 64 },
                    ["reference_resolution"] = new JObject { ["height"] = JValue.CreateNull() },
                    ["dynamicAtlasSettings"] = new JObject { ["minAtlasSize"] = 64 },
                    ["dynamic_atlas_settings"] = new JObject { ["maxAtlasSize"] = 128 },
                    ["futureUnknownKey"] = "ignored",
                },
                changes
            );
            foreach (int height in new[] { 32, 48 })
            {
                var panel = ScriptableObject.CreateInstance<PanelSettings>();
                allocated.Add(panel);
                panel.referenceResolution = new Vector2Int(1920, height);
                apply(panel);
                Assert.AreEqual(new Vector2Int(64, height), panel.referenceResolution);
                Assert.AreEqual(64, panel.dynamicAtlasSettings.minAtlasSize);
                Assert.AreEqual(128, panel.dynamicAtlasSettings.maxAtlasSize);
            }
            CollectionAssert.AreEqual(new[] { "referenceResolution", "referenceResolution", "dynamicAtlasSettings", "dynamicAtlasSettings" }, changes);
        }

        [TestCase("referenceResolution")]
        [TestCase("colorClearValue")]
        public void CreateMalformedCompositeLeavesNoAssetOrNestedFolder(string property)
        {
            string path = assetRoot + "/Rejected/Panel.asset";
            var response = Send("create_panel_settings", path, new JObject { ["sortingOrder"] = 3, [property] = BadComposite(property) });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, Path.GetFileName(assetRoot), "Rejected")));
        }

        [TestCase("[]")]
        [TestCase("[1]")]
        [TestCase("true")]
        [TestCase("false")]
        [TestCase("0")]
        [TestCase("''")]
        [TestCase("'{}'")]
        public void CreateNonObjectSettingsLeavesNoAssetOrNestedFolder(string json)
        {
            string path = assetRoot + "/Rejected/Panel.asset";
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_panel_settings",
                        ["path"] = path,
                        ["settings"] = JToken.Parse(json),
                        ["scale_mode"] = "ScaleWithScreenSize",
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'settings' must be a JSON object", response.Value<string>("error"));
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.IsFalse(File.Exists(AssetPathUtility.GetFullAssetPath(path)));
            Assert.IsFalse(Directory.Exists(AssetPathUtility.GetFullAssetPath(assetRoot + "/Rejected")));
            Assert.IsFalse(File.Exists(AssetPathUtility.GetFullAssetPath(assetRoot + "/Rejected") + ".meta"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreateNullOrOmittedSettingsKeepsLegacyConfiguration(bool explicitNull)
        {
            string path = assetRoot + "/Panel.asset";
            var request = new JObject
            {
                ["action"] = "create_panel_settings",
                ["path"] = path,
                ["scaleMode"] = "ScaleWithScreenSize",
                ["referenceResolution"] = new JObject { ["width"] = 64, ["height"] = 32 },
            };
            if (explicitNull)
                request["settings"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            PanelSettings panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            Assert.IsNotNull(panel);
            Assert.AreEqual(PanelScaleMode.ScaleWithScreenSize, panel.scaleMode);
            Assert.AreEqual(new Vector2Int(64, 32), panel.referenceResolution);
            CollectionAssert.AreEqual(new[] { "scaleMode", "referenceResolution" }, response["data"]["applied"].ToObject<string[]>());
        }

        [TestCase("{}")]
        [TestCase("{unknown_key:1}")]
        public void CreateEmptyOrUnknownSettingsKeepsObjectPrecedenceOverLegacy(string json)
        {
            string path = assetRoot + "/Panel.asset";
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_panel_settings",
                        ["path"] = path,
                        ["settings"] = JObject.Parse(json),
                        ["reference_resolution"] = new JObject { ["height"] = "invalid legacy value" },
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<PanelSettings>(path));
            Assert.IsEmpty(response["data"]["applied"].ToObject<string[]>());
        }

        [Test]
        public void CreateMalformedLegacyResolutionLeavesNoAssetOrNestedFolder()
        {
            string path = assetRoot + "/Rejected/Panel.asset";
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_panel_settings",
                        ["path"] = path,
                        ["scale_mode"] = "ScaleWithScreenSize",
                        ["reference_resolution"] = new JObject { ["width"] = 64, ["height"] = "bad" },
                    }
                )
            );
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

            var response = Send(
                "update_panel_settings",
                path,
                new JObject
                {
                    ["sortingOrder"] = 3,
                    ["clearColor"] = false,
                    [property] = BadComposite(property),
                }
            );

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
            if (action == "update_panel_settings")
                CreateOwnedPanel(path);
            var response = Send(
                action,
                path,
                new JObject
                {
                    ["referenceResolution"] = new JObject { ["width"] = 64 },
                    ["reference_resolution"] = new JObject { ["height"] = 32 },
                    ["dynamicAtlasSettings"] = new JObject { ["minAtlasSize"] = 64 },
                    ["dynamic_atlas_settings"] = new JObject { ["maxAtlasSize"] = 128 },
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            PanelSettings panel = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            Assert.IsNotNull(panel);
            Assert.AreEqual(new Vector2Int(64, 32), panel.referenceResolution);
            Assert.AreEqual(64, panel.dynamicAtlasSettings.minAtlasSize);
            Assert.AreEqual(128, panel.dynamicAtlasSettings.maxAtlasSize);
            CollectionAssert.AreEqual(
                new[] { "referenceResolution", "referenceResolution", "dynamicAtlasSettings", "dynamicAtlasSettings" },
                response["data"]["applied"].ToObject<string[]>()
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreatePreservesAnyOccupiedAssetAndGuid(bool panelAsset)
        {
            string path = assetRoot + "/Occupied.asset";
            UnityEngine.Object occupied;
            if (panelAsset)
                occupied = CreateOwnedPanel(path);
            else
            {
                occupied = new Texture2D(1, 1);
                allocated.Add(occupied);
                AssetDatabase.CreateAsset(occupied, path);
                AssetDatabase.SaveAssets();
            }
            Assert.IsTrue(AssetDatabase.Contains(occupied), "Collision setup must be persistent.");
            var loadedBefore = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            Assert.IsTrue(occupied == loadedBefore, "Collision setup must resolve the same native asset.");
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(loadedBefore));
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.IsNotEmpty(guid);

            var response = Send("create_panel_settings", path, new JObject { ["sortingOrder"] = 3 });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            var loadedAfter = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            Assert.IsTrue(loadedBefore == loadedAfter, "Rejected creation must preserve the native asset.");
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(loadedAfter));
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
            File.WriteAllText(
                physicalPath + ".meta",
                "fileFormatVersion: 2\nguid: "
                    + Guid.NewGuid().ToString("N")
                    + "\nNativeFormatImporter:\n  externalObjects: {}\n  mainObjectFileID: 0\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
            );

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

            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["path"] = path,
                        ["contents"] = "Label { color: red; }",
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(parentGuid, AssetDatabase.AssetPathToGUID(parent));
            Assert.AreEqual(occupiedGuid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual("retain occupied output", File.ReadAllText(sentinel));
        }

        [Test]
        public void ValidCreatePersistsConfiguredZeroAndFalseValues()
        {
            string path = assetRoot + "/Panel.asset";
            var response = Send(
                "create_panel_settings",
                path,
                new JObject
                {
                    ["sortingOrder"] = 0,
                    ["clearColor"] = false,
                    ["referenceResolution"] = new JObject { ["width"] = 64, ["height"] = 32 },
                }
            );
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
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "list",
                        ["path"] = fileScope ? panelPath : assetRoot + "/Missing",
                        ["filter_type"] = "PanelSettings",
                    }
                )
            );
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
                var response = JObject.FromObject(
                    ManageUI.HandleCommand(
                        new JObject
                        {
                            ["action"] = "list",
                            ["path"] = scoped,
                            ["filterType"] = "PanelSettings",
                            ["pageSize"] = 1,
                            ["pageNumber"] = page,
                        }
                    )
                );
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
        [TestCase("inlineStyle", "array")]
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
            var originalClasses = new List<string>(label.GetClasses());
            JToken invalid =
                badType == "object" ? (JToken)new JObject { ["x"] = 1 }
                : badType == "array" ? new JArray(1)
                : new JValue("bad");

            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = go.name,
                        ["element_name"] = label.name,
                        ["text"] = "changed",
                        ["add_classes"] = new JArray("new"),
                        ["remove_classes"] = new JArray("keep"),
                        [alias] = new JObject { ["width"] = 64, ["height"] = invalid },
                        ["enabled"] = false,
                        ["visible"] = false,
                        ["tooltip"] = "after",
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("original", label.text);
            CollectionAssert.AreEquivalent(originalClasses, label.GetClasses());
            Assert.AreEqual(12f, label.style.width.value.value);
            Assert.AreEqual(13f, label.style.height.value.value);
            Assert.IsTrue(label.enabledSelf);
            Assert.AreEqual("before", label.tooltip);
        }

        [Test]
        public void NullNumericStyleRetainsAcceptedZeroDefault()
        {
            var go = new GameObject("__McpUIElement_" + Guid.NewGuid().ToString("N"));
            allocated.Add(go);
            var document = go.AddComponent<UIDocument>();
            var label = new Label("original") { name = "owned-label" };
            label.style.height = 13;
            document.rootVisualElement.Add(label);
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = go.name,
                        ["element_name"] = label.name,
                        ["text"] = "changed",
                        ["inlineStyle"] = new JObject { ["width"] = 64, ["height"] = JValue.CreateNull() },
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("changed", label.text);
            Assert.AreEqual(64f, label.style.width.value.value);
            Assert.AreEqual(0f, label.style.height.value.value);
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
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = go.name,
                        ["elementName"] = label.name,
                        ["text"] = "",
                        ["addClasses"] = new JArray("new"),
                        ["inlineStyle"] = new JObject
                        {
                            ["width"] = 64,
                            ["HEIGHT"] = "32",
                            ["border-radius"] = 4,
                            ["display"] = "None",
                            ["unsupported"] = 1,
                            ["color"] = "not-a-color",
                        },
                        ["enabled"] = false,
                        ["visible"] = true,
                        ["tooltip"] = "",
                    }
                )
            );
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
            CollectionAssert.AreEqual(
                new[] { "text=''", "+class 'new'", "width=64", "height=32", "borderRadius=4", "display=None", "enabled=False", "visible=True", "tooltip=''" },
                response["data"]["modifications"].ToObject<string[]>()
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InlineStyleRejectionsAreCompleteAndStrictPreflightProtectsEveryMutation(bool strict)
        {
            var go = InlineStyleLabel(out var label);
            var response = InlineStyleRequest(
                go,
                label,
                new JObject
                {
                    ["width"] = new JObject { ["value"] = 50, ["unit"] = "%" },
                    ["color"] = "private-invalid-color",
                    ["display"] = "private-invalid-enum",
                    ["paddingLeft"] = new JObject { ["keyword"] = "auto" },
                    ["unknown"] = new JObject { ["private"] = "do-not-echo" },
                },
                strict,
                false
            );
            Assert.AreEqual(!strict, response.Value<bool>("success"), response.ToString());
            var report = response["data"]["style_results"];
            Assert.AreEqual(1, report.Value<int>("schema_version"));
            Assert.AreEqual(strict ? 0 : 1, ((JArray)report["applied"]).Count);
            Assert.AreEqual(strict ? 5 : 4, ((JArray)report["rejected"]).Count);
            Assert.That(report.ToString(), Does.Not.Contain("do-not-echo"));
            Assert.That(report.ToString(), Does.Not.Contain("private-invalid"));
            if (strict)
            {
                Assert.AreEqual("original", label.text);
                Assert.IsTrue(label.ClassListContains("keep"));
                Assert.IsFalse(label.ClassListContains("new"));
                Assert.AreEqual(12f, label.style.width.value.value);
                Assert.IsTrue(label.enabledSelf);
                Assert.AreEqual("before", label.tooltip);
                Assert.AreEqual(DisplayStyle.Flex, label.style.display.value);
            }
            else
            {
                Assert.AreEqual("changed", label.text);
                Assert.AreEqual(LengthUnit.Percent, label.style.width.value.unit);
                Assert.AreEqual(50f, label.style.width.value.value);
                Assert.AreEqual(1, ((JArray)response["data"]["skipped"]).Count);
            }
        }

        [Test]
        public void TypedInlineLengthsKeepUnitsAndOnlyPropertySpecificAuto()
        {
            var go = InlineStyleLabel(out var label);
            var response = InlineStyleRequest(
                go,
                label,
                new JObject
                {
                    ["width"] = new JObject { ["keyword"] = "auto" },
                    ["height"] = new JObject { ["value"] = 25, ["unit"] = "%" },
                    ["margin-left"] = new JObject { ["keyword"] = "auto" },
                    ["paddingRight"] = new JObject { ["value"] = 8, ["unit"] = "px" },
                    ["borderRadius"] = new JObject { ["value"] = 10, ["unit"] = "%" },
                    ["fontSize"] = new JObject { ["value"] = 20, ["unit"] = "%" },
                    ["opacity"] = new JObject { ["value"] = 0 },
                    ["backgroundColor"] = new JObject { ["value"] = "#FF0000" },
                    ["color"] = new JObject { ["value"] = "#0000FF" },
                    ["display"] = new JObject { ["value"] = "none" },
                    ["visibility"] = new JObject { ["value"] = "hidden" },
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(Color.red, label.style.backgroundColor.value);
            Assert.AreEqual(Color.blue, label.style.color.value);
            Assert.AreEqual(DisplayStyle.Flex, label.style.display.value);
            Assert.AreEqual(Visibility.Hidden, label.style.visibility.value);
            Assert.AreEqual(StyleKeyword.Auto, label.style.width.keyword);
            Assert.AreEqual(LengthUnit.Percent, label.style.height.value.unit);
            Assert.AreEqual(StyleKeyword.Auto, label.style.marginLeft.keyword);
            Assert.AreEqual(8f, label.style.paddingRight.value.value);
            Assert.AreEqual(LengthUnit.Percent, label.style.borderBottomRightRadius.value.unit);
            Assert.AreEqual(0f, label.style.opacity.value);
            Assert.AreEqual("unsupported_unit", response["data"]["style_results"]["rejected"][0].Value<string>("reason"));
        }

        [TestCase(false, "string")]
        [TestCase(true, "string")]
        [TestCase(false, "null")]
        [TestCase(true, "null")]
        [TestCase(false, "boolean")]
        [TestCase(true, "boolean")]
        public void MalformedTypedNumericStyleStillAbortsBeforeEveryMutation(bool strict, string kind)
        {
            var go = InlineStyleLabel(out var label);
            JToken invalid =
                kind == "null" ? JValue.CreateNull()
                : kind == "boolean" ? new JValue(true)
                : new JValue("not-a-number");
            var response = InlineStyleRequest(
                go,
                label,
                new JObject
                {
                    ["width"] = 64,
                    ["height"] = new JObject { ["value"] = invalid, ["unit"] = "px" },
                    ["opacity"] = 0,
                },
                strict
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("original", label.text);
            Assert.AreEqual(12f, label.style.width.value.value);
            Assert.AreEqual("before", label.tooltip);
            Assert.IsTrue(label.enabledSelf);
            Assert.AreEqual(0, ((JArray)response["data"]["style_results"]["applied"]).Count);
            Assert.AreEqual(3, ((JArray)response["data"]["style_results"]["rejected"]).Count);
            Assert.AreEqual("malformed_numeric", response["data"]["style_results"]["rejected"][1].Value<string>("reason"));
        }

        [Test]
        public void TypedNumericOverflowIsFatalBeforeMixedKeywordShapeRefusal()
        {
            var go = InlineStyleLabel(out var label);
            label.style.height = 13;
            var originalClasses = new List<string>(label.GetClasses());
            var response = InlineStyleRequest(
                go,
                label,
                new JObject
                {
                    ["width"] = 64,
                    ["height"] = new JObject { ["value"] = 1e100, ["keyword"] = "auto" },
                },
                false,
                false
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("original", label.text);
            CollectionAssert.AreEquivalent(originalClasses, label.GetClasses());
            Assert.AreEqual(12f, label.style.width.value.value);
            Assert.AreEqual(13f, label.style.height.value.value);
            Assert.IsTrue(label.enabledSelf);
            Assert.AreEqual(DisplayStyle.Flex, label.style.display.value);
            Assert.AreEqual("before", label.tooltip);
            var report = response["data"]["style_results"];
            Assert.AreEqual(0, ((JArray)report["applied"]).Count);
            var rejected = (JArray)report["rejected"];
            Assert.AreEqual(2, rejected.Count);
            Assert.AreEqual(0, rejected[0].Value<int>("index"));
            Assert.AreEqual("request_aborted_before_mutation", rejected[0].Value<string>("reason"));
            Assert.AreEqual(1, rejected[1].Value<int>("index"));
            Assert.AreEqual("height", rejected[1].Value<string>("property"));
            Assert.AreEqual("malformed_numeric", rejected[1].Value<string>("reason"));
        }

        [Test]
        public void InlineStyleReceiptsDistinguishAssignedValuesFromFinalInlineOverrides()
        {
            var go = InlineStyleLabel(out var label);
            var response = InlineStyleRequest(
                go,
                label,
                new JObject
                {
                    ["width"] = 20,
                    ["WIDTH"] = new JObject { ["value"] = 40, ["unit"] = "%" },
                    ["display"] = "None",
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var applied = (JArray)response["data"]["style_results"]["applied"];
            Assert.AreEqual(3, applied.Count);
            Assert.AreEqual(20f, applied[0]["assigned_value"].Value<float>("value"));
            Assert.AreEqual(40f, applied[0]["final_inline_value"].Value<float>("value"));
            Assert.AreEqual("%", applied[0]["final_inline_value"].Value<string>("unit"));
            Assert.AreEqual(1, applied[0]["superseded_by"].Value<int>("index"));
            Assert.AreEqual("None", applied[2].Value<string>("assigned_value"));
            Assert.AreEqual("Flex", applied[2].Value<string>("final_inline_value"));
            Assert.AreEqual("visible", applied[2]["superseded_by"].Value<string>("parameter"));
        }

        [TestCase(false, false, true)]
        [TestCase(true, false, false)]
        [TestCase(false, true, false)]
        public void UndefinedInlineEnumsRemainLabelledLegacyOnly(bool strict, bool typed, bool expected)
        {
            var go = InlineStyleLabel(out var label);
            JToken value = typed ? (JToken)new JObject { ["value"] = "999" } : new JValue("999");
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = go.name,
                        ["element_name"] = label.name,
                        ["strict_style"] = strict,
                        ["style"] = new JObject { ["display"] = value },
                    }
                )
            );
            Assert.AreEqual(expected, response.Value<bool>("success"), response.ToString());
            var report = response["data"]["style_results"];
            Assert.AreEqual(expected ? "legacy_undefined_enum" : "invalid_enum", report[expected ? "applied" : "rejected"][0].Value<string>("reason"));
        }

        [TestCase("entries")]
        [TestCase("key")]
        [TestCase("scalar")]
        public void InlineStyleBoundsRefuseBeforeMutation(string overflow)
        {
            var go = InlineStyleLabel(out var label);
            var styles = new JObject { ["width"] = 64 };
            if (overflow == "entries")
                for (int i = 0; i < 64; i++)
                    styles["unknown" + i] = i;
            else if (overflow == "key")
                styles[new string('k', 129)] = 1;
            else
                styles["color"] = new string('x', 257);
            var response = InlineStyleRequest(go, label, styles);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("inline_style_limit_exceeded", response.Value<string>("error"));
            Assert.AreEqual("original", label.text);
            Assert.AreEqual(12f, label.style.width.value.value);
        }

        [TestCase("true")]
        [TestCase("1")]
        [TestCase("null")]
        public void DirectNativeStrictStyleRequiresBooleanBeforeTargetResolution(string json)
        {
            var response = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = "missing-target",
                        ["element_name"] = "missing",
                        ["strict_style"] = JToken.Parse(json == "true" ? "\"true\"" : json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("strict_style must be a Boolean.", response.Value<string>("error"));
        }

        private GameObject InlineStyleLabel(out Label label)
        {
            var go = new GameObject("__McpInlineStyle_" + Guid.NewGuid().ToString("N"));
            allocated.Add(go);
            label = new Label("original") { name = "owned-style-label", tooltip = "before" };
            label.AddToClassList("keep");
            label.style.width = 12;
            label.style.display = DisplayStyle.Flex;
            go.AddComponent<UIDocument>().rootVisualElement.Add(label);
            return go;
        }

        private static JObject InlineStyleRequest(GameObject go, Label label, JObject styles, bool strict = false, bool visible = true)
        {
            return JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = go.name,
                        ["element_name"] = label.name,
                        ["text"] = "changed",
                        ["add_classes"] = new JArray("new"),
                        ["remove_classes"] = new JArray("keep"),
                        ["style"] = styles,
                        ["strict_style"] = strict,
                        ["enabled"] = false,
                        ["visible"] = visible,
                        ["tooltip"] = "after",
                    }
                )
            );
        }

        private GameObject MutationTree(out VisualElement root, out Label first, out Label second)
        {
            var go = new GameObject("__McpMutation_" + Guid.NewGuid().ToString("N"));
            allocated.Add(go);
            var document = go.AddComponent<UIDocument>();
            root = document.rootVisualElement;
            Assert.IsNotNull(root);
            root.Clear();
            var left = new VisualElement { name = "left/[%]" };
            var right = new VisualElement { name = "right" };
            first = new Label("first") { name = "shared", tooltip = "first-tip" };
            second = new Label("second") { name = "shared", tooltip = "second-tip" };
            first.style.width = 7;
            second.style.width = 8;
            left.Add(first);
            right.Add(second);
            root.Add(left);
            root.Add(right);
            return go;
        }

        private static JObject MutationRequest(GameObject go) =>
            new JObject
            {
                ["action"] = "modify_visual_element",
                ["target"] = go.GetInstanceIDCompat().ToString(),
                ["element_name"] = "shared",
                ["text"] = "changed",
                ["add_classes"] = new JArray("changed"),
                ["style"] = new JObject { ["width"] = 99 },
                ["enabled"] = false,
                ["tooltip"] = "changed",
            };

        private static string MutationSnapshotId(GameObject go, params VisualElement[] elements)
        {
            var segment = typeof(ManageUI)
                .Assembly.GetType("MCPForUnity.Editor.Tools.UitoolkitSnapshotCapture")
                .GetMethod("Segment", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(segment);
            string result = CaptureUISnapshot.Identity(go);
            foreach (VisualElement element in elements)
                result += "/" + (string)segment.Invoke(null, new object[] { element });
            return result;
        }

        private static void AssertMutationUntouched(Label first, Label second)
        {
            Assert.AreEqual("first", first.text);
            Assert.AreEqual("second", second.text);
            Assert.AreEqual(7, first.style.width.value.value);
            Assert.AreEqual(8, second.style.width.value.value);
            Assert.IsTrue(first.enabledSelf && second.enabledSelf);
            Assert.AreEqual("first-tip", first.tooltip);
            Assert.AreEqual("second-tip", second.tooltip);
            Assert.IsFalse(first.ClassListContains("changed") || second.ClassListContains("changed"));
        }

        [Test]
        public void MutationSelection_StrictDuplicateNamesRejectBeforeEveryMutation()
        {
            var go = MutationTree(out _, out var first, out var second);
            JObject request = MutationRequest(go);
            request["strict_name"] = true;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("ambiguous", response.Value<string>("error"));
            AssertMutationUntouched(first, second);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MutationSelection_LegacyFirstMatchRemainsAvailable(bool explicitFalse)
        {
            var go = MutationTree(out _, out var first, out var second);
            JObject request = MutationRequest(go);
            if (explicitFalse)
                request["strict_name"] = false;
            Assert.IsTrue(JObject.FromObject(ManageUI.HandleCommand(request)).Value<bool>("success"));
            Assert.AreEqual("changed", first.text);
            Assert.AreEqual("second", second.text);
        }

        [TestCase("element_path", true)]
        [TestCase("elementPath", false)]
        [TestCase("element_id", true)]
        [TestCase("elementId", false)]
        public void MutationSelection_ExplicitSelectorChangesExactlyOneDuplicate(string key, bool guard)
        {
            var go = MutationTree(out var root, out var first, out var second);
            JObject request = MutationRequest(go);
            if (!guard)
                request.Remove("element_name");
            request[key] = key.Contains("ath") ? "/1/0" : MutationSnapshotId(go, root.ElementAt(1), second);
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("first", first.text);
            Assert.AreEqual("changed", second.text);
            Assert.AreEqual(7, first.style.width.value.value);
            Assert.AreEqual(99, second.style.width.value.value);
            Assert.IsTrue(first.enabledSelf);
            Assert.IsFalse(second.enabledSelf);
        }

        [Test]
        public void MutationSelection_EscapedSnapshotIdUsesExistingEncoding()
        {
            var go = MutationTree(out var root, out var first, out var second);
            JObject request = MutationRequest(go);
            request["element_id"] = MutationSnapshotId(go, root.ElementAt(0), first);
            StringAssert.Contains("%2F", request.Value<string>("element_id"));
            Assert.IsTrue(JObject.FromObject(ManageUI.HandleCommand(request)).Value<bool>("success"));
            Assert.AreEqual("changed", first.text);
            Assert.AreEqual("second", second.text);
        }

        private sealed class MutationContentHost : VisualElement
        {
            internal readonly VisualElement Content = new VisualElement();
            internal readonly Label InternalLabel = new Label("internal") { name = "shared" };
            public override VisualElement contentContainer => Content;

            internal MutationContentHost()
            {
                hierarchy.Add(InternalLabel);
                hierarchy.Add(Content);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MutationSelection_ExplicitSelectorsUseSnapshotContentContainerOrder(bool semanticId)
        {
            var go = MutationTree(out var root, out _, out _);
            root.Clear();
            var host = new MutationContentHost { name = "host" };
            var label = new Label("content") { name = "shared" };
            host.Add(label);
            root.Add(host);
            Assert.AreSame(label, host.ElementAt(0));
            Assert.AreSame(host.InternalLabel, host.hierarchy.ElementAt(0));
            JObject request = MutationRequest(go);
            request[semanticId ? "element_id" : "element_path"] = semanticId ? MutationSnapshotId(go, host, label) : "/0/0";
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("internal", host.InternalLabel.text);
            Assert.AreEqual("changed", label.text);
        }

        [Test]
        public void MutationSelection_StrictNameIncludesInternalControlDuplicates()
        {
            var go = MutationTree(out var root, out _, out _);
            root.Clear();
            var host = new MutationContentHost { name = "host" };
            var label = new Label("content") { name = "shared" };
            host.Add(label);
            root.Add(host);
            Assert.AreSame(host.InternalLabel, root.Q("shared"), "Characterize the actual legacy UQuery candidate domain.");
            JObject request = MutationRequest(go);
            request["strict_name"] = true;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("ambiguous", response.Value<string>("error"));
            Assert.AreEqual("internal", host.InternalLabel.text);
            Assert.AreEqual("content", label.text);
            Assert.IsTrue(host.InternalLabel.enabledSelf && label.enabledSelf);
            Assert.IsFalse(host.InternalLabel.ClassListContains("changed") || label.ClassListContains("changed"));
        }

        [Test]
        public void MutationSelection_SemanticIdRejectsSyntheticForeignComponentRoot()
        {
            // Unity 6000.0 has no PanelRenderer. Two real owned UIDocuments exercise the
            // component/root guard synthetically, not a claimed dual-component integration.
            var go = MutationTree(out var root, out _, out var second);
            var other = MutationTree(out var otherRoot, out var otherFirst, out var otherSecond);
            var request = new ToolParams(new JObject { ["element_id"] = MutationSnapshotId(go, root.ElementAt(1), second) });
            var select = typeof(ManageUI).GetMethod("ResolveExplicitVisualElement", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(select);
            object[] args =
                select.GetParameters().Length == 4
                    ? new object[] { request, go, otherRoot, "shared" }
                    : new object[] { request, go, new UIComponentAdapter(other.GetComponent<UIDocument>()), otherRoot, "shared" };
            var error = Assert.Throws<TargetInvocationException>(() => select.Invoke(null, args));
            StringAssert.Contains("component", error.InnerException.Message);
            AssertMutationUntouched(otherFirst, otherSecond);
        }

        [Test]
        public void MutationSelection_StrictNameIncludesTheRootAsLegacyQDoes()
        {
            var go = MutationTree(out var root, out var first, out var second);
            root.name = "unique-root";
            Assert.AreSame(root, root.Q("unique-root"));
            JObject request = MutationRequest(go);
            request["element_name"] = root.name;
            request["strict_name"] = true;
            request.Remove("text");
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("changed", root.tooltip);
            AssertMutationUntouched(first, second);
        }

        [Test]
        public void MutationSelection_StrictUniqueNameBindsToNumericGameObjectIdentity()
        {
            var go = MutationTree(out _, out var first, out var second);
            first.name = "unique";
            var other = MutationTree(out _, out var otherFirst, out _);
            other.name = go.name;
            JObject request = MutationRequest(go);
            request["element_name"] = "unique";
            request["strictName"] = true;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("changed", first.text);
            Assert.AreEqual("second", second.text);
            Assert.AreEqual("first", otherFirst.text);
        }

        [Test]
        public void MutationSelection_EmptyPathSelectsRootByPresence()
        {
            var go = MutationTree(out var root, out var first, out var second);
            JObject request = MutationRequest(go);
            request.Remove("element_name");
            request.Remove("text");
            request["element_path"] = "";
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("changed", root.tooltip);
            AssertMutationUntouched(first, second);
        }

        [TestCase("element_path", "/01/0")]
        [TestCase("element_path", "/-1")]
        [TestCase("element_path", "/")]
        [TestCase("element_path", "/1/7")]
        [TestCase("element_path", "/1/0/")]
        [TestCase("element_path", null)]
        [TestCase("element_id", "")]
        [TestCase("element_id", "not-a-snapshot-id")]
        public void MutationSelection_InvalidExplicitSelectorNeverFallsBack(string key, string value)
        {
            var go = MutationTree(out _, out var first, out var second);
            JObject request = MutationRequest(go);
            request[key] = value;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            AssertMutationUntouched(first, second);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MutationSelection_StaleOrForeignSemanticIdNeverFallsBack(bool foreign)
        {
            var go = MutationTree(out var root, out var first, out var second);
            string identity = MutationSnapshotId(go, root.ElementAt(1), second);
            if (foreign)
            {
                var other = MutationTree(out var otherRoot, out _, out var otherSecond);
                identity = MutationSnapshotId(other, otherRoot.ElementAt(1), otherSecond);
            }
            else
                second.RemoveFromHierarchy();
            JObject request = MutationRequest(go);
            request["element_id"] = identity;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            AssertMutationUntouched(first, second);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MutationSelection_ConflictingSelectorOrNameGuardRejects(bool twoSelectors)
        {
            var go = MutationTree(out var root, out var first, out var second);
            JObject request = MutationRequest(go);
            request["element_path"] = "/1/0";
            if (twoSelectors)
                request["element_id"] = MutationSnapshotId(go, root.ElementAt(1), second);
            else
                request["element_name"] = "different-name";
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            AssertMutationUntouched(first, second);
        }

        [Test]
        public void MutationSelection_AmbiguousSemanticSiblingIdRejects()
        {
            var go = MutationTree(out var root, out var first, out var second);
            root.ElementAt(1).Add(new Label("third") { name = "shared" });
            JObject request = MutationRequest(go);
            request["element_id"] = MutationSnapshotId(go, root.ElementAt(1), second);
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("ambiguous", response.Value<string>("error"));
            AssertMutationUntouched(first, second);
        }

        [TestCase("depth")]
        [TestCase("siblings")]
        [TestCase("nodes")]
        public void MutationSelection_StrictScanBoundRejectsEvenAnEarlyMatch(string kind)
        {
            var go = MutationTree(out var root, out var first, out var second);
            first.name = "unique";
            second.name = "other";
            if (kind == "depth")
            {
                VisualElement parent = root;
                for (int i = 0; i < 129; i++)
                {
                    var child = new VisualElement();
                    parent.Add(child);
                    parent = child;
                }
            }
            else if (kind == "siblings")
                for (int i = 0; i < 1001; i++)
                    root.Add(new VisualElement());
            else
                for (int group = 0; group < 11; group++)
                {
                    var parent = new VisualElement();
                    root.Add(parent);
                    for (int i = 0; i < 999; i++)
                        parent.Add(new VisualElement());
                }
            JObject request = MutationRequest(go);
            request["element_name"] = "unique";
            request["strict_name"] = true;
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("bound", response.Value<string>("error"));
            AssertMutationUntouched(first, second);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MutationSelection_InactiveDocumentRejectsStrictOrExplicit(bool explicitPath)
        {
            var go = MutationTree(out _, out var first, out var second);
            JObject request = MutationRequest(go);
            if (explicitPath)
                request["element_path"] = "/1/0";
            else
                request["strict_name"] = true;
            go.SetActive(false);
            JObject response = JObject.FromObject(ManageUI.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            AssertMutationUntouched(first, second);
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
            JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = path,
                        ["settings"] = settings,
                    }
                )
            );

        private static JObject BadComposite(string property) =>
            property == "referenceResolution" ? new JObject { ["width"] = 64, ["height"] = "bad" } : new JObject { ["r"] = 0, ["g"] = "bad" };

        private static JObject Snapshot(PanelSettings panel) =>
            JObject.FromObject(
                new
                {
                    panel.scaleMode,
                    panel.screenMatchMode,
                    panel.match,
                    panel.referenceDpi,
                    panel.fallbackDpi,
                    panel.sortingOrder,
                    panel.targetDisplay,
                    panel.clearColor,
                    panel.clearDepthStencil,
                    resolution = new[] { panel.referenceResolution.x, panel.referenceResolution.y },
                    color = new[] { panel.colorClearValue.r, panel.colorClearValue.g, panel.colorClearValue.b, panel.colorClearValue.a },
                    atlas = new
                    {
                        panel.dynamicAtlasSettings.minAtlasSize,
                        panel.dynamicAtlasSettings.maxAtlasSize,
                        panel.dynamicAtlasSettings.maxSubTextureSize,
                        panel.dynamicAtlasSettings.activeFilters,
                    },
                }
            );
    }
}
