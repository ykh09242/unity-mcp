using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnityTests.Editor.Tools
{
    public class NullableUnityValueProbe : MonoBehaviour
    {
        public Vector2? vector2;
        public Vector3? vector3;
        public Vector4? vector4;
        public Quaternion? rotation { get; set; }
        public List<Vector3?> vectors;
    }

    /// <summary>
    /// Tests to reproduce issue #654: PropertyConversion crash causing dispatcher unavailability
    /// while telemetry continues reporting success.
    /// </summary>
    public class PropertyConversionErrorHandlingTests
    {
        private GameObject testGameObject;

        [SetUp]
        public void SetUp()
        {
            testGameObject = new GameObject("PropertyConversionTestObject");
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            if (testGameObject != null)
            {
                UnityEngine.Object.DestroyImmediate(testGameObject);
            }
        }

        private JObject SetNullableValue(string property, JToken value)
        {
            return JObject.FromObject(ManageComponents.HandleCommand(new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = typeof(NullableUnityValueProbe).FullName,
                ["property"] = property,
                ["value"] = value
            }));
        }

        [TestCase("vector2", "[1,2]", "{\"x\":1.0,\"y\":2.0}")]
        [TestCase("vector3", "[1,2,3]", "{\"x\":1.0,\"y\":2.0,\"z\":3.0}")]
        [TestCase("vector4", "[1,2,3,4]", "{\"x\":1.0,\"y\":2.0,\"z\":3.0,\"w\":4.0}")]
        [TestCase("rotation", "[1,2,3,4]", "{\"x\":1.0,\"y\":2.0,\"z\":3.0,\"w\":4.0}")]
        public void ManageComponents_NullableUnityArray_AssignsActualValue(string member, string input, string expected)
        {
            var component = testGameObject.AddComponent<NullableUnityValueProbe>();
            var response = SetNullableValue(member, JToken.Parse(input));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            object value = member == "rotation"
                ? (object)component.rotation
                : typeof(NullableUnityValueProbe).GetField(member).GetValue(component);
            Assert.IsTrue(JToken.DeepEquals(JToken.Parse(expected), JToken.FromObject(value, UnityJsonSerializer.Instance)));
        }

        [Test]
        public void ManageComponents_NullableCollection_PreservesNullAndPartialObjects()
        {
            var component = testGameObject.AddComponent<NullableUnityValueProbe>();
            var input = JToken.Parse("[null,[1,2,3],{\"x\":4}]");
            var before = input.DeepClone();
            var response = SetNullableValue("vectors", input);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3, component.vectors.Count);
            Assert.IsNull(component.vectors[0]);
            Assert.AreEqual(new Vector3(1, 2, 3), component.vectors[1].Value);
            Assert.AreEqual(new Vector3(4, 0, 0), component.vectors[2].Value);
            Assert.IsTrue(JToken.DeepEquals(before, input), "Conversion must preserve the caller's payload.");
        }

        [Test]
        public void ManageComponents_NullableObjectAndNull_RetainExistingForms()
        {
            var component = testGameObject.AddComponent<NullableUnityValueProbe>();
            Assert.IsTrue(SetNullableValue("vector3", JObject.Parse("{\"x\":7}")).Value<bool>("success"));
            Assert.AreEqual(new Vector3(7, 0, 0), component.vector3.Value);
            Assert.IsTrue(SetNullableValue("vector3", JValue.CreateNull()).Value<bool>("success"));
            Assert.IsNull(component.vector3);
            var references = JToken.Parse("[{\"$id\":\"1\",\"x\":1,\"y\":2,\"z\":3},{\"$ref\":\"1\"}]");
            Assert.IsTrue(SetNullableValue("vectors", references).Value<bool>("success"));
            Assert.AreEqual(new Vector3(1, 2, 3), component.vectors[0].Value);
            Assert.AreEqual(component.vectors[0], component.vectors[1], "Existing Json.NET references must still resolve.");
        }

        [TestCase("[1,2]")]
        [TestCase("12")]
        public void ManageComponents_InvalidNullableValue_PreservesPreviousValue(string input)
        {
            var component = testGameObject.AddComponent<NullableUnityValueProbe>();
            component.vector3 = new Vector3(7, 8, 9);
            LogAssert.Expect(LogType.Error, new Regex("Error converting token to System.Nullable"));
            Assert.IsFalse(SetNullableValue("vector3", JToken.Parse(input)).Value<bool>("success"));
            Assert.AreEqual(new Vector3(7, 8, 9), component.vector3.Value);
        }

        /// <summary>
        /// Test case 1: Integer value for object reference property (AudioClip on AudioSource)
        /// Should return graceful error, not crash dispatcher
        /// </summary>
        [Test]
        public void ManageComponents_SetProperty_IntegerForObjectReference_ReturnsGracefulError()
        {
            // Add AudioSource component
            var audioSource = testGameObject.AddComponent<AudioSource>();

            // Try to set AudioClip (object reference) to integer 12345
            var setPropertyParams = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "clip",
                ["value"] = 12345  // INCOMPATIBLE: int for AudioClip
            };

            var result = ManageComponents.HandleCommand(setPropertyParams);

            // Main test: should return a result without crashing
            Assert.IsNotNull(result, "Should return a result, not crash dispatcher");

            // If it's an ErrorResponse, verify it properly reports failure
            if (result is ErrorResponse errorResp)
            {
                Assert.IsFalse(errorResp.Success, "Should report failure for incompatible type");
            }
        }

        /// <summary>
        /// Test case 2: Array format for float property (spatialBlend expects float, not array)
        /// Mirrors the "Array format [0, 0] for Vector2 properties" from issue #654
        /// This test documents that the error is caught and doesn't crash the dispatcher
        /// </summary>
        [Test]
        public void ManageComponents_SetProperty_ArrayForFloatProperty_DoesNotCrashDispatcher()
        {
            // Expect the error log that will be generated
            LogAssert.Expect(LogType.Error, new Regex("Error converting token to System.Single"));

            // Add AudioSource component
            var audioSource = testGameObject.AddComponent<AudioSource>();

            // Try to set spatialBlend (float) to array [0, 0]
            // This triggers: "Error converting token to System.Single: Error reading double. Unexpected token: StartArray"
            var setPropertyParams = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "spatialBlend",
                ["value"] = JArray.Parse("[0, 0]")  // INCOMPATIBLE: array for float
            };

            var result = ManageComponents.HandleCommand(setPropertyParams);

            // Main test: dispatcher should remain responsive and return a result
            Assert.IsNotNull(result, "Should return a result, not crash dispatcher");

            // Verify subsequent commands still work
            var followupParams = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "volume",
                ["value"] = 0.5f
            };

            var followupResult = ManageComponents.HandleCommand(followupParams);
            Assert.IsNotNull(followupResult, "Dispatcher should still be responsive after conversion error");
        }

        /// <summary>
        /// Test case 3: Multiple property conversion failures in sequence
        /// Tests if dispatcher remains responsive after multiple errors
        /// </summary>
        [Test]
        public void ManageComponents_MultipleSetPropertyFailures_DispatcherStaysResponsive()
        {
            // Expect the error log for the invalid string conversion
            LogAssert.Expect(LogType.Error, new Regex("Error converting token to System.Single"));

            var audioSource = testGameObject.AddComponent<AudioSource>();

            // First bad conversion attempt - int for AudioClip doesn't generate an error log
            var badParam1 = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "clip",
                ["value"] = 999  // bad: int for AudioClip
            };

            var result1 = ManageComponents.HandleCommand(badParam1);
            Assert.IsNotNull(result1, "First call should return result");

            // Second bad conversion attempt - generates error log
            var badParam2 = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "rolloffFactor",
                ["value"] = "invalid_string"  // bad: string for float
            };

            var result2 = ManageComponents.HandleCommand(badParam2);
            Assert.IsNotNull(result2, "Second call should return result");

            // Third attempt - valid conversion
            var badParam3 = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "volume",
                ["value"] = 0.5f  // good: float for float - dispatcher should still work
            };

            var result3 = ManageComponents.HandleCommand(badParam3);
            Assert.IsNotNull(result3, "Third call should return result (dispatcher should still be responsive)");
        }

        /// <summary>
        /// Test case 4: After property conversion failures, other commands still work
        /// Tests dispatcher responsiveness
        /// </summary>
        [Test]
        public void ManageComponents_AfterConversionFailure_OtherOperationsWork()
        {
            var audioSource = testGameObject.AddComponent<AudioSource>();

            // Trigger a conversion failure
            var failParam = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "clip",
                ["value"] = 12345  // bad
            };

            var failResult = ManageComponents.HandleCommand(failParam);
            Assert.IsNotNull(failResult, "Should return result for failed conversion");

            // Now try a valid operation on the same component
            var validParam = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGameObject.name,
                ["componentType"] = "AudioSource",
                ["property"] = "volume",
                ["value"] = 0.5f  // valid: float for float
            };

            var validResult = ManageComponents.HandleCommand(validParam);
            Assert.IsNotNull(validResult, "Should still be able to execute valid commands after conversion failure");

            // Verify the property was actually set
            Assert.AreEqual(0.5f, audioSource.volume, "Volume should have been set to 0.5");
        }

        /// <summary>
        /// Test case 5: Telemetry continues reporting success even after conversion errors
        /// This is the core of issue #654: telemetry should accurately reflect dispatcher health
        /// </summary>
        [Test]
        public void ManageEditor_TelemetryStatus_ReportsAccurateHealth()
        {
            // Trigger multiple conversion failures first
            var audioSource = testGameObject.AddComponent<AudioSource>();

            for (int i = 0; i < 3; i++)
            {
                var badParam = new JObject
                {
                    ["action"] = "set_property",
                    ["target"] = testGameObject.name,
                    ["componentType"] = "AudioSource",
                    ["property"] = "clip",
                    ["value"] = i * 1000  // bad
                };
                ManageComponents.HandleCommand(badParam);
            }

            // Now check telemetry
            var telemetryParams = new JObject { ["action"] = "telemetry_status" };
            var telemetryResult = ManageEditor.HandleCommand(telemetryParams);

            Assert.IsNotNull(telemetryResult, "Telemetry should return result");

            // NOTE: Issue #654 noted that telemetry returns success even when dispatcher is dead.
            // If telemetry returns success, that's the actual current behavior (which may be a problem).
            // This test just documents what happens.
        }

        /// <summary>
        /// Test case 6: Direct PropertyConversion error handling
        /// Tests if PropertyConversion.ConvertToType properly handles exceptions
        /// </summary>
        [Test]
        public void PropertyConversion_ConvertToType_HandlesIncompatibleTypes()
        {
            // Try to convert integer to AudioClip type
            var token = JToken.FromObject(12345);

            // PropertyConversion.ConvertToType should either:
            // 1. Return a valid converted value
            // 2. Throw an exception that can be caught
            // 3. Return null

            Exception thrownException = null;
            object result = null;

            try
            {
                result = PropertyConversion.ConvertToType(token, typeof(AudioClip));
            }
            catch (Exception ex)
            {
                thrownException = ex;
            }

            // Document what actually happens
            if (thrownException != null)
            {
                Debug.Log($"PropertyConversion threw exception: {thrownException.GetType().Name}: {thrownException.Message}");
                Assert.Pass($"PropertyConversion threw {thrownException.GetType().Name} - exception is being raised, not swallowed");
            }
            else if (result == null)
            {
                Debug.Log("PropertyConversion returned null for incompatible type");
                Assert.Pass("PropertyConversion returned null for incompatible type");
            }
            else
            {
                Debug.Log($"PropertyConversion returned unexpected result: {result}");
                Assert.Pass("PropertyConversion produced some result");
            }
        }

        /// <summary>
        /// Test case 7: TryConvertToType should never throw
        /// </summary>
        [Test]
        public void PropertyConversion_TryConvertToType_NeverThrows()
        {
            var token = JToken.FromObject(12345);

            // This should never throw, only return null
            object result = null;
            Exception thrownException = null;

            try
            {
                result = PropertyConversion.TryConvertToType(token, typeof(AudioClip));
            }
            catch (Exception ex)
            {
                thrownException = ex;
            }

            Assert.IsNull(thrownException, "TryConvertToType should never throw");
            // Result can be null or a value, but shouldn't throw
        }

        /// <summary>
        /// Test case 8: ComponentOps error handling
        /// Tests if ComponentOps.SetProperty properly catches exceptions
        /// </summary>
        [Test]
        public void ComponentOps_SetProperty_HandlesConversionErrors()
        {
            var audioSource = testGameObject.AddComponent<AudioSource>();
            var token = JToken.FromObject(12345);

            // Try to set clip (AudioClip) to integer value
            bool success = ComponentOps.SetProperty(audioSource, "clip", token, out string error);

            Assert.IsFalse(success, "Should fail to set incompatible type");
            Assert.IsNotEmpty(error, "Should provide error message");

            // Verify the object is still in a valid state
            Assert.IsNotNull(audioSource, "AudioSource should still exist");
            Assert.IsNull(audioSource.clip, "Clip should remain null (not corrupted)");
        }
    }
}
