using System;
using System.Collections;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class AssetPersistenceTests
    {
        private string folder;
        private AnimationClip unrelated;
        private byte[] original;
        private string unrelatedPath;

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/ScopedSave_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder)));
            unrelatedPath = folder + "/Unrelated.anim";
            unrelated = new AnimationClip { name = "Unrelated", frameRate = 30 };
            AssetDatabase.CreateAsset(unrelated, unrelatedPath);
            original = File.ReadAllBytes(unrelatedPath);
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        [UnityTest]
        public IEnumerator ProjectSettingsMutationDoesNotSaveUnrelatedAssets()
        {
            foreach (string kind in new[] { "tag", "layer" })
            {
                string name = "Scoped_" + Guid.NewGuid().ToString("N");
                string key = kind + "Name";
                string settings = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ProjectSettings/TagManager.asset");
                unrelated.frameRate = 17;
                EditorUtility.SetDirty(unrelated);
                try
                {
                    JObject result = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "add_" + kind, [key] = name }));
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    var deadline = DateTime.UtcNow.AddSeconds(3);
                    while (!File.ReadAllText(settings).Contains(name) && DateTime.UtcNow < deadline)
                        yield return null;
                    StringAssert.Contains(name, File.ReadAllText(settings));
                    CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
                    Assert.IsTrue(EditorUtility.IsDirty(unrelated));
                }
                finally
                {
                    JObject result = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "remove_" + kind, [key] = name }));
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                }
                var cleanupDeadline = DateTime.UtcNow.AddSeconds(3);
                while (File.ReadAllText(settings).Contains(name) && DateTime.UtcNow < cleanupDeadline)
                    yield return null;
                StringAssert.DoesNotContain(name, File.ReadAllText(settings));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
                Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            }
        }

        [TestCase("asset_create")]
        [TestCase("asset_modify")]
        [TestCase("controller_create")]
        [TestCase("controller_add_state")]
        [TestCase("controller_add_parameter")]
        [TestCase("controller_add_layer")]
        [TestCase("clip_create")]
        public void TargetMutationPreservesUnrelatedBytesAndDirtyState(string action)
        {
            string controllerPath = folder + "/Target.controller";
            string materialPath = folder + "/Target.mat";
            if (action.StartsWith("controller_add_", StringComparison.Ordinal))
                AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            if (action == "asset_modify")
                AssetDatabase.CreateAsset(new Material(Shader.Find("Standard")), materialPath);
            unrelated.frameRate = 17;
            EditorUtility.SetDirty(unrelated);
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            JObject result;
            string targetPath;
            if (action.StartsWith("asset_", StringComparison.Ordinal))
            {
                targetPath = materialPath;
                result = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = action == "asset_create" ? "create" : "modify",
                            ["path"] = materialPath,
                            ["assetType"] = "material",
                            ["properties"] = new JObject { ["color"] = new JArray(0.1f, 0.2f, 0.3f, 1f) },
                        }
                    )
                );
            }
            else
            {
                targetPath = action == "clip_create" ? folder + "/Target.anim" : controllerPath;
                result = JObject.FromObject(
                    ManageAnimation.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["controllerPath"] = controllerPath,
                            ["clipPath"] = action == "clip_create" ? targetPath : null,
                            ["stateName"] = "OwnedState",
                            ["parameterName"] = "OwnedParameter",
                            ["layerName"] = "OwnedLayer",
                        }
                    )
                );
            }
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            if (action == "asset_modify")
                StringAssert.Contains("modified successfully", (string)result["message"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath), "Unrelated on-disk bytes must not be saved.");
            Assert.IsTrue(EditorUtility.IsDirty(unrelated), "The user's pending changes must remain dirty.");
            Assert.IsTrue(File.Exists(targetPath), targetPath);
            Assert.IsFalse(EditorUtility.IsDirty(AssetDatabase.LoadMainAssetAtPath(targetPath)), "The requested target must be saved.");
            string serialized = File.ReadAllText(targetPath);
            if (action == "controller_add_state")
                StringAssert.Contains("OwnedState", serialized);
            if (action == "controller_add_parameter")
                StringAssert.Contains("OwnedParameter", serialized);
            if (action == "controller_add_layer")
                StringAssert.Contains("OwnedLayer", serialized);
        }
    }
}
