using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class BatchResponseContractTests
    {
        private static bool Classify(object result) => (bool)typeof(BatchExecute)
            .GetMethod("DetermineCallSucceeded", BindingFlags.NonPublic | BindingFlags.Static)
            .Invoke(null, new[] { result });

        private class ExpensivePayload { public object Data => throw new InvalidOperationException("Do not serialize status payload."); }

        [Test]
        public void InvalidToolNameRejectsLargeParametersWithoutCloningThem()
        {
            JObject Request(int valueCount)
            {
                var values = new JArray();
                for (int index = 0; index < valueCount; index++) values.Add(index);
                return new JObject { ["commands"] = new JArray(new JObject {
                    ["tool"] = "", ["params"] = new JObject { ["value"] = values } }) };
            }
            var small = Request(0);
            var large = Request(50000);
            BatchExecute.HandleCommand(small).GetAwaiter().GetResult();
            BatchExecute.HandleCommand(large).GetAwaiter().GetResult();

            long Allocations(JObject request)
            {
                long before = GC.GetAllocatedBytesForCurrentThread();
                for (int index = 0; index < 3; index++)
                    BatchExecute.HandleCommand(request).GetAwaiter().GetResult();
                return (GC.GetAllocatedBytesForCurrentThread() - before) / 3;
            }

            long extraBytes = Allocations(large) - Allocations(small);
            Assert.Less(extraBytes, 262144L, "Rejected params should not allocate a clone proportional to their size.");
            var response = JObject.FromObject(BatchExecute.HandleCommand(large).GetAwaiter().GetResult());
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(1, response.SelectToken("data.callFailureCount").Value<int>());
            StringAssert.Contains("non-empty", response.SelectToken("data.results[0].error").ToString());
            Assert.AreEqual(50000, ((JArray)large.SelectToken("commands[0].params.value")).Count);
        }

        [Test]
        public void NormalizationPreservesNestedKeysAndIsolatesOriginalInput()
        {
            var source = new JObject {
                ["search_method"] = "by_name",
                ["value"] = new JObject { ["m_PersistentCalls"] = new JArray(1, 2) }
            };
            var before = source.DeepClone();
            var normalized = (JObject)typeof(BatchExecute)
                .GetMethod("NormalizeParameterKeys", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { source });
            Assert.AreEqual("by_name", normalized.Value<string>("searchMethod"));
            Assert.IsNull(normalized["search_method"]);
            Assert.IsNotNull(normalized.SelectToken("value.m_PersistentCalls"));
            Assert.IsTrue(JToken.DeepEquals(source["value"], normalized["value"]));
            ((JArray)normalized.SelectToken("value.m_PersistentCalls")).Add(3);
            Assert.IsTrue(JToken.DeepEquals(before, source), "Mutating forwarded params must not mutate the original batch input.");
        }

        [Test]
        public void ExplicitBooleanStatusAndRawPayloadsAreClassifiedWithoutSerialization()
        {
            Assert.IsFalse(Classify(new { success = false, message = "failed" }));
            Assert.IsFalse(Classify(new Dictionary<string, object> { ["success"] = false }));
            Assert.IsFalse(Classify(new ErrorResponse("failed")));
            Assert.IsFalse(Classify(new JObject { ["success"] = false }));
            Assert.IsTrue(Classify(new { success = true, data = new ExpensivePayload() }));
            Assert.IsTrue(Classify(new Dictionary<string, bool> { ["success"] = true }));
            Assert.IsTrue(Classify(new SuccessResponse("done")));
            Assert.IsTrue(Classify(new JObject { ["success"] = true }));
            Assert.IsTrue(Classify(null));
            Assert.IsTrue(Classify(new { data = new ExpensivePayload() }));
            Assert.IsTrue(Classify(new { success = "false" }));
            Assert.IsTrue(Classify(new Dictionary<string, object> { ["success"] = "false" }));
            Assert.IsTrue(Classify(new JObject()));
            Assert.IsTrue(Classify(new JArray(1, 2)));
            Assert.IsTrue(Classify(new JValue(42)));
            Assert.IsTrue(Classify(JValue.CreateNull()));
        }

        [TestCase("manage_animation", true)]
        [TestCase("manage_animation", false)]
        [TestCase("manage_vfx", true)]
        [TestCase("manage_vfx", false)]
        public async Task FailedDomainCommandRetainsDiagnosticsAndHonorsFailFast(string command, bool failFast)
        {
            CommandRegistry.Initialize();
            var discovery = MCPServiceLocator.ToolDiscovery;
            bool enabled = discovery.IsToolEnabled(command);
            bool vfxEnabled = discovery.IsToolEnabled("manage_vfx");
            discovery.SetToolEnabled(command, true);
            discovery.SetToolEnabled("manage_vfx", true);
            try
            {
                var result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject {
                    ["failFast"] = failFast,
                    ["commands"] = new JArray(
                        new JObject { ["tool"] = command, ["params"] = new JObject { ["action"] = "invalid" } },
                        new JObject { ["tool"] = "manage_vfx", ["params"] = new JObject { ["action"] = "ping" } })
                }));
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.AreEqual(1, result.SelectToken("data.callFailureCount").Value<int>());
                Assert.AreEqual(failFast ? 0 : 1, result.SelectToken("data.callSuccessCount").Value<int>());
                Assert.AreEqual(failFast ? 1 : 2, ((JArray)result.SelectToken("data.results")).Count);
                Assert.IsFalse(result.SelectToken("data.results[0].callSucceeded").Value<bool>());
                StringAssert.Contains("Unknown action", result.SelectToken("data.results[0].result.message").ToString());
                if (!failFast)
                    Assert.AreEqual("manage_vfx", result.SelectToken("data.results[1].result.tool").ToString());
            }
            finally
            {
                discovery.SetToolEnabled(command, enabled);
                discovery.SetToolEnabled("manage_vfx", vfxEnabled);
            }
        }
    }
}
