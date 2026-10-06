using System;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ConsoleProjectionTests
    {
        [TestCase("json")]
        [TestCase("detailed")]
        public void Projection_PreservesFullBodyAndSeverity(string format)
        {
            // Given a uniquely owned multiline log, independent of existing console contents.
            string marker = "ConsoleProjection_" + Guid.NewGuid().ToString("N");
            string body = marker + "\n\nFull details " + new string('x', 1024);
            Debug.Log(body);
            var parameters = new JObject
            {
                ["action"] = "get", ["types"] = new JArray("all"), ["format"] = format,
                ["filterText"] = marker, ["pageSize"] = 1, ["fields"] = new JArray("type", "message")
            };
            // When the production handler selects only the mandatory fields.
            var response = JObject.FromObject(ReadConsole.HandleCommand(parameters));
            // Then omitted details are absent and the complete body is retained.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var items = (JArray)response["data"]["items"];
            Assert.AreEqual(1, items.Count);
            CollectionAssert.AreEqual(new[] { "type", "message" }, ((JObject)items[0]).Properties().Select(p => p.Name).ToArray());
            Assert.AreEqual("Log", items[0].Value<string>("type"));
            Assert.AreEqual(body, items[0].Value<string>("message"));
        }

        [TestCase("[\"message\"]", "json", false)]
        [TestCase("[\"type\",\"message\",\"bad\"]", "json", false)]
        [TestCase("[\"type\",\"message\",\"type\"]", "json", false)]
        [TestCase("[\"type\",\"message\",\"stackTrace\"]", "json", false)]
        [TestCase("[\"type\",\"message\"]", "plain", false)]
        public void InvalidProjection_ReturnsFailure(string fields, string format, bool includeStacktrace)
        {
            // Given contradictory or unsupported selection parameters.
            var parameters = new JObject
            {
                ["action"] = "get", ["format"] = format, ["fields"] = fields,
                ["includeStacktrace"] = includeStacktrace
            };
            // When the native boundary validates the request.
            var response = JObject.FromObject(ReadConsole.HandleCommand(parameters));
            // Then the tool reports failure rather than silently dropping a requested field.
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
        }

        [Test]
        public void OmittedProjection_PreservesStructuredSchema()
        {
            // Given a uniquely owned entry and no projection parameter.
            string marker = "ConsoleProjectionLegacy_" + Guid.NewGuid().ToString("N");
            Debug.Log(marker);
            // When reading the existing structured format.
            var response = JObject.FromObject(ReadConsole.HandleCommand(new JObject
            {
                ["action"] = "get", ["types"] = new JArray("all"), ["format"] = "json",
                ["filterText"] = marker, ["pageSize"] = 1
            }));
            // Then all five legacy keys remain, including the null stack placeholder.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var item = (JObject)response["data"]["items"][0];
            CollectionAssert.AreEqual(new[] { "type", "message", "file", "line", "stackTrace" }, item.Properties().Select(p => p.Name).ToArray());
            Assert.AreEqual(JTokenType.Null, item["stackTrace"].Type);
        }
    }
}
