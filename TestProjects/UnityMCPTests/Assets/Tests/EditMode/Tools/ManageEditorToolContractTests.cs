using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageEditorToolContractTests
    {
        [TestCase("add_tag", "tagName")]
        [TestCase("add_tag", "tag_name")]
        [TestCase("remove_tag", "tagName")]
        [TestCase("remove_tag", "tag_name")]
        [TestCase("add_layer", "layerName")]
        [TestCase("add_layer", "layer_name")]
        [TestCase("remove_layer", "layerName")]
        [TestCase("remove_layer", "layer_name")]
        public void NonStringTagOrLayerName_RejectsWithoutChangingProjectSettings(string action, string key)
        {
            UnityEngine.Object[] assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset");
            Assert.IsNotEmpty(assets, "TagManager must be available for the mutation regression.");
            UnityEngine.Object tagManager = assets[0];
            string before = EditorJsonUtility.ToJson(tagManager);
            try
            {
                foreach (string json in new[] { "false", "0", "1.5", "[]", "{}" })
                {
                    var response = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = action, [key] = JToken.Parse(json) }));
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    StringAssert.Contains("must be a string", response.Value<string>("error"));
                    Assert.AreEqual(before, EditorJsonUtility.ToJson(tagManager), "Invalid names must not mutate tags or layers.");
                }
            }
            finally
            {
                if (EditorJsonUtility.ToJson(tagManager) != before)
                {
                    EditorJsonUtility.FromJsonOverwrite(before, tagManager);
                    EditorUtility.SetDirty(tagManager);
                    AssetDatabase.SaveAssets();
                }
            }
        }

        [TestCase("-999")]
        [TestCase("-2")]
        [TestCase("999")]
        [TestCase("None")]
        [TestCase("not-a-tool")]
        public void InvalidTool_RejectsWithoutChangingCurrentTool(string name)
        {
            Tool original = UnityEditor.Tools.current;
            try
            {
                UnityEditor.Tools.current = Tool.Move;
                var response = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "set_active_tool", ["toolName"] = name }));
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(Tool.Move, UnityEditor.Tools.current);
            }
            finally
            {
                UnityEditor.Tools.current = original;
            }
        }

        [TestCase("Move")]
        [TestCase("move")]
        [TestCase("1")]
        public void DefinedToolNameAndNumericValue_RemainSupported(string name)
        {
            Tool original = UnityEditor.Tools.current;
            try
            {
                var response = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "set_active_tool", ["toolName"] = name }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(Tool.Move, UnityEditor.Tools.current);
            }
            finally
            {
                UnityEditor.Tools.current = original;
            }
        }
    }
}
