using System;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Animation;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    // Authored native Edit-mode regressions. This audit compiles them without running the Editor.
    public class AnimatorControlIntegrityTests
    {
        private string assetRoot;
        private bool ownsAssetRoot;
        private GameObject ownedObject;
        private Animator animator;
        private AnimatorController controller;
        private AnimatorOverrideController ownedOverride;
        private UnityEngine.Object[] previousSelection;
        private UnityEngine.Object previousActive;

        [SetUp]
        public void SetUp()
        {
            Assert.IsFalse(Application.isPlaying, "These controls exercise Edit-mode controller defaults.");
            ownsAssetRoot = false;
            ownedOverride = null;
            ownedObject = null;
            previousSelection = Selection.objects;
            previousActive = Selection.activeObject;
            assetRoot = "Assets/__McpAnimatorControlIntegrity_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            string guid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot, "Capture folder ownership before later assertions.");
            controller = AnimatorController.CreateAnimatorControllerAtPath(assetRoot + "/Fixture.controller");
            controller.parameters = new[]
            {
                new AnimatorControllerParameter
                {
                    name = "Speed",
                    type = AnimatorControllerParameterType.Float,
                    defaultFloat = 0.5f,
                },
                new AnimatorControllerParameter
                {
                    name = "Count",
                    type = AnimatorControllerParameterType.Int,
                    defaultInt = 7,
                },
                new AnimatorControllerParameter
                {
                    name = "Enabled",
                    type = AnimatorControllerParameterType.Bool,
                    defaultBool = true,
                },
                new AnimatorControllerParameter { name = "Jump", type = AnimatorControllerParameterType.Trigger },
                new AnimatorControllerParameter
                {
                    name = "Unrelated",
                    type = AnimatorControllerParameterType.Float,
                    defaultFloat = 42,
                },
            };
            AssetDatabase.SaveAssets();
            Assert.IsTrue(EditorUtility.IsPersistent(controller));
            ownedObject = new GameObject("McpAnimatorControlIntegrity_" + Guid.NewGuid().ToString("N"));
            animator = ownedObject.AddComponent<Animator>();
            animator.runtimeAnimatorController = controller;
            animator.enabled = false; // No Rebind/Update or active runtime graph is required for default editing.
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (ownedObject != null)
                    UnityEngine.Object.DestroyImmediate(ownedObject);
                if (ownedOverride != null && !EditorUtility.IsPersistent(ownedOverride))
                    UnityEngine.Object.DestroyImmediate(ownedOverride);
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpAnimatorControlIntegrity_", StringComparison.Ordinal));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Delete only the exact successfully created unique folder.");
                    ownsAssetRoot = false;
                }
            }
            finally
            {
                Selection.objects = previousSelection;
                Selection.activeObject = previousActive;
            }
        }

        private JObject Request(string parameter, string type = null) =>
            new JObject
            {
                ["action"] = "animator_set_parameter",
                ["target"] = ownedObject.GetInstanceIDCompat(),
                ["search_method"] = "by_id",
                ["parameter_name"] = parameter,
                ["parameter_type"] = type,
            };

        private static JObject Call(JObject request) => JObject.FromObject(ManageAnimation.HandleCommand(request));

        private JArray Snapshot() =>
            new JArray(
                controller.parameters.Select(p => new JObject
                {
                    ["name"] = p.name,
                    ["type"] = (int)p.type,
                    ["float"] = p.defaultFloat,
                    ["int"] = p.defaultInt,
                    ["bool"] = p.defaultBool,
                })
            );

        private void AssertRejectedUnchanged(JObject request)
        {
            var before = Snapshot();
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(controller));
            int dirtyCount = EditorUtility.GetDirtyCount(controller);
            var result = Call(request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()), "All parameter definitions/default slots must survive rejection.");
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(controller));
            Assert.AreSame(controller, AssetDatabase.LoadAssetAtPath<AnimatorController>(AssetDatabase.GetAssetPath(controller)));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(controller)));
        }

        [TestCase("Speed", "int")]
        [TestCase("Count", "bool")]
        [TestCase("Enabled", "trigger")]
        [TestCase("Jump", "float")]
        public void ExplicitTypeMismatchPreservesAllDefaults(string parameter, string type)
        {
            var request = Request(parameter, type);
            request["value"] = 1;
            AssertRejectedUnchanged(request);
        }

        [TestCase("float")]
        [TestCase("trigger")]
        [TestCase(null)]
        public void MissingParameterRejectsExplicitAndInferredTypes(string type)
        {
            AssertRejectedUnchanged(Request("Missing", type));
        }

        [TestCase("Speed", "float", false)]
        [TestCase("Count", "int", false)]
        [TestCase("Enabled", "bool", false)]
        [TestCase("Speed", "float", true)]
        public void MalformedOrNullValuePreservesDefaults(string parameter, string type, bool nullValue)
        {
            var request = Request(parameter, type);
            request["value"] = nullValue ? JValue.CreateNull() : new JValue("bad");
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAnimation\\] Action 'animator_set_parameter' failed:"));
            AssertRejectedUnchanged(request);
        }

        [Test]
        public void UnknownTypePreservesDefaults() => AssertRejectedUnchanged(Request("Speed", "unknown"));

        [TestCase("Speed")]
        [TestCase("Count")]
        [TestCase("Enabled")]
        public void DisabledAnimatorInfersControllerTypeAndSetsOmittedDefault(string parameter)
        {
            var result = Call(Request(parameter));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var p = controller.parameters.Single(x => x.name == parameter);
            if (parameter == "Speed")
                Assert.AreEqual(0f, p.defaultFloat);
            if (parameter == "Count")
                Assert.AreEqual(0, p.defaultInt);
            if (parameter == "Enabled")
                Assert.IsFalse(p.defaultBool);
            Assert.AreEqual(42f, controller.parameters.Single(x => x.name == "Unrelated").defaultFloat);
        }

        [TestCase("Count", "INTEGER", "0")]
        [TestCase("Enabled", "BOOLEAN", "false")]
        [TestCase("Speed", "FLOAT", "-2.5")]
        public void SupportedAliasesAndNumericStringsArePreserved(string parameter, string type, string value)
        {
            var request = Request(parameter, type);
            request["value"] = value;
            var result = Call(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var p = controller.parameters.Single(x => x.name == parameter);
            if (parameter == "Count")
                Assert.AreEqual(0, p.defaultInt);
            if (parameter == "Enabled")
                Assert.IsFalse(p.defaultBool);
            if (parameter == "Speed")
                Assert.AreEqual(-2.5f, p.defaultFloat);
        }

        [Test]
        public void EditTriggerAcknowledgementPreservesController()
        {
            var request = Request("Jump", "trigger");
            request["value"] = new JObject { ["ignored"] = true };
            var before = Snapshot();
            int dirtyCount = EditorUtility.GetDirtyCount(controller);
            var result = Call(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("runtime-only", result.Value<string>("message"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(controller));
        }

        [Test]
        public void OverrideControllerEditingRemainsUnsupported()
        {
            ownedOverride = new AnimatorOverrideController(controller);
            animator.runtimeAnimatorController = ownedOverride;
            AssertRejectedUnchanged(Request("Speed", "float"));
        }

        [Test]
        public void EncodedPropertiesKeepExplicitFalsePrecedence()
        {
            var request = new JObject
            {
                ["action"] = "animator_set_parameter",
                ["target"] = ownedObject.GetInstanceIDCompat(),
                ["search_method"] = "by_id",
                ["properties"] = "{\"parameter_name\":\"Enabled\",\"parameter_type\":\"boolean\",\"value\":true}",
                ["value"] = false,
            };
            Assert.IsTrue(Call(request).Value<bool>("success"));
            Assert.IsFalse(controller.parameters.Single(p => p.name == "Enabled").defaultBool);
        }

        [Test]
        [Combinatorial]
        public void OutOfRangePlaybackLayerPreservesAnimatorAndController(
            [Values("animator_play", "animator_crossfade")] string action,
            [Values("negative", "upper", "numeric_string")] string kind
        )
        {
            int layer = kind == "negative" ? -2 : animator.layerCount;
            var request = new JObject
            {
                ["action"] = action,
                ["target"] = ownedObject.GetInstanceIDCompat(),
                ["search_method"] = "by_id",
                ["state_name"] = "Base Layer.Idle",
                ["layer"] = kind == "numeric_string" ? (JToken)new JValue(layer.ToString()) : new JValue(layer),
            };
            var before = Snapshot();
            string animatorBefore = EditorJsonUtility.ToJson(animator);
            int animatorDirty = EditorUtility.GetDirtyCount(animator);
            int controllerDirty = EditorUtility.GetDirtyCount(controller);
            string path = AssetDatabase.GetAssetPath(controller);
            string guid = AssetDatabase.AssetPathToGUID(path);

            JObject response = Call(request);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("out of range", response.Value<string>("message"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
            Assert.AreEqual(animatorBefore, EditorJsonUtility.ToJson(animator));
            Assert.AreEqual(animatorDirty, EditorUtility.GetDirtyCount(animator));
            Assert.AreEqual(controllerDirty, EditorUtility.GetDirtyCount(controller));
            Assert.IsTrue(animator.runtimeAnimatorController == controller);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsFalse(animator.enabled);
        }

        [TestCase("duration", false)]
        [TestCase("duration", true)]
        [TestCase("layer", false)]
        public void MalformedCrossfadeInputPreservesOwnedObjects(string field, bool nullValue)
        {
            var before = Snapshot();
            int dirtyCount = EditorUtility.GetDirtyCount(controller);
            var request = new JObject
            {
                ["action"] = "animator_crossfade",
                ["target"] = ownedObject.GetInstanceIDCompat(),
                ["search_method"] = "by_id",
                ["state_name"] = "Base Layer.Idle",
                [field] = nullValue ? JValue.CreateNull() : new JValue("bad"),
            };
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAnimation\\] Action 'animator_crossfade' failed:"));
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(controller));
            Assert.IsFalse(animator.enabled);
        }
    }
}
