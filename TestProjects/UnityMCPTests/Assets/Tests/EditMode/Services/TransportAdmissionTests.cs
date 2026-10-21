using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class TransportAdmissionTests
    {
        [Test]
        public void AggregateBudgetStopsEnumerationAndDisposesItsIterator()
        {
            int visited = 0;
            bool disposed = false;
            string text = new string('x', 512 * 1024);
            IEnumerable<string> Items()
            {
                try
                {
                    for (int i = 0; i < 200; i++)
                    {
                        visited++;
                        yield return text;
                    }
                }
                finally
                {
                    disposed = true;
                }
            }
            var response = TransportCommandResponse.FromObject(Items());
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
            Assert.Less(visited, 200);
            Assert.IsTrue(disposed);
        }

        [TestCase("")]
        [TestCase("{} {}")]
        [TestCase("{")]
        public void InvalidAsyncJsonReturnsBoundedError(string json)
        {
            var response = TransportCommandResponse.FromJson(json);
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
        }

        [Test]
        public void ScalarKindsAndConverterTokenCopiesPreserveAdmittedWireValues()
        {
            var token = new JObject
            {
                ["null"] = JValue.CreateNull(),
                ["integer"] = long.MaxValue,
                ["real"] = 1.25,
                ["bytes"] = new JValue(new byte[] { 0, 255, 127 }),
                ["date"] = new JValue(new DateTime(2026, 10, 20, 1, 2, 3, DateTimeKind.Utc)),
            };
            Assert.AreEqual(JsonConvert.SerializeObject(token), TransportCommandResponse.FromObject(token).ToJson());
            Assert.AreEqual(JsonConvert.SerializeObject(new { Item = token }), TransportCommandResponse.FromObject(new { Item = token }).ToJson());
        }

        [Test]
        public void NodeBudgetStopsLargeEnumerablesBeforeTheServerWouldRefuseThem()
        {
            int visited = 0;
            IEnumerable<int> Items()
            {
                for (int i = 0; i < BoundedResponseJson.MaxNodes * 2; i++)
                {
                    visited++;
                    yield return i;
                }
            }
            var response = TransportCommandResponse.FromObject(Items());
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
            Assert.Less(visited, BoundedResponseJson.MaxNodes * 2);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RawJsonCannotBypassDepthAdmission(bool nested)
        {
            var raw = new JRaw(new string('[', 128) + "0" + new string(']', 128));
            object payload = nested ? new { Item = raw } : (object)raw;
            var response = TransportCommandResponse.FromObject(payload);
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
        }

        [Test]
        public void Utf8CountingPreservesSurrogateBoundarySerialization()
        {
            foreach (string value in new[] { "\ud83d", "\ude80", "\ud83d\ude80" })
            {
                var payload = new { Value = value };
                Assert.AreEqual(JsonConvert.SerializeObject(payload), TransportCommandResponse.FromObject(payload).ToJson());
            }
        }

        private sealed class Values
        {
            internal int Reads;
            internal string Text;
            public string Value
            {
                get
                {
                    Reads++;
                    return Text;
                }
            }
        }

        [TestCase('x', 65 * 1024 * 1024)]
        [TestCase('\u6f22', 22 * 1024 * 1024)]
        public void OversizedValuesAreRefusedDuringFreeze(char character, int count)
        {
            var value = new Values { Text = new string(character, count) };
            var response = TransportCommandResponse.FromObject(value);
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
            Assert.AreEqual(1, value.Reads);
            Assert.Less(response.ToJson().Length, 1024);
        }

        [Test]
        public void ExcessiveDepthIsRefusedBeforeSerializationRecursion()
        {
            JToken root = new JValue(1);
            for (int i = 0; i < 128; i++)
                root = new JArray(root);
            var response = TransportCommandResponse.FromObject(root);
            Assert.AreEqual("error", (string)(response.Payload as JObject)?["status"]);
        }

        [Test]
        public void AcceptedValuesKeepUnicodeEscapesAndEvaluateGetterOnce()
        {
            var value = new Values { Text = "text\n\t\"\\\u6f22\U0001f680" };
            var response = TransportCommandResponse.FromObject(value);
            string expected = JsonConvert.SerializeObject(new { Value = value.Text });
            Assert.AreEqual(expected, response.ToJson());
            Assert.AreEqual(expected, response.ToJson());
            Assert.AreEqual(1, value.Reads);
        }
    }
}
