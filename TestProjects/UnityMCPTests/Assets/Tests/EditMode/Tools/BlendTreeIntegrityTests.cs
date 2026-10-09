using System;
using System.Globalization;
using System.IO;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class BlendTreeIntegrityTests
    {
        private string _root;
        private string _rootGuid;
        private string _controllerPath;
        private string _clipPath;
        private AnimatorController _controller;

        [SetUp]
        public void SetUp()
        {
            string folder = "BlendTreeIntegrity_" + Guid.NewGuid().ToString("N");
            _root = "Assets/" + folder;
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root));
            _rootGuid = AssetDatabase.CreateFolder("Assets", folder);
            Assert.IsNotEmpty(_rootGuid);
            _controllerPath = _root + "/Controller.controller";
            _clipPath = _root + "/Child.anim";
            _controller = AnimatorController.CreateAnimatorControllerAtPath(_controllerPath);
            AssetDatabase.CreateAsset(new AnimationClip { name = "Child" }, _clipPath);
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_rootGuid) && AssetDatabase.AssetPathToGUID(_root) == _rootGuid)
                AssetDatabase.DeleteAsset(_root);
        }

        private JObject Send(string action, JObject properties)
        {
            properties["controller_path"] = _controllerPath;
            properties["state_name"] = "Tree";
            return JObject.FromObject(ManageAnimation.HandleCommand(new JObject { ["action"] = action, ["properties"] = properties }));
        }

        private BlendTree CreateTree(BlendTreeType type)
        {
            JObject response =
                type == BlendTreeType.Simple1D
                    ? Send("controller_create_blend_tree_1d", new JObject { ["blend_parameter"] = "Speed" })
                    : Send("controller_create_blend_tree_2d", new JObject { ["blend_parameter_x"] = "X", ["blend_parameter_y"] = "Y" });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var tree = (BlendTree)_controller.layers[0].stateMachine.states[0].state.motion;
            tree.blendType = type;
            // Manual thresholds are an explicit fixture choice, not a production policy change.
            if (type == BlendTreeType.Simple1D)
                tree.useAutomaticThresholds = false;
            AssetDatabase.SaveAssets();
            return tree;
        }

        [TestCase("typo")]
        [TestCase("Direct")]
        [TestCase("Simple1D")]
        [TestCase("")]
        public void InvalidCreationTypePreservesStatesAndSubassets(string type)
        {
            int assets = AssetDatabase.LoadAllAssetsAtPath(_controllerPath).Length;
            bool dirty = EditorUtility.IsDirty(_controller);
            JObject response = Send(
                "controller_create_blend_tree_2d",
                new JObject
                {
                    ["blend_parameter_x"] = "X",
                    ["blend_parameter_y"] = "Y",
                    ["blend_type"] = type,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(_controller.layers[0].stateMachine.states);
            Assert.AreEqual(assets, AssetDatabase.LoadAllAssetsAtPath(_controllerPath).Length);
            Assert.AreEqual(dirty, EditorUtility.IsDirty(_controller));
        }

        [TestCase(null, BlendTreeType.SimpleDirectional2D)]
        [TestCase("sImPlEdIrEcTiOnAl2D", BlendTreeType.SimpleDirectional2D)]
        [TestCase("FreeformDirectional2D", BlendTreeType.FreeformDirectional2D)]
        [TestCase("FreeformCartesian2D", BlendTreeType.FreeformCartesian2D)]
        public void SupportedCreationTypesRetainDefaultsAndCaseCompatibility(string type, BlendTreeType expected)
        {
            JObject response = Send(
                "controller_create_blend_tree_2d",
                new JObject
                {
                    ["blend_parameter_x"] = "X",
                    ["blend_parameter_y"] = "Y",
                    ["blend_type"] = type,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var tree = (BlendTree)_controller.layers[0].stateMachine.states[0].state.motion;
            Assert.AreEqual(expected, tree.blendType);
        }

        [TestCase("{}")]
        [TestCase("{threshold:null}")]
        [TestCase("{threshold:true}")]
        [TestCase("{threshold:'bad'}")]
        [TestCase("{threshold:NaN}")]
        [TestCase("{threshold:Infinity}")]
        [TestCase("{threshold:1e100}")]
        public void InvalidThresholdPreservesChildrenAndDirtyState(string json)
        {
            var tree = CreateTree(BlendTreeType.Simple1D);
            bool treeDirty = EditorUtility.IsDirty(tree),
                controllerDirty = EditorUtility.IsDirty(_controller);
            var properties = JObject.Parse(json);
            properties["clip_path"] = _clipPath;
            JObject response = Send("controller_add_blend_tree_child", properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(tree.children);
            Assert.AreEqual(treeDirty, EditorUtility.IsDirty(tree));
            Assert.AreEqual(controllerDirty, EditorUtility.IsDirty(_controller));
        }

        [TestCase("0", 0f)]
        [TestCase("-2", -2f)]
        [TestCase("'1.5'", 1.5f)]
        public void FiniteThresholdRetainsItsValue(string json, float expected)
        {
            var tree = CreateTree(BlendTreeType.Simple1D);
            JObject response = Send("controller_add_blend_tree_child", new JObject { ["clip_path"] = _clipPath, ["threshold"] = JToken.Parse(json) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1, response["data"].Value<int>("childCount"));
            Assert.AreEqual(expected, tree.children[0].threshold);
        }

        [TestCase("{}")]
        [TestCase("{position:null}")]
        [TestCase("{position:[0]}")]
        [TestCase("{position:[true,0]}")]
        [TestCase("{position:[0,'bad']}")]
        [TestCase("{position:[NaN,0]}")]
        [TestCase("{position:[0,Infinity]}")]
        [TestCase("{position:[0,1e100]}")]
        public void InvalidPositionPreservesChildrenAndDirtyState(string json)
        {
            var tree = CreateTree(BlendTreeType.SimpleDirectional2D);
            bool dirty = EditorUtility.IsDirty(tree);
            var properties = JObject.Parse(json);
            properties["clip_path"] = _clipPath;
            JObject response = Send("controller_add_blend_tree_child", properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(tree.children);
            Assert.AreEqual(dirty, EditorUtility.IsDirty(tree));
        }

        [TestCase(BlendTreeType.SimpleDirectional2D)]
        [TestCase(BlendTreeType.FreeformDirectional2D)]
        [TestCase(BlendTreeType.FreeformCartesian2D)]
        public void FinitePositionRetainsSourceValuesAndCompatibleExtraElements(BlendTreeType type)
        {
            var tree = CreateTree(type);
            JObject response = Send(
                "controller_add_blend_tree_child",
                new JObject { ["clip_path"] = _clipPath, ["position"] = new JArray("-2", 0, "ignored") }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1, response["data"].Value<int>("childCount"));
            Assert.AreEqual(new Vector2(-2, 0), tree.children[0].position);
        }

        [Test]
        public void DirectModeRejectsBeforeAddingAnyChild()
        {
            var tree = CreateTree(BlendTreeType.Direct);
            JObject response = Send("controller_add_blend_tree_child", new JObject { ["clip_path"] = _clipPath, ["position"] = new JArray(0, 0) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(tree.children);
        }

        private JObject SendRaw(string action, JObject properties)
        {
            properties["controller_path"] = _controllerPath;
            var command = JsonConvert.DeserializeObject<Command>(
                new JObject
                {
                    ["params"] = new JObject { ["action"] = action, ["properties"] = properties },
                }.ToString(Formatting.None)
            );
            return JObject.FromObject(ManageAnimation.HandleCommand(command.@params));
        }

        private string CreationSnapshot() =>
            JsonConvert.SerializeObject(
                new
                {
                    controller = EditorJsonUtility.ToJson(_controller),
                    machine = EditorJsonUtility.ToJson(_controller.layers[0].stateMachine),
                    dirty = EditorUtility.GetDirtyCount(_controller),
                    machineDirty = EditorUtility.GetDirtyCount(_controller.layers[0].stateMachine),
                    bytes = Convert.ToBase64String(File.ReadAllBytes(Path.Combine(Application.dataPath, _controllerPath.Substring(7)))),
                    subassets = AssetDatabase.LoadAllAssetsAtPath(_controllerPath).Length,
                }
            );

        [TestCase("controller_create_blend_tree_1d", "state_name", "false", "stateName")]
        [TestCase("controller_create_blend_tree_1d", "state_name", "0", "stateName")]
        [TestCase("controller_create_blend_tree_1d", "blend_parameter", "false", "blendParameter")]
        [TestCase("controller_create_blend_tree_1d", "blend_parameter", "{}", "blendParameter")]
        [TestCase("controller_create_blend_tree_2d", "state_name", "[]", "stateName")]
        [TestCase("controller_create_blend_tree_2d", "blend_parameter_x", "0", "blendParameterX")]
        [TestCase("controller_create_blend_tree_2d", "blend_parameter_y", "false", "blendParameterY")]
        public void NonStringCreationNamesRejectBeforeStateOrSubassetChanges(string action, string field, string raw, string errorField)
        {
            string before = CreationSnapshot();
            var properties = new JObject
            {
                ["state_name"] = "Tree",
                ["blend_parameter"] = "Speed",
                ["blend_parameter_x"] = "X",
                ["blend_parameter_y"] = "Y",
            };
            properties[field] = JToken.Parse(raw);
            JObject response = SendRaw(action, properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'" + errorField + "' must be a string", response.Value<string>("error"));
            Assert.AreEqual(before, CreationSnapshot());
        }

        [TestCase("false", "False")]
        [TestCase("0", "0")]
        public void NonStringChildStateDoesNotSelectCoincidentName(string raw, string name)
        {
            var tree = CreateTree(BlendTreeType.Simple1D);
            _controller.layers[0].stateMachine.states[0].state.name = name;
            AssetDatabase.SaveAssets();
            string before = CreationSnapshot();
            int dirty = EditorUtility.GetDirtyCount(tree);
            JObject response = SendRaw(
                "controller_add_blend_tree_child",
                new JObject
                {
                    ["state_name"] = JToken.Parse(raw),
                    ["clip_path"] = _clipPath,
                    ["threshold"] = 0,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'stateName' must be a string", response.Value<string>("error"));
            Assert.IsEmpty(tree.children);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(tree));
            Assert.AreEqual(before, CreationSnapshot());
        }

        [TestCase("false")]
        [TestCase("0")]
        [TestCase("[]")]
        public void NonStringClipRejectsWithFieldErrorBeforeAnyMutation(string raw)
        {
            // A missing owned state keeps this fixture safe even on a baseline that might load a coincident clip elsewhere.
            string before = CreationSnapshot();
            JObject response = SendRaw(
                "controller_add_blend_tree_child",
                new JObject
                {
                    ["state_name"] = "Missing_" + Guid.NewGuid().ToString("N"),
                    ["clip_path"] = JToken.Parse(raw),
                    ["threshold"] = 0,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'clipPath' must be a string", response.Value<string>("error"));
            Assert.AreEqual(before, CreationSnapshot());
        }

        [Test]
        public void MissingParameterErrorPrecedesMalformedStateName()
        {
            string before = CreationSnapshot();
            JObject response = SendRaw("controller_create_blend_tree_1d", new JObject { ["state_name"] = false });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("'blendParameter' is required", response.Value<string>("message"));
            Assert.AreEqual(before, CreationSnapshot());
        }

        [Test]
        public void ParsedIsoStateAndParameterKeepLegacyDateConversion()
        {
            var culture = CultureInfo.CurrentCulture;
            var uiCulture = CultureInfo.CurrentUICulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("ko-KR");
                CultureInfo.CurrentUICulture = CultureInfo.CurrentCulture;
                const string iso = "2026-10-09T11:12:13Z";
                string expected = JsonConvert.DeserializeObject<JObject>("{\"name\":\"" + iso + "\"}")["name"].ToString();
                JObject response = SendRaw("controller_create_blend_tree_1d", new JObject { ["state_name"] = iso, ["blend_parameter"] = iso });
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var state = _controller.layers[0].stateMachine.states[0].state;
                Assert.AreEqual(expected, state.name);
                Assert.AreEqual(expected, ((BlendTree)state.motion).blendParameter);
            }
            finally
            {
                CultureInfo.CurrentCulture = culture;
                CultureInfo.CurrentUICulture = uiCulture;
            }
        }

        [TestCase("controller_create_blend_tree_1d")]
        [TestCase("controller_create_blend_tree_2d")]
        [TestCase("controller_add_blend_tree_child")]
        public void NonStringControllerPathRejectsBeforeMissingStateName(string action)
        {
            // No state name: even a baseline finding a coincident controller cannot mutate it.
            string before = CreationSnapshot();
            JObject response = JObject.FromObject(ManageAnimation.HandleCommand(new JObject { ["action"] = action, ["controller_path"] = false }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'controllerPath' must be a string", response.Value<string>("error"));
            Assert.AreEqual(before, CreationSnapshot());
        }
    }
}
