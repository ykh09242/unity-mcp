using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageEditorToolContractTests
    {
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
