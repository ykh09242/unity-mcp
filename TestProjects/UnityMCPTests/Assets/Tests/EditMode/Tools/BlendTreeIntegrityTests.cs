using System;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class BlendTreeIntegrityTests
    {
        private string _root;
        private string _controllerPath;
        private string _clipPath;
        private AnimatorController _controller;

        [SetUp]
        public void SetUp()
        {
            string folder = "BlendTreeIntegrity_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder);
            _root = "Assets/" + folder;
            _controllerPath = _root + "/Controller.controller";
            _clipPath = _root + "/Child.anim";
            _controller = AnimatorController.CreateAnimatorControllerAtPath(_controllerPath);
            AssetDatabase.CreateAsset(new AnimationClip { name = "Child" }, _clipPath);
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_root))
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
    }
}
