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
            using var consent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState("batch_execute");
            Assert.IsTrue(MCPServiceLocator.ToolDiscovery.IsToolEnabled("batch_execute"));
            const string name = "get_project_info";
            var discovery = MCPServiceLocator.ResourceDiscovery;
            Assert.IsNotNull(discovery.GetResourceMetadata(name));
            bool wasEnabled = discovery.IsResourceEnabled(name);
            discovery.SetResourceEnabled(name, false);
            try
            {
                var task = BatchExecute.HandleCommand(
                    new JObject { ["failFast"] = true, ["commands"] = new JArray(new JObject { ["tool"] = name }, new JObject { ["tool"] = name }) }
                );
                while (!task.IsCompleted)
                    yield return null;
                var result = JObject.FromObject(task.Result);
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.AreEqual(1, ((JArray)result.SelectToken("data.results")).Count, result.ToString());
                StringAssert.Contains("disabled", result.SelectToken("data.results[0].result").ToString());
                Assert.AreEqual("failed", (string)result.SelectToken("data.outcomes[0].state"));
                Assert.AreEqual("resource_disabled", (string)result.SelectToken("data.outcomes[0].reason"));
                Assert.AreEqual("not_run", (string)result.SelectToken("data.outcomes[1].state"));
                Assert.AreEqual("fail_fast", (string)result.SelectToken("data.outcomes[1].reason"));
                Assert.AreEqual(1, result.SelectToken("data.notRunCount").Value<int>());
            }
            finally
            {
                discovery.SetResourceEnabled(name, wasEnabled);
            }
        }
    }
}
