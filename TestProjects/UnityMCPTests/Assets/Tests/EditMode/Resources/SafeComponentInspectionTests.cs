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

    public sealed class InspectionGetterProbe : MonoBehaviour
    {
        public int Number = 17;
        public int[] ManyValues;
        public InspectionEqualityValue Equality;

        [SerializeField]
        private int privateNumber = 19;
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
            Assert.AreEqual(19, result.SelectToken("properties.privateNumber").Value<int>());
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
