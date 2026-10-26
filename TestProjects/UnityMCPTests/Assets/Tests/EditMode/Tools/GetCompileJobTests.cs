using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class GetCompileJobTests
    {
        [TestCase(null)]
        [TestCase("not-an-id")]
        [TestCase("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")]
        public void InvalidIdentityIsRejectedWithoutMutation(string id)
        {
            int before = EditorStateCache.CompileCount;
            var result = ToJObject(GetCompileJob.HandleCommand(new JObject { ["compile_id"] = id }));
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual(before, EditorStateCache.CompileCount);
        }

        [Test]
        public void UnknownIdentityIsReadOnlyAndNeverReturnsLatestJob()
        {
            int before = EditorStateCache.CompileCount;
            var result = ToJObject(GetCompileJob.HandleCommand(new JObject { ["compile_id"] = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" }));
            Assert.IsTrue((bool)result["success"]);
            Assert.AreEqual("unknown", (string)result["data"]["outcome"]);
            Assert.AreEqual("result_unavailable", (string)result["data"]["reason"]);
            Assert.IsNull(result["data"]["native_coordination"], "The legacy strict compile ledger shape remains unchanged.");
            Assert.AreEqual("owned_request_pipeline_events", (string)result["native_coordination"]["scope"]);
            Assert.AreEqual(before, EditorStateCache.CompileCount);
        }
    }
}
