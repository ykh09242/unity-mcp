using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class NestedBatchSecurityTests
    {
        [TestCase("batch_execute")]
        [TestCase("BATCH_EXECUTE")]
        public async Task NestedBatchIsRejectedBeforeAnyCommandExecutes(string name)
        {
            // An unknown command would be reported in data.results if dispatch began.
            var response = JObject.FromObject(
                await BatchExecute.HandleCommand(
                    new JObject
                    {
                        ["commands"] = new JArray(
                            new JObject { ["tool"] = "must_not_dispatch" },
                            new JObject
                            {
                                ["tool"] = name,
                                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "get_project_info" }) },
                            }
                        ),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("Nested", response.ToString());
            Assert.IsNull(response.SelectToken("data.results"));
        }
    }
}
