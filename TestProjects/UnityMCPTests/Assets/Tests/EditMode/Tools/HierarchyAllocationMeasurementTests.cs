using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public sealed class HierarchyAllocationMeasurementTests
    {
        [Test]
        public void AllocationRecorderUnitIsReportedWithoutAssumingBytes()
        {
            using var recorder = ProfilerRecorder.StartNew(
                ProfilerCategory.Memory,
                "GC.Alloc",
                1024,
                ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            GC.KeepAlive(new byte[65536]);
            recorder.Stop();
            long value = 0;
            for (int i = 0; i < recorder.Count; i++)
                value += recorder.GetSample(i).Value;
            TestContext.Out.WriteLine(
                "ALLOCATION_RECORDER_UNIT "
                    + new JObject
                    {
                        ["valid"] = recorder.Valid,
                        ["unit"] = recorder.Valid ? recorder.UnitType.ToString() : "invalid",
                        ["sampleValueSum"] = value,
                        ["knownAllocationPayloadBytes"] = 65536,
                        ["samples"] = recorder.Count,
                    }.ToString(Formatting.None)
            );
        }

        [Test, Explicit("Controller-owned equal complete hierarchy measurements."), Timeout(300000)]
        public void CompleteRootDeepAndComponentPagesPreserveEveryRow()
        {
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE");
            Assert.IsTrue(Application.isBatchMode && Guid.TryParseExact(nonce, "N", out _), "An owned native controller is required.");
            Scene prior = SceneManager.GetActiveScene();
            string ownedBaseline = null;
            bool restoreStartup = false;
            NewSceneSetup startupSetup = NewSceneSetup.EmptyScene;
            try
            {
                if (string.IsNullOrEmpty(prior.path))
                {
                    // UTF may reopen the pristine batch startup after controller preparation.
                    // Only its empty/default setup may be saved and restored by this owned probe.
                    Assert.IsFalse(prior.isDirty);
                    Assert.That(prior.rootCount, Is.EqualTo(0).Or.EqualTo(2));
                    foreach (var root in prior.GetRootGameObjects())
                        Assert.That(root.name, Is.EqualTo("Main Camera").Or.EqualTo("Directional Light"));
                    startupSetup = prior.rootCount == 0 ? NewSceneSetup.EmptyScene : NewSceneSetup.DefaultGameObjects;
                    Assert.AreEqual(1, SceneManager.sceneCount);
                    ownedBaseline = "Assets/__HierarchyBaseline_" + Guid.NewGuid().ToString("N") + ".unity";
                    Assert.IsTrue(EditorSceneManager.SaveScene(prior, ownedBaseline));
                    restoreStartup = true;
                }
                foreach (string shape in new[] { "10000-roots", "1000-deep-1000-children", "1000-objects-16-components" })
                {
                    Scene owned = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    SceneManager.SetActiveScene(owned);
                    try
                    {
                        var expected = new List<GameObject>();
                        Transform parent = null;
                        int count = shape == "10000-roots" ? 10000 : 1000;
                        if (shape.Contains("deep"))
                            for (int depth = 0; depth < 1000; depth++)
                            {
                                var node = new GameObject("Depth" + depth);
                                node.transform.SetParent(parent, false);
                                parent = node.transform;
                            }
                        for (int i = 0; i < count; i++)
                        {
                            var go = new GameObject("Item" + i.ToString("D5"));
                            if (parent != null)
                                go.transform.SetParent(parent, false);
                            if (shape.Contains("components"))
                                for (int c = 0; c < 16; c++)
                                    go.AddComponent<BoxCollider>();
                            expected.Add(go);
                        }
                        string expectedHash = null;
                        for (int round = 0; round < 6; round++)
                        {
                            var rows = new JArray();
                            var nativeTimes = new JArray();
                            var serializationTimes = new JArray();
                            for (int cursor = 0; cursor < count; cursor += 100)
                            {
                                var request = Request(cursor, parent);
                                var timer = Stopwatch.StartNew();
                                object response = ManageScene.HandleCommand(request);
                                timer.Stop();
                                nativeTimes.Add(timer.Elapsed.TotalMilliseconds);
                                timer.Restart();
                                var token = JObject.FromObject(response);
                                string encoded = token.ToString(Formatting.None);
                                timer.Stop();
                                serializationTimes.Add(timer.Elapsed.TotalMilliseconds);
                                Assert.IsNotEmpty(encoded);
                                VerifyPage(token, expected, cursor);
                                foreach (JObject row in token["data"]["items"])
                                {
                                    row["instanceID"] = 0; // IDs vary across owned Editor launches.
                                    rows.Add(row);
                                }
                            }
                            using var sha = SHA256.Create();
                            string digest = BitConverter
                                .ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(rows.ToString(Formatting.None))))
                                .Replace("-", "")
                                .ToLowerInvariant();
                            expectedHash ??= digest;
                            Assert.AreEqual(expectedHash, digest);
                            TestContext.Out.WriteLine(
                                "HIERARCHY_ALLOCATION "
                                    + new JObject
                                    {
                                        ["shape"] = shape,
                                        ["round"] = round,
                                        ["units"] = count,
                                        ["pages"] = nativeTimes.Count,
                                        ["outputSha256"] = digest,
                                        ["nativePageMs"] = nativeTimes,
                                        ["serializationPageMs"] = serializationTimes,
                                    }.ToString(Formatting.None)
                            );
                        }
                        // GC.Alloc recorder values can be durations, not allocation sizes.
                        // Preserve invalid/full/unit coverage instead of interpreting it as zero.
                        var allocations = new JArray();
                        for (int cursor = 0; cursor < count; cursor += 100)
                        {
                            var request = Request(cursor, parent);
                            object response;
                            using (
                                var recorder = ProfilerRecorder.StartNew(
                                    ProfilerCategory.Memory,
                                    "GC.Alloc",
                                    65536,
                                    ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                                )
                            )
                            {
                                response = ManageScene.HandleCommand(request);
                                recorder.Stop();
                                int samples = recorder.Valid ? recorder.Count : 0;
                                string unit = recorder.Valid ? recorder.UnitType.ToString() : "invalid";
                                string status =
                                    !recorder.Valid ? "invalid"
                                    : samples >= recorder.Capacity ? "full"
                                    : unit != "Bytes" ? "unit_mismatch"
                                    : samples == 0 ? "empty"
                                    : "unvalidated_byte_counter";
                                allocations.Add(
                                    new JObject
                                    {
                                        ["cursor"] = cursor,
                                        ["status"] = status,
                                        ["recorded_unit"] = unit,
                                        ["samples"] = samples,
                                        ["capacity"] = recorder.Valid ? recorder.Capacity : 65536,
                                        ["bytes"] = JValue.CreateNull(),
                                    }
                                );
                            }
                            VerifyPage(JObject.FromObject(response), expected, cursor);
                        }
                        TestContext.Out.WriteLine(
                            "HIERARCHY_ALLOCATION_TRAFFIC "
                                + new JObject
                                {
                                    ["shape"] = shape,
                                    ["units"] = count,
                                    ["passes"] = allocations,
                                }.ToString(Formatting.None)
                        );
                        // Live changes must be visible; no frozen root inventory survives a call.
                        expected[0].name = "Changed";
                        var changed = JObject.FromObject(ManageScene.HandleCommand(Request(0, parent)));
                        Assert.AreEqual("Changed", changed["data"]["items"][0].Value<string>("name"));
                        var rawChanged = (SuccessResponse)ManageScene.HandleCommand(Request(0, parent));
                        var actualItems = (List<object>)rawChanged.Data.GetType().GetProperty("items").GetValue(rawChanged.Data);
                        var actualFirst = (Dictionary<string, object>)actualItems[0];
                        ((List<string>)actualFirst["componentTypes"]).Clear();
                        Assert.IsNotEmpty((List<string>)((Dictionary<string, object>)actualItems[1])["componentTypes"]);
                        Assert.IsNotEmpty((JArray)JObject.FromObject(ManageScene.HandleCommand(Request(0, parent)))["data"]["items"][0]["componentTypes"]);
                        expected[count - 1].transform.SetSiblingIndex(0);
                        var reordered = JObject.FromObject(ManageScene.HandleCommand(Request(0, parent)));
                        Assert.AreEqual(expected[count - 1].GetInstanceIDCompat(), reordered["data"]["items"][0].Value<int>("instanceID"));
                        Object.DestroyImmediate(expected[count - 1]);
                        Assert.AreEqual(count - 1, JObject.FromObject(ManageScene.HandleCommand(Request(0, parent)))["data"].Value<int>("total"));
                        expected.Clear();
                    }
                    finally
                    {
                        SceneManager.SetActiveScene(prior);
                        Assert.IsTrue(EditorSceneManager.CloseScene(owned, true));
                    }
                }
            }
            finally
            {
                if (prior.IsValid() && prior.isLoaded)
                    SceneManager.SetActiveScene(prior);
                if (restoreStartup)
                {
                    Assert.AreEqual(startupSetup == NewSceneSetup.EmptyScene ? 0 : 2, prior.rootCount, "The owned startup baseline gained unexpected content.");
                    EditorSceneManager.NewScene(startupSetup, NewSceneMode.Single);
                }
                if (ownedBaseline != null)
                    Assert.IsTrue(AssetDatabase.DeleteAsset(ownedBaseline), "The owned baseline asset must be removed.");
            }
        }

        private static JObject Request(int cursor, Transform parent)
        {
            var request = new JObject
            {
                ["action"] = "get_hierarchy",
                ["cursor"] = cursor,
                ["pageSize"] = 100,
                ["includeTransform"] = true,
            };
            if (parent != null)
                request["parent"] = parent.gameObject.GetInstanceIDCompat();
            return request;
        }

        private static void VerifyPage(JObject response, List<GameObject> expected, int cursor)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = (JObject)response["data"];
            Assert.AreEqual(expected.Count, data.Value<int>("total"));
            var items = (JArray)data["items"];
            Assert.AreEqual(Math.Min(100, expected.Count - cursor), items.Count);
            for (int i = 0; i < items.Count; i++)
            {
                Assert.AreEqual(expected[cursor + i].GetInstanceIDCompat(), items[i].Value<int>("instanceID"));
                CollectionAssert.AreEqual(
                    expected[cursor + i].GetComponents<Component>().Where(c => c != null).Select(c => c.GetType().Name),
                    items[i]["componentTypes"].Values<string>()
                );
            }
        }
    }
}
