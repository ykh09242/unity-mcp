using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using TestNamespace;
using UnityEditor;
using UnityEngine;
using UnityEngine.Events;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// Verifies that batch_execute only normalizes top-level parameter keys (snake_case → camelCase)
    /// and preserves nested value keys (e.g. Unity serialized property paths like m_PersistentCalls).
    /// </summary>
    public class BatchExecuteKeyPreservationTests
    {
        private GameObject testGo;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            CommandRegistry.Initialize();
        }

        [SetUp]
        public void SetUp()
        {
            testGo = new GameObject("BatchKeyTestGO");
        }

        [TearDown]
        public void TearDown()
        {
            if (testGo != null)
                Object.DestroyImmediate(testGo);
        }

        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("42")]
        [TestCase("{}")]
        [TestCase("{\"tool\":\" \"}")]
        [TestCase("{\"tool\":42}")]
        [TestCase("{\"tool\":{}}")]
        [TestCase("{\"tool\":\"manage_components\",\"params\":[]}")]
        [TestCase("{\"tool\":\"manage_components\",\"params\":true}")]
        [TestCase("{\"tool\":\"manage_components\",\"params\":\"invalid\"}")]
        public void MalformedLaterCommand_PreservesEarlierMutationTarget(string invalidCommandJson)
        {
            var audio = testGo.AddComponent<AudioSource>();
            audio.volume = 0.8f;
            int dirtyCount = EditorUtility.GetDirtyCount(audio);
            var discovery = MCPServiceLocator.ToolDiscovery;
            bool wasEnabled = discovery.IsToolEnabled("manage_components");
            discovery.SetToolEnabled("manage_components", true);
            try
            {
                var response = JObject.FromObject(
                    BatchExecute
                        .HandleCommand(
                            new JObject
                            {
                                ["commands"] = new JArray(
                                    new JObject
                                    {
                                        ["tool"] = "manage_components",
                                        ["params"] = new JObject
                                        {
                                            ["action"] = "set_property",
                                            ["target"] = testGo.GetInstanceIDCompat(),
                                            ["search_method"] = "by_id",
                                            ["component_type"] = "AudioSource",
                                            ["property"] = "volume",
                                            ["value"] = 0.2f,
                                        },
                                    },
                                    JToken.Parse(invalidCommandJson)
                                ),
                            }
                        )
                        .GetAwaiter()
                        .GetResult()
                );

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(0, response.SelectToken("data.callSuccessCount").Value<int>());
                Assert.AreEqual(1, response.SelectToken("data.callFailureCount").Value<int>());
                Assert.AreEqual(1, ((JArray)response.SelectToken("data.results")).Count, "Only the malformed command should be reported; none are dispatched.");
                Assert.AreEqual(0.8f, audio.volume, 0.001f, "Every command shape must be validated before the first dispatch.");
                Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(audio));
            }
            finally
            {
                discovery.SetToolEnabled("manage_components", wasEnabled);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingOrNullCommandParameters_RemainSupported(bool explicitNull)
        {
            var discovery = MCPServiceLocator.ToolDiscovery;
            bool wasEnabled = discovery.IsToolEnabled("manage_vfx");
            discovery.SetToolEnabled("manage_vfx", true);
            try
            {
                var command = new JObject { ["tool"] = "manage_vfx" };
                if (explicitNull)
                    command["params"] = JValue.CreateNull();
                var response = JObject.FromObject(BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray(command) }).GetAwaiter().GetResult());
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(
                    "Action is required",
                    response.SelectToken("data.results[0].result.message").Value<string>(),
                    "Valid empty envelopes must reach the handler."
                );
            }
            finally
            {
                discovery.SetToolEnabled("manage_vfx", wasEnabled);
            }
        }

        [Test]
        public void NestedValueKeys_WithUnderscores_ArePreservedThroughBatch()
        {
            testGo.AddComponent<UnityEventTestComponent>();
            int targetId = testGo.GetInstanceIDCompat();

            var batchParams = new JObject
            {
                ["commands"] = new JArray
                {
                    new JObject
                    {
                        ["tool"] = "manage_components",
                        ["params"] = new JObject
                        {
                            ["action"] = "set_property",
                            ["target"] = testGo.name,
                            ["search_method"] = "by_name",
                            ["component_type"] = "UnityEventTestComponent",
                            ["property"] = "onSimpleEvent",
                            ["value"] = JObject.Parse(
                                @"{
                                ""m_PersistentCalls"": {
                                    ""m_Calls"": [
                                        {
                                            ""m_Target"": { ""instanceID"": "
                                    + targetId
                                    + @" },
                                            ""m_TargetAssemblyTypeName"": ""UnityEngine.GameObject, UnityEngine"",
                                            ""m_MethodName"": ""SetActive"",
                                            ""m_Mode"": 6,
                                            ""m_Arguments"": { ""m_BoolArgument"": true },
                                            ""m_CallState"": 2
                                        }
                                    ]
                                }
                            }"
                            ),
                        },
                    },
                },
            };

            var result = BatchExecute.HandleCommand(batchParams).GetAwaiter().GetResult();
            var resultObj = JObject.FromObject(result);

            Assert.IsTrue(resultObj.Value<bool>("success"), $"Batch should succeed: {resultObj}");

            // Verify the nested m_PersistentCalls keys were preserved (not mangled to mPersistentCalls)
            var comp = testGo.GetComponent<UnityEventTestComponent>();
            var so = new SerializedObject(comp);
            var callsProp = so.FindProperty("onSimpleEvent.m_PersistentCalls.m_Calls");
            Assert.IsNotNull(callsProp, "m_Calls property should exist");
            Assert.AreEqual(1, callsProp.arraySize, "Should have 1 persistent call");
            Assert.AreEqual("SetActive", callsProp.GetArrayElementAtIndex(0).FindPropertyRelative("m_MethodName").stringValue);
        }

        [Test]
        public void TopLevelParameterKeys_AreStillNormalized()
        {
            testGo.AddComponent<AudioSource>();

            // Use snake_case top-level keys: search_method, component_type
            var batchParams = new JObject
            {
                ["commands"] = new JArray
                {
                    new JObject
                    {
                        ["tool"] = "manage_components",
                        ["params"] = new JObject
                        {
                            ["action"] = "set_property",
                            ["target"] = testGo.name,
                            ["search_method"] = "by_name",
                            ["component_type"] = "AudioSource",
                            ["property"] = "volume",
                            ["value"] = 0.42f,
                        },
                    },
                },
            };

            var result = BatchExecute.HandleCommand(batchParams).GetAwaiter().GetResult();
            var resultObj = JObject.FromObject(result);

            Assert.IsTrue(resultObj.Value<bool>("success"), $"Batch with snake_case top-level keys should succeed: {resultObj}");
            Assert.AreEqual(0.42f, testGo.GetComponent<AudioSource>().volume, 0.001f);
        }

        [Test]
        public void Regression_CreateGameObject_StillWorksViaBatch()
        {
            string goName = "BatchCreatedGO_" + System.Guid.NewGuid().ToString("N").Substring(0, 8);
            GameObject created = null;

            try
            {
                var batchParams = new JObject
                {
                    ["commands"] = new JArray
                    {
                        new JObject
                        {
                            ["tool"] = "manage_gameobject",
                            ["params"] = new JObject
                            {
                                ["action"] = "create",
                                ["name"] = goName,
                                ["primitive_type"] = "Cube",
                            },
                        },
                    },
                };

                var result = BatchExecute.HandleCommand(batchParams).GetAwaiter().GetResult();
                var resultObj = JObject.FromObject(result);

                Assert.IsTrue(resultObj.Value<bool>("success"), $"Batch create GO should succeed: {resultObj}");

                created = GameObject.Find(goName);
                Assert.IsNotNull(created, $"GameObject '{goName}' should exist in scene");
            }
            finally
            {
                if (created != null)
                    Object.DestroyImmediate(created);
            }
        }
    }
}
