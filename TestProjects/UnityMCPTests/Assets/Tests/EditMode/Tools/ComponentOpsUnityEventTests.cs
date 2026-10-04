using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.Events;
using UnityEditor;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using TestNamespace;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentOpsUnityEventTests
    {
        private GameObject testGo;

        [SetUp]
        public void SetUp()
        {
            testGo = new GameObject("UnityEventTestGO");
        }

        [TearDown]
        public void TearDown()
        {
            if (testGo != null)
                Object.DestroyImmediate(testGo);
        }

        [Test]
        public void SetProperty_UnityEvent_SinglePersistentCall_PersistsViaSerialization()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            int targetId = testGo.GetInstanceIDCompat();

            var value = JObject.Parse(@"{
                ""m_PersistentCalls"": {
                    ""m_Calls"": [
                        {
                            ""m_Target"": { ""instanceID"": " + targetId + @" },
                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                            ""m_MethodName"": ""SetActive"",
                            ""m_Mode"": 6,
                            ""m_Arguments"": {
                                ""m_BoolArgument"": true
                            },
                            ""m_CallState"": 2
                        }
                    ]
                }
            }");

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", value, out string error);

            Assert.IsTrue(ok, $"SetProperty should succeed, got error: {error}");

            // Verify via SerializedObject readback
            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.IsNotNull(callsProp, "m_Calls property should exist");
            Assert.AreEqual(1, callsProp.arraySize, "Should have 1 persistent call");

            var call0 = callsProp.GetArrayElementAtIndex(0);
            Assert.AreEqual("SetActive", call0.FindPropertyRelative("m_MethodName").stringValue);
            Assert.AreEqual(testGo, call0.FindPropertyRelative("m_Target").objectReferenceValue);
            Assert.AreEqual(6, call0.FindPropertyRelative("m_Mode").enumValueIndex);
            Assert.AreEqual(2, call0.FindPropertyRelative("m_CallState").enumValueIndex);
        }

        [Test]
        public void SetProperty_UnityEvent_MultiplePersistentCalls_AllPersist()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            int targetId = testGo.GetInstanceIDCompat();

            var value = JObject.Parse(@"{
                ""m_PersistentCalls"": {
                    ""m_Calls"": [
                        {
                            ""m_Target"": { ""instanceID"": " + targetId + @" },
                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                            ""m_MethodName"": ""SetActive"",
                            ""m_Mode"": 6,
                            ""m_Arguments"": { ""m_BoolArgument"": true },
                            ""m_CallState"": 2
                        },
                        {
                            ""m_Target"": { ""instanceID"": " + targetId + @" },
                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                            ""m_MethodName"": ""SetActive"",
                            ""m_Mode"": 6,
                            ""m_Arguments"": { ""m_BoolArgument"": false },
                            ""m_CallState"": 2
                        }
                    ]
                }
            }");

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", value, out string error);

            Assert.IsTrue(ok, $"SetProperty should succeed, got error: {error}");

            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.AreEqual(2, callsProp.arraySize, "Should have 2 persistent calls");

            Assert.AreEqual("SetActive", callsProp.GetArrayElementAtIndex(0).FindPropertyRelative("m_MethodName").stringValue);
            Assert.AreEqual("SetActive", callsProp.GetArrayElementAtIndex(1).FindPropertyRelative("m_MethodName").stringValue);
        }

        [Test]
        public void SetProperty_UnityEvent_EmptyCalls_ClearsEvent()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();

            // First set a call
            int targetId = testGo.GetInstanceIDCompat();
            var withCall = JObject.Parse(@"{
                ""m_PersistentCalls"": {
                    ""m_Calls"": [
                        {
                            ""m_Target"": { ""instanceID"": " + targetId + @" },
                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                            ""m_MethodName"": ""SetActive"",
                            ""m_Mode"": 6,
                            ""m_Arguments"": { ""m_BoolArgument"": true },
                            ""m_CallState"": 2
                        }
                    ]
                }
            }");
            ComponentOps.SetProperty(comp, "onSimpleEvent", withCall, out _);

            // Now clear it
            var empty = JObject.Parse(@"{
                ""m_PersistentCalls"": {
                    ""m_Calls"": []
                }
            }");

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", empty, out string error);

            Assert.IsTrue(ok, $"SetProperty should succeed, got error: {error}");

            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.AreEqual(0, callsProp.arraySize, "Should have 0 persistent calls after clearing");
        }

        [Test]
        public void SetProperty_PrivateSerializedUnityEvent_RoutesViaSerialization()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            int targetId = testGo.GetInstanceIDCompat();

            var value = JObject.Parse(@"{
                ""m_PersistentCalls"": {
                    ""m_Calls"": [
                        {
                            ""m_Target"": { ""instanceID"": " + targetId + @" },
                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                            ""m_MethodName"": ""SetActive"",
                            ""m_Mode"": 6,
                            ""m_Arguments"": { ""m_BoolArgument"": true },
                            ""m_CallState"": 2
                        }
                    ]
                }
            }");

            bool ok = ComponentOps.SetProperty(comp, "_onPrivateEvent", value, out string error);

            Assert.IsTrue(ok, $"SetProperty on private [SerializeField] UnityEvent should succeed, got error: {error}");

            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("_onPrivateEvent.m_PersistentCalls.m_Calls");
            Assert.IsNotNull(callsProp, "Private event m_Calls should exist");
            Assert.AreEqual(1, callsProp.arraySize, "Should have 1 persistent call");
            Assert.IsNotNull(comp.PrivateEvent, "The serialized event should update the component instance");
            Assert.AreEqual(1, comp.PrivateEvent.GetPersistentEventCount());
            Assert.AreEqual(testGo, comp.PrivateEvent.GetPersistentTarget(0));
        }

        [Test]
        public void SetProperty_SimpleFloat_StillWorksViaReflection()
        {
            var audioSource = testGo.AddComponent<AudioSource>();

            bool ok = ComponentOps.SetProperty(audioSource, "volume", new JValue(0.5f), out string error);

            Assert.IsTrue(ok, $"SetProperty for float should succeed, got error: {error}");
            Assert.AreEqual(0.5f, audioSource.volume, 0.001f);
        }

        [Test]
        public void SetProperty_UnityEvent_InvalidCall_DoesNotResizeExistingCalls()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            var original = CreateEventValue(true);
            Assert.IsTrue(ComponentOps.SetProperty(comp, "onSimpleEvent", original, out string setupError), setupError);

            var invalid = CreateEventValue(false);
            var calls = (JArray)invalid["m_PersistentCalls"]["m_Calls"];
            calls.Add(new JObject { ["m_UnknownProperty"] = 1 });

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", invalid, out string error);

            Assert.IsFalse(ok);
            StringAssert.Contains("m_UnknownProperty", error);
            using var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.AreEqual(1, callsProp.arraySize, "A rejected event update must preserve its existing calls.");
            Assert.IsTrue(callsProp.GetArrayElementAtIndex(0).FindPropertyRelative("m_Arguments.m_BoolArgument").boolValue);
        }

        [Test]
        public void SetProperty_UnityEvent_InvalidSibling_DoesNotClearExistingCalls()
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            Assert.IsTrue(ComponentOps.SetProperty(comp, "onSimpleEvent", CreateEventValue(true), out string setupError), setupError);
            var invalid = new JObject
            {
                ["m_PersistentCalls"] = new JObject { ["m_Calls"] = new JArray() },
                ["m_UnknownProperty"] = 1
            };

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", invalid, out string error);

            Assert.IsFalse(ok);
            StringAssert.Contains("m_UnknownProperty", error);
            Assert.AreEqual(1, comp.onSimpleEvent.GetPersistentEventCount(), "A later validation error must not clear the event.");
            Assert.AreEqual(testGo, comp.onSimpleEvent.GetPersistentTarget(0));
        }

        [TestCase("nonsense")]
        [TestCase("")]
        public void SetProperty_UnityEvent_InvalidBoolean_PreservesExistingArgument(string value)
        {
            var comp = testGo.AddComponent<UnityEventTestComponent>();
            Assert.IsTrue(ComponentOps.SetProperty(comp, "onSimpleEvent", CreateEventValue(true), out string setupError), setupError);
            var invalid = CreateEventValue(true);
            invalid["m_PersistentCalls"]["m_Calls"][0]["m_Arguments"]["m_BoolArgument"] = value;

            bool ok = ComponentOps.SetProperty(comp, "onSimpleEvent", invalid, out string error);

            Assert.IsFalse(ok);
            StringAssert.Contains("Expected boolean value", error);
            using var so = new SerializedObject(comp);
            Assert.IsTrue(so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls")
                .GetArrayElementAtIndex(0).FindPropertyRelative("m_Arguments.m_BoolArgument").boolValue);
        }

        private JObject CreateEventValue(bool argument)
        {
            return new JObject
            {
                ["m_PersistentCalls"] = new JObject
                {
                    ["m_Calls"] = new JArray(new JObject
                    {
                        ["m_Target"] = new JObject { ["instanceID"] = testGo.GetInstanceIDCompat() },
                        ["m_TargetAssemblyTypeName"] = "UnityEngine.GameObject, UnityEngine",
                        ["m_MethodName"] = "SetActive",
                        ["m_Mode"] = 6,
                        ["m_Arguments"] = new JObject { ["m_BoolArgument"] = argument },
                        ["m_CallState"] = 2
                    })
                }
            };
        }

        [Test]
        public void HandleCommand_EndToEnd_UnityEventWiring()
        {
            testGo.AddComponent<UnityEventTestComponent>();
            int targetId = testGo.GetInstanceIDCompat();

            var p = new JObject
            {
                ["action"] = "set_property",
                ["target"] = testGo.name,
                ["search_method"] = "by_name",
                ["component_type"] = "UnityEventTestComponent",
                ["property"] = "onSimpleEvent",
                ["value"] = JObject.Parse(@"{
                    ""m_PersistentCalls"": {
                        ""m_Calls"": [
                            {
                                ""m_Target"": { ""instanceID"": " + targetId + @" },
                                ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                                ""m_MethodName"": ""SetActive"",
                                ""m_Mode"": 6,
                                ""m_Arguments"": { ""m_BoolArgument"": true },
                                ""m_CallState"": 2
                            }
                        ]
                    }
                }")
            };

            var result = ManageComponents.HandleCommand(p);
            var resultObj = result as JObject ?? JObject.FromObject(result);

            Assert.IsTrue(resultObj.Value<bool>("success"), $"HandleCommand should succeed: {resultObj}");

            // Verify via SerializedObject
            var comp = testGo.GetComponent<UnityEventTestComponent>();
            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.AreEqual(1, callsProp.arraySize, "Should have 1 persistent call after end-to-end");
            Assert.AreEqual("SetActive", callsProp.GetArrayElementAtIndex(0).FindPropertyRelative("m_MethodName").stringValue);
        }
    }
}
