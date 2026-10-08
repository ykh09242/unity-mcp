using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class VfxGraphIntegrityTests
    {
        private GameObject _object;
        private Component _effect;

        [SetUp]
        public void SetUp()
        {
            _object = new GameObject("VfxGraphIntegrity_" + Guid.NewGuid().ToString("N"));
            Type type = Type.GetType("UnityEngine.VFX.VisualEffect, UnityEngine.VFXModule");
            if (type != null)
                _effect = _object.AddComponent(type);
        }

        [TearDown]
        public void TearDown()
        {
            if (_object != null)
                UnityEngine.Object.DestroyImmediate(_object);
        }

        private JObject Send(string action, JObject properties)
        {
            return JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = _object.GetInstanceIDCompat(),
                        ["search_method"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
        }

        private void RequireEnabledGraph()
        {
            JObject response = Send("vfx_integrity_unknown_action", new JObject());
            if (response["message"]?.ToString().Contains("not installed") == true)
                Assert.Ignore("Production UNITY_VFX_GRAPH/package branch is unavailable.");
            Assert.IsNotNull(_effect, "UnityEngine.VFX.VisualEffect is unavailable.");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Unknown vfx action", response["message"]?.ToString());
        }

        private T GetProperty<T>(string name) => (T)_effect.GetType().GetProperty(name).GetValue(_effect);

        [Test]
        public void PublicAvailabilityHasExplicitEnabledOrUnavailableResponse()
        {
            JObject response = Send("vfx_integrity_unknown_action", new JObject());
            if (response["message"]?.ToString().Contains("not installed") == true)
                Assert.IsFalse(response.Value<bool>("success"));
            else
            {
                Assert.IsNotNull(_effect);
                Assert.IsFalse(response.Value<bool>("success"));
                StringAssert.Contains("Unknown vfx action", response["message"]?.ToString());
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingTemplate_DoesNotCreateFoldersOrReplaceExistingFolder(bool existingFolder)
        {
            RequireEnabledGraph();
            string root = "Assets/VfxMissingTemplate_" + Guid.NewGuid().ToString("N");
            string folder = root + "/Nested";
            string originalGuid = null;
            if (existingFolder)
                originalGuid = UnityEditor.AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            try
            {
                var response = JObject.FromObject(
                    ManageVFX.HandleCommand(
                        new JObject
                        {
                            ["action"] = "vfx_create_asset",
                            ["assetName"] = "Missing",
                            ["folderPath"] = folder,
                            ["template"] = "Missing_" + Guid.NewGuid().ToString("N"),
                        }
                    )
                );

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("VFX template not found", response.Value<string>("message"));
                Assert.IsFalse(UnityEditor.AssetDatabase.IsValidFolder(folder));
                Assert.IsFalse(Directory.Exists(AssetPathUtility.GetFullAssetPath(folder)));
                Assert.IsFalse(File.Exists(AssetPathUtility.GetFullAssetPath(folder) + ".meta"));
                if (existingFolder)
                {
                    Assert.IsTrue(UnityEditor.AssetDatabase.IsValidFolder(root));
                    Assert.AreEqual(originalGuid, UnityEditor.AssetDatabase.AssetPathToGUID(root));
                }
                else
                {
                    Assert.IsFalse(UnityEditor.AssetDatabase.IsValidFolder(root));
                    Assert.IsFalse(Directory.Exists(AssetPathUtility.GetFullAssetPath(root)));
                    Assert.IsFalse(File.Exists(AssetPathUtility.GetFullAssetPath(root) + ".meta"));
                }
                UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                if (UnityEditor.AssetDatabase.IsValidFolder(root))
                    UnityEditor.AssetDatabase.DeleteAsset(root);
            }
        }

        [TestCase("name-forward")]
        [TestCase("name-backslash")]
        [TestCase("folder-traversal")]
        [TestCase("overwrite-string")]
        [TestCase("overwrite-number")]
        public void InvalidCreateInputRejectsBeforeTemplateLookupOrFolderCreation(string scenario)
        {
            RequireEnabledGraph();
            string root = "Assets/VfxInvalidCreate_" + Guid.NewGuid().ToString("N");
            string absoluteRoot = Path.Combine(Application.dataPath, Path.GetFileName(root));
            var request = new JObject
            {
                ["action"] = "vfx_create_asset",
                ["assetName"] = "New",
                ["folderPath"] = root + "/Nested",
                ["template"] = "Missing_" + Guid.NewGuid().ToString("N"),
            };
            string expected;
            if (scenario.StartsWith("name-", StringComparison.Ordinal))
            {
                request["assetName"] = scenario == "name-forward" ? "../Escaped" : "..\\Escaped";
                expected = "assetName";
            }
            else if (scenario == "folder-traversal")
            {
                request["folderPath"] = root + "/../../Escaped";
                expected = "folderPath";
            }
            else
            {
                request["overwrite"] = scenario == "overwrite-string" ? (JToken)new JValue("bad") : new JValue(1);
                expected = "overwrite";
            }

            JObject response = JObject.FromObject(ManageVFX.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(expected, response.Value<string>("message"), "invalid input must be rejected before template discovery");
            Assert.IsFalse(Directory.Exists(absoluteRoot));
            Assert.IsFalse(File.Exists(absoluteRoot + ".meta"));
            Assert.IsFalse(UnityEditor.AssetDatabase.IsValidFolder(root));
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingUnrelatedTargetIsPreservedBeforeTemplateLookup(bool directory)
        {
            RequireEnabledGraph();
            string root = "Assets/VfxCollision_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, Path.GetFileName(root))));
            Assert.IsEmpty(UnityEditor.AssetDatabase.AssetPathToGUID(root, UnityEditor.AssetPathToGUIDOptions.OnlyExistingAssets));
            string guid = UnityEditor.AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            Assert.IsNotEmpty(guid);
            try
            {
                string target = root + "/Target.vfx";
                string absolute = AssetPathUtility.GetFullAssetPath(target);
                if (directory)
                    Directory.CreateDirectory(absolute);
                else
                    File.WriteAllText(absolute, "unrelated bytes");
                JObject response = OverwriteVfx(root, target, "Missing_" + Guid.NewGuid().ToString("N"));
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("unrelated asset", response.Value<string>("message"));
                if (directory)
                    Assert.IsTrue(Directory.Exists(absolute));
                else
                    Assert.AreEqual("unrelated bytes", File.ReadAllText(absolute));
                UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                Assert.AreEqual(root, UnityEditor.AssetDatabase.GUIDToAssetPath(guid));
                Assert.IsTrue(UnityEditor.AssetDatabase.DeleteAsset(root));
            }
        }

        private static void WithExpectedMissingRenderPipelineWarning(Action createAssets)
        {
            const string warning =
                "The Visual Effect Graph is supported in the High Definition Render Pipeline (HDRP) and the Universal Render Pipeline (URP). Please assign your chosen Render Pipeline Asset in the Graphics Settings to use it.";
            Application.LogCallback expectWarning = (message, stack, kind) =>
            {
                if (kind == LogType.Warning && message == warning && UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline == null)
                    // Register only warnings actually emitted: cached templates can produce none.
                    UnityEngine.TestTools.LogAssert.Expect(LogType.Warning, warning);
            };
            Application.logMessageReceived += expectWarning;
            try
            {
                createAssets();
            }
            finally
            {
                Application.logMessageReceived -= expectWarning;
            }
        }

        private void WithOwnedVfxAssets(Action<string, string, string> check)
        {
            RequireEnabledGraph();
            string templatePath = null;
            WithExpectedMissingRenderPipelineWarning(() =>
            {
                var listed = JObject.FromObject(ManageVFX.HandleCommand(new JObject { ["action"] = "vfx_list_templates" }));
                templatePath = ((JArray)listed["data"]?["templates"])
                    ?.OfType<JObject>()
                    .Select(entry => entry.Value<string>("path"))
                    .FirstOrDefault(path => UnityEditor.AssetDatabase.LoadMainAssetAtPath(path)?.GetType().Name == "VisualEffectAsset");
            });
            if (templatePath == null)
                Assert.Ignore("A loadable existing VFX Graph template is required; the fixture does not install optional packages or templates.");

            string suffix = Guid.NewGuid().ToString("N");
            string root = "Assets/VfxOverwrite_" + suffix;
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, Path.GetFileName(root))));
            Assert.IsEmpty(UnityEditor.AssetDatabase.AssetPathToGUID(root, UnityEditor.AssetPathToGUIDOptions.OnlyExistingAssets));
            string guid = UnityEditor.AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            Assert.IsNotEmpty(guid);
            try
            {
                string source = root + "/Source_" + suffix + ".vfx";
                string target = root + "/Target_" + suffix + ".vfx";
                WithExpectedMissingRenderPipelineWarning(() =>
                {
                    Assert.IsTrue(UnityEditor.AssetDatabase.CopyAsset(templatePath, source));
                    Assert.IsTrue(UnityEditor.AssetDatabase.CopyAsset(templatePath, target));
                });
                check(root, source, target);
            }
            finally
            {
                Assert.AreEqual(root, UnityEditor.AssetDatabase.GUIDToAssetPath(guid));
                Assert.IsTrue(UnityEditor.AssetDatabase.DeleteAsset(root), "Delete only the exact owned VFX fixture root.");
            }
        }

        private static JObject OverwriteVfx(string root, string target, string template) =>
            JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = "vfx_create_asset",
                        ["folderPath"] = root,
                        ["assetName"] = Path.GetFileNameWithoutExtension(target),
                        ["template"] = Path.GetFileNameWithoutExtension(template),
                        ["overwrite"] = true,
                    }
                )
            );

        [Test]
        public void DestinationAsItsOwnTemplatePreservesContentsAndGuid()
        {
            WithOwnedVfxAssets(
                (root, source, target) =>
                {
                    string absolute = AssetPathUtility.GetFullAssetPath(target);
                    byte[] before = File.ReadAllBytes(absolute);
                    byte[] metadata = File.ReadAllBytes(absolute + ".meta");
                    string guid = UnityEditor.AssetDatabase.AssetPathToGUID(target);
                    JObject response = OverwriteVfx(root, target, target);
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(absolute));
                    CollectionAssert.AreEqual(metadata, File.ReadAllBytes(absolute + ".meta"));
                    Assert.AreEqual(guid, UnityEditor.AssetDatabase.AssetPathToGUID(target));
                    UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
                }
            );
        }

        [Test]
        public void CaseDistinctTemplateOverwritesDestinationOnCaseSensitiveFilesystems()
        {
            if (Path.DirectorySeparatorChar == '\\')
                Assert.Ignore("Windows path policy treats case-only names as the same file.");
            WithOwnedVfxAssets(
                (root, source, originalTarget) =>
                {
                    string target = root + "/" + Path.GetFileName(source).ToLowerInvariant();
                    string moveError = UnityEditor.AssetDatabase.MoveAsset(originalTarget, target);
                    if (!string.IsNullOrEmpty(moveError))
                        Assert.Ignore("The current AssetDatabase cannot represent case-distinct VFX assets: " + moveError);
                    string sourceGuid = UnityEditor.AssetDatabase.AssetPathToGUID(source);
                    string targetGuid = UnityEditor.AssetDatabase.AssetPathToGUID(target);
                    if (sourceGuid == targetGuid)
                        Assert.Ignore("The current AssetDatabase resolves case-only names to the same asset.");

                    string absoluteSource = AssetPathUtility.GetFullAssetPath(source);
                    string absoluteTarget = AssetPathUtility.GetFullAssetPath(target);
                    string sourceText = File.ReadAllText(absoluteSource);
                    if (!sourceText.StartsWith("%YAML", StringComparison.Ordinal))
                        Assert.Ignore("The case-distinct fixture requires an existing text YAML VFX template.");
                    var findTemplate = typeof(ManageVFX)
                        .Assembly.GetType("MCPForUnity.Editor.Tools.Vfx.VfxGraphAssets")
                        .GetMethod("FindTemplate", BindingFlags.NonPublic | BindingFlags.Static);
                    Assert.IsNotNull(findTemplate);
                    string selected = (string)findTemplate.Invoke(null, new object[] { Path.GetFileNameWithoutExtension(source) });
                    if (string.IsNullOrEmpty(selected) || !string.Equals(Path.GetFullPath(selected), absoluteSource, StringComparison.Ordinal))
                        Assert.Ignore("The current AssetDatabase template search did not select the distinct source spelling.");

                    File.AppendAllText(absoluteSource, "\n# case-distinct template " + Guid.NewGuid().ToString("N") + "\n");
                    UnityEditor.AssetDatabase.ImportAsset(source, UnityEditor.ImportAssetOptions.ForceSynchronousImport);
                    byte[] expected = File.ReadAllBytes(absoluteSource);
                    Assert.IsFalse(expected.SequenceEqual(File.ReadAllBytes(absoluteTarget)), "The template bytes must distinguish a real copy from a no-op.");
                    JObject response = OverwriteVfx(root, target, source);
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    CollectionAssert.AreEqual(expected, File.ReadAllBytes(absoluteTarget));
                    Assert.AreEqual(targetGuid, UnityEditor.AssetDatabase.AssetPathToGUID(target));
                    Assert.IsEmpty(Directory.GetFiles(AssetPathUtility.GetFullAssetPath(root), "__McpVfxOverwrite_*"));
                    UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
                }
            );
        }

        [Test]
        [Platform("Win")]
        public void FailedTemplateCopyPreservesDestinationAndCleansStaging()
        {
            WithOwnedVfxAssets(
                (root, source, target) =>
                {
                    string absoluteTarget = AssetPathUtility.GetFullAssetPath(target);
                    byte[] before = File.ReadAllBytes(absoluteTarget);
                    byte[] metadata = File.ReadAllBytes(absoluteTarget + ".meta");
                    string guid = UnityEditor.AssetDatabase.AssetPathToGUID(target);
                    var errors = new List<string>();
                    Application.LogCallback capture = (message, stack, kind) =>
                    {
                        if (kind == LogType.Error || kind == LogType.Exception || kind == LogType.Assert)
                            errors.Add(message);
                    };
                    bool previousIgnore = UnityEngine.TestTools.LogAssert.ignoreFailingMessages;
                    JObject response;
                    try
                    {
                        Application.logMessageReceived += capture;
                        // The native copy reports an expected sharing-violation error; inspect every captured error below.
                        UnityEngine.TestTools.LogAssert.ignoreFailingMessages = true;
                        using (var locked = new FileStream(AssetPathUtility.GetFullAssetPath(source), FileMode.Open, FileAccess.Read, FileShare.None))
                            response = OverwriteVfx(root, target, source);
                    }
                    finally
                    {
                        UnityEngine.TestTools.LogAssert.ignoreFailingMessages = previousIgnore;
                        Application.logMessageReceived -= capture;
                    }
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    foreach (string error in errors)
                        Assert.IsTrue(
                            error.Contains(Path.GetFileName(source)) || error.Contains("__McpVfxOverwrite_"),
                            "Only errors identifying the owned source or staging copy are expected: " + error
                        );
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(absoluteTarget));
                    CollectionAssert.AreEqual(metadata, File.ReadAllBytes(absoluteTarget + ".meta"));
                    Assert.AreEqual(guid, UnityEditor.AssetDatabase.AssetPathToGUID(target));
                    Assert.IsEmpty(Directory.GetFiles(AssetPathUtility.GetFullAssetPath(root), "__McpVfxOverwrite_*"));
                }
            );
        }

        [Test]
        [Platform("Win")]
        public void FailedOverwritePreservesDestinationAndCleansStaging()
        {
            WithOwnedVfxAssets(
                (root, source, target) =>
                {
                    string absolute = AssetPathUtility.GetFullAssetPath(target);
                    byte[] before = File.ReadAllBytes(absolute);
                    byte[] metadata = File.ReadAllBytes(absolute + ".meta");
                    string guid = UnityEditor.AssetDatabase.AssetPathToGUID(target);
                    JObject response;
                    using (var locked = new FileStream(absolute, FileMode.Open, FileAccess.Read, FileShare.Read))
                        response = OverwriteVfx(root, target, source);
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(absolute));
                    CollectionAssert.AreEqual(metadata, File.ReadAllBytes(absolute + ".meta"));
                    Assert.AreEqual(guid, UnityEditor.AssetDatabase.AssetPathToGUID(target));
                    Assert.IsEmpty(
                        Directory.GetFiles(AssetPathUtility.GetFullAssetPath(root), "__McpVfxOverwrite_*"),
                        "failed overwrite must remove its owned staging bytes and metadata"
                    );
                    UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
                }
            );
        }

        [Test]
        public void SuccessfulOverwritePreservesDestinationGuidAndCleansStaging()
        {
            WithOwnedVfxAssets(
                (root, source, target) =>
                {
                    string guid = UnityEditor.AssetDatabase.AssetPathToGUID(target);
                    byte[] expected = File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(source));
                    JObject response = OverwriteVfx(root, target, source);
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    CollectionAssert.AreEqual(expected, File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(target)));
                    Assert.AreEqual(guid, UnityEditor.AssetDatabase.AssetPathToGUID(target));
                    Assert.IsEmpty(Directory.GetFiles(AssetPathUtility.GetFullAssetPath(root), "__McpVfxOverwrite_*"));
                    UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
                }
            );
        }

        [TestCase("float")]
        [TestCase("int")]
        [TestCase("bool")]
        [TestCase("vector2")]
        [TestCase("vector3")]
        [TestCase("vector4")]
        [TestCase("color")]
        [TestCase("gradient")]
        [TestCase("curve")]
        [TestCase("texture")]
        [TestCase("mesh")]
        public void MissingExposedParameterRejectsBeforeNativeOverride(string kind)
        {
            RequireEnabledGraph();
            var properties = new JObject
            {
                ["parameter"] = "Missing_" + Guid.NewGuid().ToString("N"),
                ["value"] =
                    kind == "bool" ? (JToken)new JValue(false)
                    : kind.StartsWith("vector") || kind == "color" ? new JArray(1, 2, 3, 4)
                    : new JValue(0),
                ["texture_path"] = "Assets/AbsentTexture.png",
                ["mesh_path"] = "Assets/AbsentMesh.asset",
            };
            int dirtyCount = UnityEditor.EditorUtility.GetDirtyCount(_effect);
            JObject response = Send("vfx_set_" + kind, properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Parameter", response["message"]?.ToString());
            Assert.AreEqual(dirtyCount, UnityEditor.EditorUtility.GetDirtyCount(_effect));
        }

        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        [TestCase("1e100")]
        [TestCase("true")]
        public void InvalidPlaybackRatePreservesNativeState(string token)
        {
            RequireEnabledGraph();
            float original = GetProperty<float>("playRate");
            JObject response = Send("vfx_set_playback_speed", new JObject { ["play_rate"] = JToken.Parse(token) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(original, GetProperty<float>("playRate"));
        }

        [TestCase("{}", 1f)]
        [TestCase("{play_rate:0}", 0f)]
        [TestCase("{play_rate:'2.5'}", 2.5f)]
        public void PlaybackDefaultZeroAndExistingConversionsRemainAccepted(string json, float expected)
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_set_playback_speed", JObject.Parse(json));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expected, GetProperty<float>("playRate"));
        }

        [TestCase("{}", true)]
        [TestCase("{seed:0,reset_seed_on_play:false}", false)]
        public void SeedDefaultZeroAndFalseRemainAccepted(string json, bool reset)
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_set_seed", JObject.Parse(json));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0u, GetProperty<uint>("startSeed"));
            Assert.AreEqual(reset, GetProperty<bool>("resetSeedOnPlay"));
        }

        [Test]
        public void PauseTogglesOwnedComponentInBothDirections()
        {
            RequireEnabledGraph();
            bool original = GetProperty<bool>("pause");
            JObject first = Send("vfx_pause", new JObject());
            Assert.IsTrue(first.Value<bool>("success"));
            Assert.AreEqual(!original, GetProperty<bool>("pause"));
            JObject second = Send("vfx_pause", new JObject());
            Assert.IsTrue(second.Value<bool>("success"));
            Assert.AreEqual(original, GetProperty<bool>("pause"));
        }

        [Test]
        public void EventWithoutNameFailsWithoutSending()
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_send_event", JObject.Parse("{position:[],size:true}"));
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("Event name required", response["message"]?.ToString());
        }

        [TestCase("position", "[true,0,0]", "position[0]")]
        [TestCase("velocity", "[0,'Infinity',0]", "velocity[1]")]
        [TestCase("color", "{r:1,g:1,b:true}", "color.b")]
        [TestCase("size", "true", "size")]
        [TestCase("lifetime", "'NaN'", "lifetime")]
        [TestCase("position", "[]", "Invalid Vector3")]
        [TestCase("color", "{}", "Invalid Color")]
        public void InvalidEventPayloadRejectsBeforeNativeAttributeCreation(string field, string json, string expected)
        {
            RequireEnabledGraph();
            Assert.IsNull(GetProperty<UnityEngine.Object>("visualEffectAsset"), "The fixture must have no asset for native event attributes.");
            var properties = JObject.Parse("{event_name:'spawn',position:[1,2,3],velocity:[0,1,0],color:[1,1,1],size:1,lifetime:2}");
            properties[field] = JToken.Parse(json);
            JObject response = Send("vfx_send_event", properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(expected, response.Value<string>("message"));
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void EventPayloadValidationPreservesPositionBeforeLaterErrors()
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_send_event", JObject.Parse("{event_name:'spawn',position:[true,0,0],velocity:[],color:{},size:true,lifetime:true}"));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("position[0]", response.Value<string>("message"));
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [TestCase(2)]
        [TestCase(3)]
        [TestCase(4)]
        public void ExistingDimensionParsersAcceptArraysAndObjects(int dimensions)
        {
            var array = new JArray(2, 3, 4, 5);
            var properties = JObject.Parse("{x:2,y:3,z:4,w:5}");
            if (dimensions == 2)
            {
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(array).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(properties).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(new JArray(2, 3)).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(JObject.Parse("{x:2,y:3}")).Value);
            }
            else if (dimensions == 3)
            {
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(array).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(properties).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(new JArray(2, 3, 4)).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(JObject.Parse("{x:2,y:3,z:4}")).Value);
            }
            else
            {
                Assert.AreEqual(new Vector4(2, 3, 4, 5), VectorParsing.ParseVector4(array).Value);
                Assert.AreEqual(new Vector4(2, 3, 4, 5), VectorParsing.ParseVector4(properties).Value);
            }
        }
    }
}
