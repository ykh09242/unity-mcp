using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class PayloadLoggingSecurityTests
    {
        private const string Sentinel = "MCP_SECRET_SENTINEL_73c4";
        private const BindingFlags PrivateStatic = BindingFlags.Static | BindingFlags.NonPublic;
        private readonly List<string> messages = new List<string>();
        private bool hadDebugPreference;
        private bool debugPreference;

        [SetUp]
        public void CaptureLogs()
        {
            hadDebugPreference = EditorPrefs.HasKey(EditorPrefKeys.DebugLogs);
            debugPreference = EditorPrefs.GetBool(EditorPrefKeys.DebugLogs, false);
            McpLog.SetDebugLoggingEnabled(true);
            Application.logMessageReceived += Capture;
        }

        [TearDown]
        public void RestoreLogs()
        {
            Application.logMessageReceived -= Capture;
            McpLog.SetDebugLoggingEnabled(debugPreference);
            if (!hadDebugPreference)
                EditorPrefs.DeleteKey(EditorPrefKeys.DebugLogs);
            messages.Clear();
        }

        private void Capture(string message, string stack, LogType type) => messages.Add(message);

        private void AssertNoPayload()
        {
            foreach (string message in messages)
                StringAssert.DoesNotContain(Sentinel, message);
        }

        [Test]
        public void PropertyFailureKeepsPayloadAndExceptionOutOfLogs()
        {
            var converter = new SecretThrowingConverter();
            UnityJsonSerializer.Instance.Converters.Insert(0, converter);
            try
            {
                LogAssert.Expect(LogType.Error, new Regex(@"\[PropertyConversion\] Property value conversion failed\."));
                Assert.Throws<InvalidOperationException>(() => PropertyConversion.ConvertToType(new JObject { [Sentinel] = Sentinel }, typeof(SecretValue)));
                Assert.AreEqual(1, messages.Count);
                AssertNoPayload();
            }
            finally
            {
                UnityJsonSerializer.Instance.Converters.Remove(converter);
            }
        }

        [TestCase("Vector2")]
        [TestCase("Vector3")]
        [TestCase("Vector4")]
        [TestCase("Quaternion")]
        [TestCase("Color")]
        [TestCase("Gradient")]
        [TestCase("AnimationCurve")]
        [TestCase("Rect")]
        [TestCase("Bounds")]
        public void ParseFailureLogsOnlyStableType(string type)
        {
            var invalid = new SecretThrowingValue();
            JToken input = type switch
            {
                "Gradient" => new JObject { ["startColor"] = new JArray(1, 1, 1), ["startAlpha"] = invalid },
                "AnimationCurve" => new JObject { ["start"] = invalid },
                "Bounds" => new JObject { ["center"] = new JArray(invalid, 0, 0), ["size"] = new JArray(1, 1, 1) },
                _ => new JArray(invalid, 0, 0, 1),
            };
            invalid.Armed = true;
            string failingType = type == "Bounds" ? "Vector3" : type;
            LogAssert.Expect(LogType.Warning, new Regex(@"\[VectorParsing\] Failed to parse " + failingType + @" value\."));
            object result = type switch
            {
                "Vector2" => VectorParsing.ParseVector2(input),
                "Vector3" => VectorParsing.ParseVector3(input),
                "Vector4" => VectorParsing.ParseVector4(input),
                "Quaternion" => VectorParsing.ParseQuaternion(input),
                "Color" => VectorParsing.ParseColor(input),
                "Gradient" => VectorParsing.ParseGradient(input),
                "AnimationCurve" => VectorParsing.ParseAnimationCurve(input),
                "Rect" => VectorParsing.ParseRect(input),
                "Bounds" => VectorParsing.ParseBounds(input),
                _ => throw new ArgumentOutOfRangeException(nameof(type)),
            };
            Assert.IsNull(result);
            Assert.AreEqual(1, messages.Count);
            AssertNoPayload();
        }

        [Test]
        public void SuccessfulConversionDoesNotLogPayloadInDebugMode()
        {
            Assert.AreEqual(Sentinel, PropertyConversion.ConvertTo<string>(new JValue(Sentinel)));
            Assert.AreEqual(
                Vector3.one,
                VectorParsing.ParseVector3(
                    new JObject
                    {
                        ["x"] = 1,
                        ["y"] = 1,
                        ["z"] = 1,
                        [Sentinel] = Sentinel,
                    }
                )
            );
            Assert.IsEmpty(messages);
        }

        [Test]
        public void InvalidScalarRejectionDoesNotLogPayload()
        {
            Assert.Throws<ArgumentException>(() => VectorParsing.ParseVector3(new JArray(Sentinel, 0, 0)));
            Assert.IsEmpty(messages);
        }

        [TestCase("Gradient")]
        [TestCase("AnimationCurve")]
        public void MalformedNestedKeyLogsNoPayload(string type)
        {
            LogAssert.Expect(LogType.Warning, new Regex(@"\[VectorParsing\] Failed to parse " + type + @" value\."));
            object result =
                type == "Gradient"
                    ? (object)VectorParsing.ParseGradient(new JObject { ["colorKeys"] = new JArray(Sentinel) })
                    : VectorParsing.ParseAnimationCurve(new JObject { ["keys"] = new JArray(Sentinel) });
            Assert.IsNull(result);
            Assert.AreEqual(1, messages.Count);
            AssertNoPayload();
        }

        [Test]
        public void MaterialColorFailureDoesNotLogPropertyOrException()
        {
            var serializer = new JsonSerializer();
            serializer.Converters.Add(new SecretThrowingConverter(typeof(Color)));
            LogAssert.Expect(LogType.Warning, new Regex(@"\[MaterialOps\] Failed to parse color value\."));

            Assert.Throws<InvalidOperationException>(() => MaterialOps.ParseColor(new JObject { [Sentinel] = Sentinel }, serializer));

            Assert.AreEqual(1, messages.Count);
            AssertNoPayload();
        }

        [Test]
        public void StdioDebugFrameLogsOnlyLength()
        {
            string payload = new JObject { [Sentinel] = Sentinel }.ToString();
            InvokeStdioLog("LogReceivedCommand", payload);
            Assert.AreEqual(1, messages.Count);
            StringAssert.Contains("characters: " + payload.Length, messages[0]);
            AssertNoPayload();
        }

        [TestCase(true)]
        [TestCase(false)]
        public void StdioHandlerFailureDoesNotLogExceptionMessage(bool benign)
        {
            Exception exception = benign ? new IOException(Sentinel) : new InvalidOperationException(Sentinel);
            if (!benign)
                LogAssert.Expect(LogType.Error, new Regex("Client handler failed"));
            InvokeStdioLog("LogClientHandlerFailure", exception);
            Assert.AreEqual(1, messages.Count);
            AssertNoPayload();
        }

        private static void InvokeStdioLog(string method, object value) =>
            typeof(StdioBridgeHost).GetMethod(method, PrivateStatic).Invoke(null, new[] { value });

        private class SecretValue { }

        private class SecretThrowingValue : JValue
        {
            internal SecretThrowingValue()
                : base(Sentinel) { }

            internal bool Armed;
            private int reads;

            // Inject one unexpected token-reader failure without changing production parsing.
            // Subsequent reads permit the pre-fix logger to serialize the secret-bearing token.
            public override JTokenType Type => Armed && ++reads == 2 ? throw new InvalidOperationException(Sentinel) : base.Type;
        }

        private class SecretThrowingConverter : JsonConverter
        {
            private readonly Type target;

            internal SecretThrowingConverter(Type target = null) => this.target = target ?? typeof(SecretValue);

            public override bool CanConvert(Type objectType) => objectType == target;

            public override object ReadJson(JsonReader reader, Type objectType, object existingValue, JsonSerializer serializer) =>
                throw new InvalidOperationException(Sentinel);

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer) => throw new NotSupportedException();
        }
    }
}
