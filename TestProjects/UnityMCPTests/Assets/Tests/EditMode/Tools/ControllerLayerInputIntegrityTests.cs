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
    public class ControllerLayerInputIntegrityTests
    {
        private string _root;
        private string _guid;
        private string _path;
        private AnimatorController _controller;

        [SetUp]
        public void SetUp()
        {
            _root = "Assets/__McpControllerLayerInput_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root));
            _guid = AssetDatabase.CreateFolder("Assets", _root.Substring(7));
            Assert.IsNotEmpty(_guid);
            _path = _root + "/Controller.controller";
            _controller = AnimatorController.CreateAnimatorControllerAtPath(_path);
            _controller.AddLayer("False");
            _controller.AddLayer("0");
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_guid) && AssetDatabase.AssetPathToGUID(_root) == _guid)
                AssetDatabase.DeleteAsset(_root);
        }

        private JObject Send(string action, JObject properties)
        {
            properties["controller_path"] = _path;
            var command = JsonConvert.DeserializeObject<Command>(
                new JObject
                {
                    ["params"] = new JObject { ["action"] = "controller_" + action, ["properties"] = properties },
                }.ToString(Formatting.None)
            );
            return JObject.FromObject(ManageAnimation.HandleCommand(command.@params));
        }

        private string Snapshot() =>
            JsonConvert.SerializeObject(
                new
                {
                    content = EditorJsonUtility.ToJson(_controller),
                    dirty = EditorUtility.GetDirtyCount(_controller),
                    bytes = Convert.ToBase64String(File.ReadAllBytes(Path.Combine(Application.dataPath, _path.Substring(7)))),
                    subassets = AssetDatabase.LoadAllAssetsAtPath(_path).Length,
                }
            );

        [TestCase("add_layer", "false")]
        [TestCase("add_layer", "0")]
        [TestCase("add_layer", "[]")]
        [TestCase("add_layer", "{}")]
        [TestCase("remove_layer", "false")]
        [TestCase("remove_layer", "0")]
        [TestCase("set_layer_weight", "false")]
        [TestCase("set_layer_weight", "0")]
        public void ConsumedNonStringNameRejectsWithoutControllerChanges(string action, string raw)
        {
            string before = Snapshot();
            JObject response = Send(action, new JObject { ["layer_name"] = JToken.Parse(raw), ["weight"] = 0.25f });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'layerName' must be a string", response.Value<string>("error"));
            Assert.AreEqual(before, Snapshot());
        }

        [TestCase("'typo'")]
        [TestCase("false")]
        [TestCase("0")]
        [TestCase("[]")]
        public void InvalidConsumedModeRejectsWithoutAddingLayer(string raw)
        {
            string before = Snapshot();
            JObject response = Send("add_layer", new JObject { ["layer_name"] = "New", ["blending_mode"] = JToken.Parse(raw) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("blendingMode", response.Value<string>("message") ?? response.Value<string>("error"));
            Assert.AreEqual(before, Snapshot());
        }

        [TestCase("remove_layer")]
        [TestCase("set_layer_weight")]
        public void ExplicitIndexIgnoresMalformedUnusedName(string action)
        {
            JObject response = Send(
                action,
                new JObject
                {
                    ["layer_index"] = "1",
                    ["layer_name"] = new JObject { ["unused"] = false },
                    ["weight"] = -2f,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("False", response["data"].Value<string>("layerName"));
            if (action == "set_layer_weight")
                Assert.AreEqual(-2f, _controller.layers[1].defaultWeight);
            else
                Assert.AreEqual(2, _controller.layers.Length);
        }

        [TestCase("null", AnimatorLayerBlendingMode.Override)]
        [TestCase("''", AnimatorLayerBlendingMode.Override)]
        [TestCase("'ADDITIVE'", AnimatorLayerBlendingMode.Additive)]
        public void DefaultAndNamedModesRemainCompatible(string raw, AnimatorLayerBlendingMode expected)
        {
            JObject response = Send("add_layer", new JObject { ["layer_name"] = "New", ["blending_mode"] = JToken.Parse(raw) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expected, _controller.layers[3].blendingMode);
        }

        [Test]
        public void ParsedIsoStringNameKeepsLegacyDateConversion()
        {
            var culture = CultureInfo.CurrentCulture;
            var uiCulture = CultureInfo.CurrentUICulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("ko-KR");
                CultureInfo.CurrentUICulture = CultureInfo.CurrentCulture;
                const string iso = "2026-10-09T11:12:13Z";
                string expected = JsonConvert.DeserializeObject<JObject>("{\"name\":\"" + iso + "\"}")["name"].ToString();
                JObject response = Send("add_layer", new JObject { ["layer_name"] = iso });
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(expected, _controller.layers[3].name);
            }
            finally
            {
                CultureInfo.CurrentCulture = culture;
                CultureInfo.CurrentUICulture = uiCulture;
            }
        }

        [TestCase("add_layer")]
        [TestCase("remove_layer")]
        [TestCase("set_layer_weight")]
        public void NonStringControllerPathRejectsBeforeMissingSelector(string action)
        {
            // Missing selector guarantees that a baseline cannot mutate an unrelated coincident controller.
            string before = Snapshot();
            JObject response = JObject.FromObject(
                ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_" + action, ["controller_path"] = false })
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'controllerPath' must be a string", response.Value<string>("error"));
            Assert.AreEqual(before, Snapshot());
        }
    }
}
