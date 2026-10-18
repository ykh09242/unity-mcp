using System;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class TransportJsonTests
    {
        [TestCase("2026-10-18T01:02:03Z")]
        [TestCase("2026-10-18T01:02:03.1234567Z")]
        [TestCase("2026-10-18T01:02:03+09:00")]
        [TestCase("2026-10-18T01:02:03-05:30")]
        [TestCase("2026-10-18")]
        [TestCase("/Date(1198908717056)/")]
        public void RawLiteralsStayStringsAcrossCommandEnvelopeAndAsyncResponse(string literal)
        {
            string quoted = JsonConvert.ToString(literal);
            string parameters = "{\"text\":" + quoted + ",\"nested\":{\"items\":[" + quoted + ",{\"text\":" + quoted + "}]}}";
            var command = TransportJson.DeserializeCommand("{\"type\":\"literal_probe\",\"params\":" + parameters + "}");
            Assert.AreEqual("literal_probe", command.type);
            AssertLiteral(command.@params, literal);
            var envelope = TransportJson.ParseObject("{\"type\":\"execute\",\"id\":" + quoted + ",\"name\":\"literal_probe\",\"params\":" + parameters + "}");
            Assert.AreEqual(JTokenType.String, envelope["id"].Type);
            Assert.AreEqual(literal, (string)envelope["id"]);
            AssertLiteral((JObject)envelope["params"], literal);
            var response = TransportCommandResponse.FromJson("{\"status\":\"success\",\"result\":" + parameters + "}");
            AssertLiteral((JObject)((JObject)response.Payload)["result"], literal);
            AssertLiteral((JObject)TransportJson.ParseObject(response.ToJson())["result"], literal);
        }

        private static void AssertLiteral(JObject value, string expected)
        {
            foreach (JToken token in new[] { value["text"], value["nested"]["items"][0], value["nested"]["items"][1]["text"] })
            {
                Assert.AreEqual(JTokenType.String, token.Type);
                Assert.AreEqual(expected, (string)token);
            }
        }

        [Test]
        public void EscapedTextAndMixedScalarKindsRemainUnchanged()
        {
            const string raw =
                "{\"type\":\"literal_probe\",\"params\":{\"text\":\"2026-10-18T01:02:03\\u005A\",\"integer\":9007199254740993,\"float\":1.25,\"yes\":true,\"no\":false,\"nil\":null}}";
            var parameters = TransportJson.DeserializeCommand(raw).@params;
            Assert.AreEqual(JTokenType.String, parameters["text"].Type);
            Assert.AreEqual("2026-10-18T01:02:03Z", (string)parameters["text"]);
            Assert.AreEqual(JTokenType.Integer, parameters["integer"].Type);
            Assert.AreEqual(9007199254740993L, (long)parameters["integer"]);
            Assert.AreEqual(JTokenType.Float, parameters["float"].Type);
            Assert.AreEqual(1.25, (double)parameters["float"]);
            Assert.AreEqual(JTokenType.Boolean, parameters["yes"].Type);
            Assert.AreEqual(true, (bool)parameters["yes"]);
            Assert.AreEqual(false, (bool)parameters["no"]);
            Assert.AreEqual(JTokenType.Null, parameters["nil"].Type);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("{")]
        [TestCase("{} {}")]
        [TestCase("{} trailing")]
        [TestCase("{} /* tail */")]
        [TestCase("[]")]
        [TestCase("null")]
        [TestCase("42")]
        [TestCase("{\"type\":\"probe\",\"params\":null}")]
        [TestCase("{\"type\":\"probe\"}")]
        [TestCase("{\"type\":\"probe\",\"params\":[]}")]
        public void ExistingInvalidRootNullAndCompletionBehaviorIsPreserved(string raw)
        {
            Assert.AreEqual(Outcome(() => JsonConvert.DeserializeObject<Command>(raw)), Outcome(() => TransportJson.DeserializeCommand(raw)));
            Assert.AreEqual(Outcome(() => JObject.Parse(raw)), Outcome(() => TransportJson.ParseObject(raw)));
            Assert.AreEqual(Outcome(() => JToken.Parse(raw)), Outcome(() => TransportJson.ParseToken(raw)));
        }

        private static string Outcome(Func<object> read)
        {
            try
            {
                return "value:" + JsonConvert.SerializeObject(read());
            }
            catch (Exception error)
            {
                return "error:" + error.GetType().FullName;
            }
        }

        [Test]
        public void TypedReadRetainsDefaultConverterButOverridesItsDateReadSetting()
        {
            var original = JsonConvert.DefaultSettings;
            var converter = new CommandConverter();
            try
            {
                JsonConvert.DefaultSettings = () => new JsonSerializerSettings { DateParseHandling = DateParseHandling.DateTime, Converters = { converter } };
                var value = TransportJson.DeserializeCommand("{\"type\":\"probe\",\"params\":{\"text\":\"2026-10-18T01:02:03+09:00\"}}");
                Assert.AreEqual(1, converter.Reads);
                Assert.AreEqual("converted:probe", value.type);
                Assert.AreEqual(JTokenType.String, value.@params["text"].Type);
                Assert.AreEqual("2026-10-18T01:02:03+09:00", (string)value.@params["text"]);
            }
            finally
            {
                JsonConvert.DefaultSettings = original;
            }
        }

        private sealed class CommandConverter : JsonConverter
        {
            internal int Reads;
            public override bool CanWrite => false;

            public override bool CanConvert(Type type) => type == typeof(Command);

            public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer)
            {
                Reads++;
                var value = JObject.Load(reader);
                return new Command { type = "converted:" + (string)value["type"], @params = (JObject)value["params"] };
            }

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer) => throw new NotSupportedException();
        }
    }
}
