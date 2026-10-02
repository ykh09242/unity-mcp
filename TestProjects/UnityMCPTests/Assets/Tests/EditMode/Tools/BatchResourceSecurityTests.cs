using System.Collections;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class BatchResourceSecurityTests
    {
        [UnityTest]
        public IEnumerator DisabledResourceCannotExecuteThroughBatch()
        {
            const string name = "get_project_info";
            var discovery = MCPServiceLocator.ResourceDiscovery;
            Assert.IsNotNull(discovery.GetResourceMetadata(name));
            bool wasEnabled = discovery.IsResourceEnabled(name);
            discovery.SetResourceEnabled(name, false);
            try
            {
                var task = BatchExecute.HandleCommand(new JObject
                {
                    ["failFast"] = true,
                    ["commands"] = new JArray(
                        new JObject { ["tool"] = name },
                        new JObject { ["tool"] = name })
                });
                while (!task.IsCompleted) yield return null;
                var result = JObject.FromObject(task.Result);
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("disabled", result.ToString());
                Assert.AreEqual(1, ((JArray)result.SelectToken("data.results")).Count);
            }
            finally { discovery.SetResourceEnabled(name, wasEnabled); }
        }
    }
}
