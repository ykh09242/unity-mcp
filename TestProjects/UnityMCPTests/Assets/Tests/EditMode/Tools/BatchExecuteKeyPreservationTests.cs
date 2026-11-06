using System.IO;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
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
        private MCPForUnityTests.Editor.Services.ScopedConsentTestState scopedConsent;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            CommandRegistry.Initialize();
        }

        [SetUp]
        public void SetUp()
        {
            scopedConsent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState(
                "batch_execute",
                "manage_gameobject",
                "manage_scene_edits",
                "manage_script"
            );
            testGo = new GameObject("BatchKeyTestGO");
        }

        [TearDown]
        public void TearDown()
        {
            scopedConsent?.Dispose();
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

        private static JObject SendGameObject(JObject parameters, bool batch)
        {
            if (!batch)
                return JObject.FromObject(ManageGameObject.HandleCommand(parameters));
            var response = JObject.FromObject(
                BatchExecute
                    .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_gameobject", ["params"] = parameters }) })
                    .GetAwaiter()
                    .GetResult()
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response.SelectToken("data.results[0].result");
        }

        private static JObject SendScript(JObject parameters, bool batch)
        {
            if (!batch)
                return JObject.FromObject(ManageScript.HandleCommand(parameters));
            var response = JObject.FromObject(
                BatchExecute
                    .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_script", ["params"] = parameters }) })
                    .GetAwaiter()
                    .GetResult()
            );
            return (JObject)response.SelectToken("data.results[0].result");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ScriptReadWindow_HasDirectBatchParity(bool batch)
        {
            string folder = "BatchScript_" + System.Guid.NewGuid().ToString("N");
            string directory = Path.Combine(Application.dataPath, folder);
            Directory.CreateDirectory(directory);
            try
            {
                File.WriteAllText(Path.Combine(directory, "Probe.cs"), "// first\n// second\n// third\n");
                var request = new JObject
                {
                    ["action"] = "read",
                    ["name"] = "Probe",
                    ["path"] = "Assets/" + folder,
                    ["start_line"] = 2,
                    ["line_count"] = 1,
                };
                var result = SendScript(request, batch);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsTrue(result["data"].Value<bool>("lineWindowApplied"));
                Assert.AreEqual("// second\n", result["data"].Value<string>("contents"));
                Assert.AreEqual(2, request.Value<int>("start_line"), "Caller parameters must stay unchanged.");
            }
            finally
            {
                File.Delete(Path.Combine(directory, "Probe.cs"));
                Directory.Delete(directory);
            }
        }

        [TestCase("get_change_set")]
        [TestCase("apply_change_set")]
        [TestCase("recover_change_set")]
        public void ScriptChangeSetId_ReachesTheSameLookupThroughBatch(string action)
        {
            var request = new JObject { ["action"] = action, ["change_set_id"] = System.Guid.NewGuid().ToString("N") };
            var direct = SendScript((JObject)request.DeepClone(), false);
            var batched = SendScript(request, true);
            Assert.AreEqual("change_set_not_found", direct.Value<string>("code"), direct.ToString());
            Assert.AreEqual(direct.Value<string>("code"), batched.Value<string>("code"), batched.ToString());
        }

        [TestCase("custom_contract_probe")]
        [TestCase("gameobject_custom_contract_probe")]
        public void UndeclaredToolPayload_PreservesExactKeysAndExplicitNull(string tool)
        {
            var source = JObject.Parse("{\"user_key\":null,\"userKey\":false,\"nested_value\":{\"m_Field\":1}}");
            var method = typeof(BatchExecute).GetMethod(
                "NormalizeParameterKeys",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic
            );
            var normalized = (JObject)method.Invoke(null, new object[] { source, tool });
            Assert.IsTrue(JToken.DeepEquals(source, normalized), normalized.ToString());
            Assert.AreNotSame(source, normalized);
        }

        [TestCase("false")]
        [TestCase("null")]
        public void EqualAliases_AreAcceptedAndPreserveExplicitValues(string value)
        {
            var request = new JObject
            {
                ["action"] = "modify",
                ["target"] = testGo.GetInstanceIDCompat(),
                ["world_position_stays"] = JToken.Parse(value),
                ["worldPositionStays"] = JToken.Parse(value),
            };
            Assert.IsTrue(SendGameObject((JObject)request.DeepClone(), false).Value<bool>("success"));
            Assert.IsTrue(SendGameObject((JObject)request.DeepClone(), true).Value<bool>("success"));
            Assert.AreEqual(JToken.Parse(value).Type, request["world_position_stays"].Type, "Caller payloads must remain unchanged.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ScenePreviewId_PreservesCanonicalTokenThroughBatch(bool batch)
        {
            var preview = JObject.FromObject(
                ManageSceneEdits.HandleCommand(
                    new JObject
                    {
                        ["action"] = "preview",
                        ["edits"] = new JArray(
                            new JObject
                            {
                                ["target"] = testGo.GetInstanceIDCompat(),
                                ["component_type"] = "Transform",
                                ["property"] = "localPosition",
                                ["value"] = new JArray(3, 2, 1),
                            }
                        ),
                    }
                )
            );
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            var request = new JObject { ["action"] = "apply", ["preview_id"] = preview.SelectToken("data.preview_id").DeepClone() };
            var response = batch
                ? JObject.FromObject(
                    BatchExecute
                        .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_scene_edits", ["params"] = request }) })
                        .GetAwaiter()
                        .GetResult()
                )
                : JObject.FromObject(ManageSceneEdits.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(new Vector3(3, 2, 1), testGo.transform.localPosition);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ScenePreviewId_ContradictoryNullAliasDoesNotConsumePreview(bool batch)
        {
            var preview = JObject.FromObject(
                ManageSceneEdits.HandleCommand(
                    new JObject
                    {
                        ["action"] = "preview",
                        ["edits"] = new JArray(
                            new JObject
                            {
                                ["target"] = testGo.GetInstanceIDCompat(),
                                ["component_type"] = "Transform",
                                ["property"] = "localPosition",
                                ["value"] = new JArray(6, 5, 4),
                            }
                        ),
                    }
                )
            );
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            var token = preview.SelectToken("data.preview_id");
            var request = new JObject
            {
                ["action"] = "apply",
                ["preview_id"] = token.DeepClone(),
                ["previewId"] = JValue.CreateNull(),
            };
            var response = batch
                ? JObject.FromObject(
                    BatchExecute
                        .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_scene_edits", ["params"] = request }) })
                        .GetAwaiter()
                        .GetResult()
                )
                : JObject.FromObject(ManageSceneEdits.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(Vector3.zero, testGo.transform.localPosition);
            var valid = JObject.FromObject(ManageSceneEdits.HandleCommand(new JObject { ["action"] = "apply", ["previewId"] = token.DeepClone() }));
            Assert.IsTrue(valid.Value<bool>("success"), valid.ToString());
            Assert.AreEqual(new Vector3(6, 5, 4), testGo.transform.localPosition);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CanonicalTransformOptions_PreserveFalseAndSiblingPlacement(bool batch)
        {
            var parent = new GameObject("ParityParent");
            var sibling = new GameObject("ParitySibling");
            try
            {
                parent.transform.position = new Vector3(10, 0, 0);
                sibling.transform.SetParent(parent.transform, false);
                testGo.transform.localPosition = new Vector3(2, 3, 4);
                var response = SendGameObject(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = testGo.GetInstanceIDCompat(),
                        ["parent"] = parent.GetInstanceIDCompat(),
                        ["world_position_stays"] = false,
                        ["sibling_index"] = 0,
                    },
                    batch
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(new Vector3(2, 3, 4), testGo.transform.localPosition);
                Assert.AreEqual(0, testGo.transform.GetSiblingIndex());
                Assert.IsTrue(
                    SendGameObject(
                            new JObject
                            {
                                ["action"] = "modify",
                                ["target"] = testGo.GetInstanceIDCompat(),
                                ["parent"] = JValue.CreateNull(),
                            },
                            batch
                        )
                        .Value<bool>("success")
                );
                Assert.IsNull(testGo.transform.parent);
            }
            finally
            {
                testGo.transform.SetParent(null, false);
                Object.DestroyImmediate(parent);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CanonicalDuplicateAndRelativeMove_HaveDirectBatchParity(bool batch)
        {
            GameObject duplicate = null;
            string name = "ParityDuplicate_" + System.Guid.NewGuid().ToString("N");
            try
            {
                var response = SendGameObject(
                    new JObject
                    {
                        ["action"] = "duplicate",
                        ["target"] = testGo.GetInstanceIDCompat(),
                        ["new_name"] = name,
                    },
                    batch
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                duplicate = GameObject.Find(name);
                Assert.IsNotNull(duplicate, response.ToString());
                testGo.transform.position = new Vector3(4, 5, 6);
                testGo.transform.rotation = Quaternion.Euler(0, 90, 0);
                testGo.transform.localScale = new Vector3(2, 2, 2);
                response = SendGameObject(
                    new JObject
                    {
                        ["action"] = "move_relative",
                        ["target"] = duplicate.GetInstanceIDCompat(),
                        ["reference_object"] = testGo.GetInstanceIDCompat(),
                        ["world_space"] = false,
                        ["offset"] = new JArray(1, 0, 0),
                    },
                    batch
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.That(Vector3.Distance(testGo.transform.TransformPoint(Vector3.right), duplicate.transform.position), Is.LessThan(0.001f));
            }
            finally
            {
                if (duplicate != null)
                    Object.DestroyImmediate(duplicate);
                var leaked = GameObject.Find(testGo.name + "_Copy");
                if (leaked != null)
                    Object.DestroyImmediate(leaked);
            }
        }

        [TestCase("world_position_stays", "worldPositionStays", "false", "true")]
        [TestCase("new_name", "newName", "null", "\"conflict\"")]
        [TestCase("sibling_index", "siblingIndex", "0", "1")]
        [TestCase("reference_object", "referenceObject", "null", "42")]
        [TestCase("world_space", "worldSpace", "false", "true")]
        public void ContradictoryAliases_AreRejectedBeforeDirectOrBatchMutation(string canonical, string alias, string first, string second)
        {
            foreach (bool batch in new[] { false, true })
            {
                var request = new JObject
                {
                    ["action"] = "modify",
                    ["target"] = testGo.GetInstanceIDCompat(),
                    ["name"] = "must-not-rename",
                    [canonical] = JToken.Parse(first),
                    [alias] = JToken.Parse(second),
                };
                JObject response = batch
                    ? JObject.FromObject(
                        BatchExecute
                            .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_gameobject", ["params"] = request }) })
                            .GetAwaiter()
                            .GetResult()
                    )
                    : JObject.FromObject(ManageGameObject.HandleCommand(request));
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("BatchKeyTestGO", testGo.name);
            }
        }
    }
}
