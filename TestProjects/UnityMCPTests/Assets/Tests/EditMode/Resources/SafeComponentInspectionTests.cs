using System;
using System.Runtime.Serialization;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Resources
{
    [Serializable]
    public sealed class InspectionNestedValue
    {
        public int Number = 23;

        [NonSerialized]
        public int Calls;
        public int Computed
        {
            get
            {
                Calls++;
                return 99;
            }
        }

        [OnSerializing]
        private void BeforeSerialization(StreamingContext context) => Calls++;

        public bool ShouldSerializeNumber()
        {
            Calls++;
            return true;
        }
    }

    [JsonConverter(typeof(InspectionUserConverter))]
    public sealed class InspectionConvertedValue
    {
        public int Number = 37;
    }

    public sealed class InspectionUserConverter : JsonConverter
    {
        public static int Calls;

        public override bool CanConvert(Type type) => type == typeof(InspectionConvertedValue);

        public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer) => throw new NotSupportedException();

        public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
        {
            Calls++;
            writer.WriteValue("user code ran");
        }
    }

    public sealed class InspectionEqualityValue
    {
        public static int Calls;
        public InspectionEqualityValue Next;

        public override bool Equals(object other)
        {
            Calls++;
            return ReferenceEquals(this, other);
        }

        public override int GetHashCode()
        {
            Calls++;
            return System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        }
    }

    [JsonConverter(typeof(InspectionHookConverter))]
    public enum InspectionAttributedEnum
    {
        One = 1,
    }

    [JsonObject(ItemConverterType = typeof(InspectionHookConverter))]
    public sealed class InspectionAttributedValue
    {
        public int Number = 43;
    }

    public sealed class InspectionHookConverter : JsonConverter
    {
        public static int Calls;

        public InspectionHookConverter() => Calls++;

        public override bool CanWrite
        {
            get
            {
                Calls++;
                return true;
            }
        }

        public override bool CanConvert(Type type)
        {
            Calls++;
            return true;
        }

        public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer) => throw new NotSupportedException();

        public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
        {
            Calls++;
            writer.WriteValue(911);
        }
    }

    public sealed class InspectionCustomToken : JValue
    {
        public static int Calls;
        public int Number = 47;

        public InspectionCustomToken()
            : base(53) { }

        public override JTokenType Type
        {
            get
            {
                Calls++;
                return base.Type;
            }
        }

        public override void WriteTo(JsonWriter writer, params JsonConverter[] converters)
        {
            Calls++;
            base.WriteTo(writer, converters);
        }
    }

    public sealed class InspectionRawPayload
    {
        public static int Calls;

        public override string ToString()
        {
            Calls++;
            return "unsafe raw payload";
        }
    }

    public sealed class InspectionGetterProbe : MonoBehaviour
    {
        public int Number = 17;
        public int[] ManyValues;
        public byte[] Binary;
        public object CustomValue;
        public JToken Token;
        public InspectionEqualityValue Equality;

        [SerializeField]
        private int privateNumber = 19;

        public int ReadPrivateNumberForTest() => privateNumber;

        public InspectionNestedValue Nested = new InspectionNestedValue();
        public InspectionConvertedValue Converted = new InspectionConvertedValue();

        [NonSerialized]
        public int Calls;
        public int Computed
        {
            get
            {
                Calls++;
                return 41;
            }
        }
        public int Throwing
        {
            get
            {
                Calls++;
                throw new InvalidOperationException("The inspector invoked user code.");
            }
        }
    }

    public sealed class SafeComponentInspectionTests
    {
        private GameObject owned;
        private InspectionGetterProbe probe;

        [SetUp]
        public void SetUp()
        {
            owned = new GameObject("SafeInspection_" + Guid.NewGuid().ToString("N"));
            probe = owned.AddComponent<InspectionGetterProbe>();
            InspectionUserConverter.Calls = 0;
        }

        [TearDown]
        public void TearDown() => Object.DestroyImmediate(owned);

        private JObject Parameters() =>
            new JObject { ["instanceID"] = owned.GetInstanceIDCompat(), ["componentName"] = typeof(InspectionGetterProbe).FullName };

        [Test]
        public void DirectDefaultReadDoesNotExecuteUserGettersOrNestedSerializationHooks()
        {
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, probe.Calls, "Default inspection invoked a component getter.");
            Assert.AreEqual(0, probe.Nested.Calls, "Default inspection invoked nested user code.");
            Assert.AreEqual(0, InspectionUserConverter.Calls, "Default inspection invoked a user converter.");
            Assert.AreEqual(17, result.SelectToken("properties.Number").Value<int>());
            Assert.AreEqual(probe.ReadPrivateNumberForTest(), result.SelectToken("properties.privateNumber").Value<int>());
            Assert.AreEqual(23, result.SelectToken("properties.Nested.Number").Value<int>());
            Assert.AreEqual(37, result.SelectToken("properties.Converted.Number").Value<int>());
            Assert.IsNull(result.SelectToken("properties.Computed"));
            Assert.IsNull(result.SelectToken("properties.Nested.Computed"));
            Assert.IsNotNull(result["skippedProperties"], "Skipped getters must be discoverable.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SingleResourceDefaultsToSafeInspectionWithAndWithoutSelection(bool selected)
        {
            var request = Parameters();
            if (selected)
                request["component_instance_id"] = probe.GetInstanceIDCompat();
            var result = JObject.FromObject(GameObjectComponentResource.HandleCommand(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(17, result.SelectToken("data.component.properties.Number").Value<int>());
            Assert.AreEqual(0, probe.Calls + probe.Nested.Calls + InspectionUserConverter.Calls);
        }

        [Test]
        public void DefaultInspectionPreservesPublicNonSerializedFields()
        {
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, result.SelectToken("properties.Calls")?.Value<int>(), "Public runtime fields remain useful without evaluating getters.");
        }

        [Test]
        public void LargeDefaultFieldArrayKeepsAllValues()
        {
            probe.ManyValues = new int[120000];
            for (int index = 0; index < probe.ManyValues.Length; index++)
                probe.ManyValues[index] = index;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.IsNull(result["serializationError"]);
            var values = (JArray)result.SelectToken("properties.ManyValues");
            Assert.AreEqual(120000, values.Count);
            Assert.AreEqual(119999, values[119999].Value<int>());
            Assert.AreEqual(0, probe.Calls);
        }

        [Test]
        public void DefaultReadDoesNotInvokeCustomEqualityDuringCycleDetection()
        {
            probe.Equality = new InspectionEqualityValue();
            probe.Equality.Next = probe.Equality;
            InspectionEqualityValue.Calls = 0;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, InspectionEqualityValue.Calls);
            Assert.IsNull(result["serializationError"]);
        }

        [Test]
        public void EnumAttributesDoNotCreateOrInvokeConvertersDuringDefaultInspection()
        {
            probe.CustomValue = new System.Collections.Generic.Dictionary<string, object>
            {
                ["direct"] = InspectionAttributedEnum.One,
                ["nested"] = new[] { InspectionAttributedEnum.One },
            };
            InspectionHookConverter.Calls = 0;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, InspectionHookConverter.Calls);
            Assert.AreEqual(1, result.SelectToken("properties.CustomValue.direct").Value<int>());
            Assert.AreEqual(1, result.SelectToken("properties.CustomValue.nested[0]").Value<int>());
        }

        [Test]
        public void ObjectAttributesDoNotConstructItemConvertersDuringDefaultInspection()
        {
            probe.CustomValue = new InspectionAttributedValue();
            InspectionHookConverter.Calls = 0;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, InspectionHookConverter.Calls);
            Assert.AreEqual(43, result.SelectToken("properties.CustomValue.Number").Value<int>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void JsonTokenSubclassesAreInspectedAsFieldsWithoutVirtualCalls(bool nested)
        {
            probe.Token = nested ? new JObject { ["custom"] = new InspectionCustomToken() } : new InspectionCustomToken();
            InspectionCustomToken.Calls = 0;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, InspectionCustomToken.Calls);
            Assert.AreEqual(47, result.SelectToken(nested ? "properties.Token.custom.Number" : "properties.Token.Number").Value<int>());
        }

        [Test]
        public void StandardJsonTokensPreserveValuesAndRawTextWithoutCallingUserToString()
        {
            probe.Token = new JObject
            {
                ["values"] = new JArray(false, 42, null, new JValue(ulong.MaxValue)),
                ["raw"] = new JRaw("{\"value\":7}"),
                ["customRaw"] = new JRaw(new InspectionRawPayload()),
            };
            InspectionRawPayload.Calls = 0;
            var result = JObject.FromObject(GameObjectSerializer.GetComponentData(probe));
            Assert.AreEqual(0, InspectionRawPayload.Calls);
            Assert.AreEqual(ulong.MaxValue, result.SelectToken("properties.Token.values[3]").Value<ulong>());
            Assert.AreEqual("{\"value\":7}", result.SelectToken("properties.Token.raw").Value<string>());
        }

        [TestCase(3)]
        [TestCase(1000001)]
        public void BinaryFieldsKeepTheirBase64WireFormatAndByteCapacity(int length)
        {
            probe.Binary = new byte[length];
            probe.Binary[0] = 1;
            probe.Binary[1] = 2;
            probe.Binary[2] = 3;
            var data = GameObjectSerializer.GetComponentData(probe);
            var result = JObject.Parse(JsonConvert.SerializeObject(data));
            Assert.IsNull(result["serializationError"]);
            Assert.AreEqual(Convert.ToBase64String(probe.Binary), result.SelectToken("properties.Binary").Value<string>());
        }

        [Test]
        public void PropertyPageIncludesUsefulFieldsWithoutGetterEvaluation()
        {
            var request = Parameters();
            request["cursor"] = 1;
            request["pageSize"] = 1;
            request["includeProperties"] = true;
            var result = JObject.FromObject(GameObjectComponentsResource.HandleCommand(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(17, result.SelectToken("data.components[0].properties.Number").Value<int>());
            Assert.AreEqual(0, probe.Calls + probe.Nested.Calls + InspectionUserConverter.Calls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExplicitEvaluationRetainsComputedProperties(bool selected)
        {
            var request = Parameters();
            if (selected)
                request["component_index"] = 0;
            request["evaluate_getters"] = true;
            var result = JObject.FromObject(GameObjectComponentResource.HandleCommand(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(41, result.SelectToken("data.component.properties.Computed").Value<int>());
            Assert.Greater(probe.Calls, 0);
        }

        [Test]
        public void MetadataPageNeverEvaluatesEvenWhenExplicitlyRequested()
        {
            var request = Parameters();
            request["includeProperties"] = false;
            request["evaluate_getters"] = true;
            var result = JObject.FromObject(GameObjectComponentsResource.HandleCommand(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, probe.Calls + probe.Nested.Calls + InspectionUserConverter.Calls);
        }

        [TestCase(1)]
        [TestCase("yes")]
        public void InvalidEvaluationFlagCannotEnableUserCode(object invalid)
        {
            var request = Parameters();
            request["evaluate_getters"] = JToken.FromObject(invalid);
            var result = JObject.FromObject(GameObjectComponentResource.HandleCommand(request));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, probe.Calls + probe.Nested.Calls + InspectionUserConverter.Calls);
        }
    }
}
