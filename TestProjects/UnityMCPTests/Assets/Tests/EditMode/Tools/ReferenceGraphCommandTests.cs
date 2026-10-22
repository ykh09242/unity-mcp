using System;
using System.Threading;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ReferenceGraphCommandTests
    {
        private static JObject Call(JObject request) => JObject.FromObject(ManageReferenceGraph.HandleCommand(request));

        [Test]
        public void IdentityIsReturnedAndRequiredBeforeReadingPages()
        {
            JObject status = Call(new JObject { ["action"] = "status" });
            Assert.That(status.Value<bool>("success"), Is.True, status.ToString());
            string instance = status["data"].Value<string>("instance_id");
            string project = status["data"].Value<string>("project_id");
            Assert.That(instance, Is.Not.Empty);
            Assert.That(project, Is.Not.Empty);
            JObject request = new JObject { ["action"] = "inventory", ["page_size"] = 1 };
            Assert.That(Call(request).Value<string>("error"), Is.EqualTo("reference_scope_changed"));
            request["expected_instance_id"] = instance;
            request["expected_project_id"] = "another-project";
            Assert.That(Call(request).Value<string>("error"), Is.EqualTo("reference_scope_changed"));
            request["expected_project_id"] = project;
            JObject result = Call(request);
            Assert.That(result.Value<bool>("success"), Is.True, result.ToString());
            Assert.That(result["data"].Value<string>("instance_id"), Is.EqualTo(instance));
            Assert.That(result["data"].Value<string>("project_id"), Is.EqualTo(project));
            Assert.That(result["data"]["assets"], Is.TypeOf<JArray>());
        }

        [TestCase("user_id")]
        [TestCase("database")]
        [TestCase("root")]
        public void CollectorRejectsServerStateAndFilesystemAuthority(string field)
        {
            JObject result = Call(new JObject { ["action"] = "status", [field] = "unexpected" });
            Assert.That(result.Value<bool>("success"), Is.False);
        }

        [Test]
        public void CancellationDoesNotRunCollection()
        {
            using (var source = new CancellationTokenSource())
            {
                source.Cancel();
                Assert.Throws<OperationCanceledException>(() => ManageReferenceGraph.HandleCommand(new JObject { ["action"] = "status" }, source.Token));
            }
        }

        [Test]
        public void NumericAndNullIdentityFieldsAreRejected()
        {
            Assert.That(Call(new JObject { ["action"] = "changes", ["after"] = "0" }).Value<bool>("success"), Is.False);
            Assert.That(Call(new JObject { ["action"] = "status", ["expected_instance_id"] = JValue.CreateNull() }).Value<bool>("success"), Is.False);
        }
    }
}
