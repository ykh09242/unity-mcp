using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageGraphicsTests
    {
        private const string TempRoot = "Assets/Temp/ManageGraphicsTests";
        private bool _hasVolumeSystem;
        private bool _hasSceneView;

        [SetUp]
        public void SetUp()
        {
            EnsureFolder(TempRoot);

            var pingResult = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "ping" }));
            if (pingResult.Value<bool>("success"))
            {
                var data = pingResult["data"];
                _hasVolumeSystem = data?.Value<bool>("hasVolumeSystem") ?? false;
            }

            _hasSceneView = UnityEditor.SceneView.lastActiveSceneView != null;
        }

        [TearDown]
        public void TearDown()
        {
#if UNITY_2022_2_OR_NEWER
            foreach (var go in UnityFindObjectsCompat.FindAll<GameObject>())
#else
            foreach (var go in UnityEngine.Object.FindObjectsOfType<GameObject>())
#endif
            {
                if (go.name.StartsWith("GfxTest_"))
                    UnityEngine.Object.DestroyImmediate(go);
            }

            if (AssetDatabase.IsValidFolder(TempRoot))
                AssetDatabase.DeleteAsset(TempRoot);
            CleanupEmptyParentFolders(TempRoot);

            // Reset scene debug mode
            ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_set_scene_debug", ["mode"] = "Textured" });
        }

        // =====================================================================
        // Dispatch / Error Handling
        // =====================================================================

        [Test]
        public void HandleCommand_NullParams_ReturnsError()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(null));
            Assert.IsFalse(result.Value<bool>("success"));
        }

        [Test]
        public void HandleCommand_MissingAction_ReturnsError()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject()));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("action"));
        }

        [Test]
        public void HandleCommand_UnknownAction_ReturnsError()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bogus_action" }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("Unknown action"));
        }

        [Test]
        public void Ping_ReturnsPipelineInfo()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "ping" }));
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.That(result["message"].ToString(), Does.Contain("Pipeline"));
            var data = result["data"];
            Assert.IsNotNull(data);
            Assert.IsNotNull(data["pipeline"]);
            Assert.IsNotNull(data["pipelineName"]);
        }

        // =====================================================================
        // Volume Actions
        // =====================================================================

        // Assert.Ignore, not Assume.That: Assume yields Inconclusive, which the Test Runner
        // window renders as a failure and which leaves the run's resultState non-Passed.
        private void RequireVolumeSystem()
        {
            if (!_hasVolumeSystem)
                Assert.Ignore("Volume system not available — skipping.");
        }

        [Test]
        public void VolumeCreate_ProfileInNewNestedFolders_PersistsAndReusesProfile()
        {
            RequireVolumeSystem();
            string folder = $"{TempRoot}/VolumeProfile/Nested";
            string path = $"{folder}/Profile.asset";
            var parameters = new JObject
            {
                ["action"] = "volume_create",
                ["name"] = "GfxTest_NestedProfile",
                ["profile_path"] = path,
            };

            var created = ToJObject(ManageGraphics.HandleCommand(parameters));

            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(folder));
            var profile = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.IsNotNull(profile);
            string folderGuid = AssetDatabase.AssetPathToGUID(folder);
            string profileGuid = AssetDatabase.AssetPathToGUID(path);

            parameters["name"] = "GfxTest_ReusedNestedProfile";
            var reused = ToJObject(ManageGraphics.HandleCommand(parameters));

            Assert.IsTrue(reused.Value<bool>("success"), reused.ToString());
            Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(folder));
            Assert.AreEqual(profileGuid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(profile, AssetDatabase.LoadMainAssetAtPath(path));
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void VolumeCreate_Global_CreatesVolume()
        {
            RequireVolumeSystem();
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_create",
                        ["name"] = "GfxTest_Volume",
                        ["is_global"] = true,
                        ["priority"] = 10,
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result["data"]["isGlobal"].Value<bool>());
            Assert.AreEqual(10, result["data"]["priority"].Value<int>());
        }

        [Test]
        public void VolumeCreate_WithEffects_AddsEffects()
        {
            RequireVolumeSystem();
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_create",
                        ["name"] = "GfxTest_VolumeEffects",
                        ["effects"] = new JArray
                        {
                            new JObject { ["type"] = "Bloom", ["intensity"] = 2 },
                            new JObject { ["type"] = "Vignette", ["intensity"] = 0.5 },
                        },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var effects = result["data"]["effects"] as JArray;
            Assert.IsNotNull(effects);
            Assert.AreEqual(2, effects.Count);
        }

        [Test]
        public void VolumeCreate_Local_CreatesNonGlobal()
        {
            RequireVolumeSystem();
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_create",
                        ["name"] = "GfxTest_LocalVol",
                        ["is_global"] = false,
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(result["data"]["isGlobal"].Value<bool>());
        }

        [Test]
        public void VolumeAddEffect_AddsEffect()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_AddFx");

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_add_effect",
                        ["target"] = "GfxTest_AddFx",
                        ["effect"] = "Bloom",
                        ["parameters"] = new JObject { ["intensity"] = 3 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("Bloom", result["data"]["effect"].ToString());
        }

        [Test]
        public void VolumeAddEffect_Duplicate_ReturnsError()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_DupFx");
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "volume_add_effect",
                    ["target"] = "GfxTest_DupFx",
                    ["effect"] = "Bloom",
                }
            );

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_add_effect",
                        ["target"] = "GfxTest_DupFx",
                        ["effect"] = "Bloom",
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("already exists"));
        }

        [Test]
        public void VolumeAddEffect_InvalidEffect_ReturnsError()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_BadFx");

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_add_effect",
                        ["target"] = "GfxTest_BadFx",
                        ["effect"] = "FakeEffect",
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("not found"));
        }

        [Test]
        public void VolumeSetEffect_SetsParameters()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_SetFx");
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "volume_add_effect",
                    ["target"] = "GfxTest_SetFx",
                    ["effect"] = "Bloom",
                }
            );

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_set_effect",
                        ["target"] = "GfxTest_SetFx",
                        ["effect"] = "Bloom",
                        ["parameters"] = new JObject { ["intensity"] = 5, ["scatter"] = 0.8 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var setParams = result["data"]["set"] as JArray;
            Assert.IsNotNull(setParams);
            Assert.That(setParams.Select(t => t.ToString()), Contains.Item("intensity"));
            Assert.That(setParams.Select(t => t.ToString()), Contains.Item("scatter"));
        }

        [Test]
        public void VolumeSetEffect_InvalidParam_ReportsFailed()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_BadParam");
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "volume_add_effect",
                    ["target"] = "GfxTest_BadParam",
                    ["effect"] = "Bloom",
                }
            );

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_set_effect",
                        ["target"] = "GfxTest_BadParam",
                        ["effect"] = "Bloom",
                        ["parameters"] = new JObject { ["nonExistent"] = 42 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"));
            var failed = result["data"]["failed"] as JArray;
            Assert.IsNotNull(failed);
            Assert.That(failed.Select(t => t.ToString()), Contains.Item("nonExistent"));
        }

        [Test]
        public void VolumeRemoveEffect_RemovesEffect()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_RmFx");
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "volume_add_effect",
                    ["target"] = "GfxTest_RmFx",
                    ["effect"] = "Vignette",
                }
            );

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_remove_effect",
                        ["target"] = "GfxTest_RmFx",
                        ["effect"] = "Vignette",
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            // Verify it's gone
            var info = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = "GfxTest_RmFx" }));
            var effects = info["data"]["effects"] as JArray;
            Assert.IsNotNull(effects);
            Assert.AreEqual(0, effects.Count);
        }

        [Test]
        public void VolumeRemoveEffect_NonExistent_ReturnsError()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_RmMissing");

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_remove_effect",
                        ["target"] = "GfxTest_RmMissing",
                        ["effect"] = "DepthOfField",
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("not found"));
        }

        [Test]
        public void VolumeGetInfo_ReturnsEffectList()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_Info");
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "volume_add_effect",
                    ["target"] = "GfxTest_Info",
                    ["effect"] = "Bloom",
                }
            );

            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = "GfxTest_Info" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.AreEqual("GfxTest_Info", data["name"].ToString());
            var effects = data["effects"] as JArray;
            Assert.IsNotNull(effects);
            Assert.AreEqual(1, effects.Count);
            Assert.AreEqual("Bloom", effects[0]["type"].ToString());
        }

        [Test]
        public void VolumeGetInfo_NonExistentTarget_ReturnsError()
        {
            RequireVolumeSystem();
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = "NonExistentVolume" }));
            Assert.IsFalse(result.Value<bool>("success"));
        }

        [Test]
        public void VolumeSetProperties_UpdatesWeightAndPriority()
        {
            RequireVolumeSystem();
            CreateTestVolume("GfxTest_Props");

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "volume_set_properties",
                        ["target"] = "GfxTest_Props",
                        ["properties"] = new JObject { ["weight"] = 0.5, ["priority"] = 20 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var changed = result["data"]["changed"] as JArray;
            Assert.IsNotNull(changed);
            Assert.That(changed.Select(t => t.ToString()), Contains.Item("weight"));
            Assert.That(changed.Select(t => t.ToString()), Contains.Item("priority"));

            // Verify via get_info
            var info = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = "GfxTest_Props" }));
            Assert.AreEqual(0.5f, info["data"]["weight"].Value<float>(), 0.01f);
            Assert.AreEqual(20f, info["data"]["priority"].Value<float>(), 0.01f);
        }

        [TestCase("priority", false)]
        [TestCase("priority", true)]
        [TestCase("is_global", false)]
        [TestCase("is_global", true)]
        [TestCase("blend_distance", false)]
        [TestCase("blend_distance", true)]
        public void VolumeSetProperties_InvalidLaterScalarPreservesAllPropertiesAndDirtyState(string invalidProperty, bool nested)
        {
            RequireVolumeSystem();
            const string name = "GfxTest_RejectedProps";
            CreateTestVolume(name);
            var go = GameObject.Find(name);
            var volume = go.GetComponent(GraphicsHelpers.VolumeType);
            string before = EditorJsonUtility.ToJson(volume);
            int dirtyBefore = EditorUtility.GetDirtyCount(volume);
            bool sceneDirtyBefore = go.scene.isDirty;
            var values = new JObject
            {
                ["weight"] = 0.25f,
                ["priority"] = 2.5f,
                ["is_global"] = false,
                ["blend_distance"] = 3.75f,
            };
            values[invalidProperty] = "bad";
            var request = nested ? new JObject { ["properties"] = values } : values;
            request["action"] = "volume_set_properties";
            request["target"] = name;

            var result = ToJObject(ManageGraphics.HandleCommand(request));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains(invalidProperty, result.Value<string>("error"));
            Assert.AreEqual(before, EditorJsonUtility.ToJson(volume));
            Assert.AreEqual(dirtyBefore, EditorUtility.GetDirtyCount(volume));
            Assert.AreEqual(sceneDirtyBefore, go.scene.isDirty);
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void VolumeSetProperties_NullNumericValuesSkipAndNullBooleanSetsFalse(bool nested)
        {
            RequireVolumeSystem();
            const string name = "GfxTest_NullProps";
            CreateTestVolume(name);
            var original = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = name }));
            var values = new JObject
            {
                ["weight"] = JValue.CreateNull(),
                ["priority"] = 2.5f,
                ["is_global"] = JValue.CreateNull(),
                ["blend_distance"] = JValue.CreateNull(),
            };
            var request = nested ? new JObject { ["properties"] = values } : values;
            request["action"] = "volume_set_properties";
            request["target"] = name;

            var result = ToJObject(ManageGraphics.HandleCommand(request));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(new[] { "priority", "isGlobal" }, result["data"]["changed"].Values<string>().ToArray());
            var info = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_get_info", ["target"] = name }));
            Assert.AreEqual(original["data"]["weight"], info["data"]["weight"]);
            Assert.AreEqual(2.5f, info["data"]["priority"].Value<float>());
            Assert.IsFalse(info["data"]["is_global"].Value<bool>());
            Assert.AreEqual(original["data"]["blend_distance"], info["data"]["blend_distance"]);
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void VolumeListEffects_ReturnsAvailableTypes()
        {
            RequireVolumeSystem();
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_list_effects" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var effects = result["data"]["effects"] as JArray;
            Assert.IsNotNull(effects);
            Assert.Greater(effects.Count, 0);
            Assert.IsNotNull(effects[0]["name"]);
        }

        [Test]
        public void VolumeCreateProfile_NewNestedFolders_DuplicateRefusalPreservesOutput()
        {
            RequireVolumeSystem();
            string folder = $"{TempRoot}/Created/Nested";
            string path = $"{folder}/Profile.asset";
            var parameters = new JObject { ["action"] = "volume_create_profile", ["path"] = path };
            var created = ToJObject(ManageGraphics.HandleCommand(parameters));

            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(folder));
            var profile = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.IsNotNull(profile);
            string folderGuid = AssetDatabase.AssetPathToGUID(folder);
            string profileGuid = AssetDatabase.AssetPathToGUID(path);

            var refused = ToJObject(ManageGraphics.HandleCommand(parameters));

            Assert.IsFalse(refused.Value<bool>("success"));
            Assert.That(refused["error"].ToString(), Does.Contain("already exists"));
            Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(folder));
            Assert.AreEqual(profileGuid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(profile, AssetDatabase.LoadMainAssetAtPath(path));
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void VolumeCreateProfile_CreatesAsset()
        {
            RequireVolumeSystem();
            string path = $"{TempRoot}/TestProfile";
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_create_profile", ["path"] = path }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            string fullPath = result["data"]["path"].ToString();
            Assert.IsTrue(fullPath.EndsWith(".asset"));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(fullPath));
        }

        // =====================================================================
        // Bake Actions
        // =====================================================================

        [Test]
        public void BakeGetSettings_ReturnsLightmapperInfo()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsNotNull(data["lightmapper"]);
            Assert.IsNotNull(data["lightmapResolution"]);
        }

        [Test]
        public void BakeSetSettings_ChangesAndRestores()
        {
            // Read original
            var original = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));
            int origResolution = original["data"]["lightmapResolution"].Value<int>();

            // Change
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "bake_set_settings",
                        ["settings"] = new JObject { ["lightmapResolution"] = 20 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var changed = result["data"]["changed"] as JArray;
            Assert.That(changed.Select(t => t.ToString()), Contains.Item("lightmapResolution"));

            // Verify
            var verify = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));
            Assert.AreEqual(20, verify["data"]["lightmapResolution"].Value<int>());

            // Restore
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "bake_set_settings",
                    ["settings"] = new JObject { ["lightmapResolution"] = origResolution },
                }
            );
        }

        [Test]
        public void BakeStatus_ReportsNotRunning()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_status" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["isRunning"]);
        }

        [Test]
        public void BakeClear_Succeeds()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_clear" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void BakeCreateReflectionProbe_CreatesProbe()
        {
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "bake_create_reflection_probe",
                        ["name"] = "GfxTest_ReflProbe",
                        ["position"] = new JArray(0, 1, 0),
                        ["size"] = new JArray(10, 10, 10),
                        ["resolution"] = 128,
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var go = GameObject.Find("GfxTest_ReflProbe");
            Assert.IsNotNull(go);
            Assert.IsNotNull(go.GetComponent<ReflectionProbe>());
        }

        [Test]
        public void BakeCreateLightProbeGroup_CreatesGrid()
        {
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "bake_create_light_probe_group",
                        ["name"] = "GfxTest_LPGroup",
                        ["position"] = new JArray(0, 0, 0),
                        ["grid_size"] = new JArray(2, 2, 2),
                        ["spacing"] = 2,
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(8, result["data"]["probeCount"].Value<int>());
            var go = GameObject.Find("GfxTest_LPGroup");
            Assert.IsNotNull(go);
            Assert.IsNotNull(go.GetComponent<LightProbeGroup>());
        }

        [Test]
        public void BakeSetProbePositions_SetsPositions()
        {
            ManageGraphics.HandleCommand(
                new JObject
                {
                    ["action"] = "bake_create_light_probe_group",
                    ["name"] = "GfxTest_LPPos",
                    ["grid_size"] = new JArray(1, 1, 1),
                }
            );

            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "bake_set_probe_positions",
                        ["target"] = "GfxTest_LPPos",
                        ["positions"] = new JArray { new JArray(0, 0, 0), new JArray(1, 0, 0), new JArray(0, 1, 0) },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3, result["data"]["probeCount"].Value<int>());
        }

        [Test]
        public void BakeSetProbePositions_WrongComponent_ReturnsError()
        {
            var go = new GameObject("GfxTest_NoProbe");
            var result = ToJObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "bake_set_probe_positions",
                        ["target"] = "GfxTest_NoProbe",
                        ["positions"] = new JArray { new JArray(0, 0, 0) },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("LightProbeGroup"));
        }

        // =====================================================================
        // Stats Actions
        // =====================================================================

        [Test]
        public void StatsGet_ReturnsCounters()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_get" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["draw_calls"]);
            Assert.IsNotNull(result["data"]["batches"]);
            Assert.IsNotNull(result["data"]["triangles"]);
        }

        [Test]
        public void StatsListCounters_ReturnsList()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_list_counters" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var counters = result["data"]["counters"] as JArray;
            Assert.IsNotNull(counters);
            Assert.Greater(counters.Count, 0);
        }

        [Test]
        public void StatsGetMemory_ReturnsMemoryInfo()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_get_memory" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["totalAllocatedMB"]);
            Assert.IsNotNull(result["data"]["graphicsDriverMB"]);
        }

        [TestCase("Wireframe")]
        [TestCase("wireframe")]
        [TestCase("1")]
        [TestCase("Normal")]
        [TestCase("-1")]
        public void StatsSetSceneDebug_ValidMode_Succeeds(string mode)
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_set_scene_debug", ["mode"] = mode }));
            if (_hasSceneView)
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            else
                Assert.IsFalse(result.Value<bool>("success"));
        }

        [Test]
        public void StatsSetSceneDebug_InvalidMode_ReturnsError()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_set_scene_debug", ["mode"] = "InvalidMode" }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("Valid:"));
        }

        [TestCase("999")]
        [TestCase("-2")]
        [TestCase("2147483647")]
        [TestCase("UserDefined")]
        [TestCase("-2147483648")]
        [TestCase("Baked")]
        public void StatsSetSceneDebug_UnavailableModeRejectsWithoutChangingViewOrLogging(string mode)
        {
            var sceneView = UnityEditor.SceneView.lastActiveSceneView;
            var previousMode = sceneView != null ? sceneView.cameraMode : default;

            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_set_scene_debug", ["mode"] = mode }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains($"'{mode}'", result.Value<string>("error"));
            StringAssert.DoesNotContain("Error in action", result.Value<string>("error"));
            if (sceneView != null)
                Assert.AreEqual(previousMode, sceneView.cameraMode);
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        // =====================================================================
        // Pipeline Actions
        // =====================================================================

        [Test]
        public void PipelineGetInfo_ReturnsPipelineName()
        {
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "pipeline_get_info" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["pipelineName"]);
            Assert.IsNotNull(result["data"]["qualityLevelName"]);
        }

        [Test]
        public void PipelineGetSettings_ReturnsSettings()
        {
            using var pipeline = OwnedUrpPipeline.Create();
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "pipeline_get_settings" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(pipeline.AssetPath, result["data"]["assetPath"].ToString());
            var settings = result["data"]["settings"];
            Assert.IsNotNull(settings);
            Assert.IsNotNull(settings["renderScale"]);
        }

        [Test]
        public void PipelineSetQuality_InvalidLevel_ReturnsError()
        {
            int originalLevel = QualitySettings.GetQualityLevel();
            var originalPipeline = QualitySettings.renderPipeline;
            var originalActivePipeline = GraphicsSettings.currentRenderPipeline;
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "pipeline_set_quality", ["level"] = "NonExistentLevel" }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("level"));
            Assert.That(result["error"].ToString(), Does.Contain("Int32"));
            Assert.AreEqual(originalLevel, QualitySettings.GetQualityLevel());
            Assert.IsTrue(QualitySettings.renderPipeline == originalPipeline);
            Assert.IsTrue(GraphicsSettings.currentRenderPipeline == originalActivePipeline);
            UnityEngine.TestTools.LogAssert.NoUnexpectedReceived();
        }

        // =====================================================================
        // Renderer Feature Actions (URP only)
        // =====================================================================

        [Test]
        public void FeatureList_ReturnsFeatures()
        {
            using var pipeline = OwnedUrpPipeline.Create();
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "feature_list" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["features"]);
            Assert.IsNotNull(result["data"]["rendererDataName"]);
            Assert.AreEqual(pipeline.RendererName, result["data"]["rendererDataName"].ToString());
            Assert.AreEqual(0, ((JArray)result["data"]["features"]).Count);
        }

        [Test]
        public void FeatureAdd_InvalidType_ReturnsError()
        {
            using var pipeline = OwnedUrpPipeline.Create();
            var result = ToJObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "feature_add", ["type"] = "NonExistentFeature" }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.That(result["error"].ToString(), Does.Contain("not found"));
            Assert.That(result["error"].ToString(), Does.Contain("Available:"));
        }

        // =====================================================================
        // Helpers
        // =====================================================================

        private sealed class OwnedUrpPipeline : IDisposable
        {
            private readonly PropertyInfo _graphicsPipelineProperty;
            private readonly RenderPipelineAsset _originalGraphicsPipeline;
            private readonly RenderPipelineAsset _originalQualityPipeline;
            private readonly int _originalQualityLevel;
            private string _root;
            private string _rootGuid;
            private ScriptableObject _renderer;
            private RenderPipelineAsset _pipeline;
            private bool _qualityPipelineAssigned;
            private bool _disposed;

            public string AssetPath => _root + "/Pipeline.asset";
            public string RendererName => _renderer.name;

            private OwnedUrpPipeline()
            {
                // Keep the test assembly independent of optional URP assemblies and the
                // defaultRenderPipeline API rename in newer Editors.
                _graphicsPipelineProperty =
                    typeof(GraphicsSettings).GetProperty("defaultRenderPipeline", BindingFlags.Public | BindingFlags.Static)
                    ?? typeof(GraphicsSettings).GetProperty("renderPipelineAsset", BindingFlags.Public | BindingFlags.Static);
                Assert.IsNotNull(_graphicsPipelineProperty, "The Editor must expose its default render pipeline asset.");
                _originalGraphicsPipeline = (RenderPipelineAsset)_graphicsPipelineProperty.GetValue(null);
                _originalQualityPipeline = QualitySettings.renderPipeline;
                _originalQualityLevel = QualitySettings.GetQualityLevel();
            }

            public static OwnedUrpPipeline Create()
            {
                var pipelineType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset, Unity.RenderPipelines.Universal.Runtime");
                var rendererType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRendererData, Unity.RenderPipelines.Universal.Runtime");
                if (pipelineType == null || rendererType == null)
                    Assert.Ignore("The optional URP package is not installed — skipping owned URP fixture.");

                var owned = new OwnedUrpPipeline();
                try
                {
                    owned._root = "Assets/__McpGraphicsUrp_" + Guid.NewGuid().ToString("N");
                    Assert.IsFalse(AssetDatabase.IsValidFolder(owned._root));
                    owned._rootGuid = AssetDatabase.CreateFolder("Assets", owned._root.Substring("Assets/".Length));
                    Assert.IsNotEmpty(owned._rootGuid);
                    Assert.AreEqual(owned._root, AssetDatabase.GUIDToAssetPath(owned._rootGuid));

                    owned._renderer = ScriptableObject.CreateInstance(rendererType);
                    owned._renderer.name = "OwnedGraphicsRenderer";
                    AssetDatabase.CreateAsset(owned._renderer, owned._root + "/Renderer.asset");
                    Assert.IsTrue(EditorUtility.IsPersistent(owned._renderer));
                    var create = pipelineType
                        .GetMethods(BindingFlags.Public | BindingFlags.Static)
                        .SingleOrDefault(method =>
                            method.Name == "Create"
                            && method.ReturnType == pipelineType
                            && method.GetParameters().Length == 1
                            && method.GetParameters()[0].ParameterType.IsInstanceOfType(owned._renderer)
                        );
                    Assert.IsNotNull(create, "Installed URP must expose Create(ScriptableRendererData).");
                    owned._pipeline = (RenderPipelineAsset)create.Invoke(null, new object[] { owned._renderer });
                    owned._pipeline.name = "OwnedGraphicsPipeline";
                    AssetDatabase.CreateAsset(owned._pipeline, owned.AssetPath);
                    Assert.IsTrue(EditorUtility.IsPersistent(owned._pipeline));
                    using (var serialized = new SerializedObject(owned._pipeline))
                    {
                        var renderers = serialized.FindProperty("m_RendererDataList");
                        Assert.IsNotNull(renderers);
                        Assert.AreEqual(1, renderers.arraySize);
                        Assert.IsTrue(renderers.GetArrayElementAtIndex(0).objectReferenceValue == owned._renderer);
                    }

                    // A synchronous API test needs only the current quality override; it
                    // does not render a frame or initialize URP global project settings.
                    owned._qualityPipelineAssigned = true;
                    QualitySettings.renderPipeline = owned._pipeline;
                    Assert.IsTrue(GraphicsSettings.currentRenderPipeline == owned._pipeline);
                    return owned;
                }
                catch
                {
                    owned.Dispose();
                    throw;
                }
            }

            public void Dispose()
            {
                if (_disposed)
                    return;
                _disposed = true;
                if (_qualityPipelineAssigned)
                {
                    if (QualitySettings.GetQualityLevel() != _originalQualityLevel)
                        QualitySettings.SetQualityLevel(_originalQualityLevel, false);
                    QualitySettings.renderPipeline = _originalQualityPipeline;
                    if ((RenderPipelineAsset)_graphicsPipelineProperty.GetValue(null) != _originalGraphicsPipeline)
                        _graphicsPipelineProperty.SetValue(null, _originalGraphicsPipeline);
                    Assert.AreEqual(_originalQualityLevel, QualitySettings.GetQualityLevel());
                    Assert.IsTrue(QualitySettings.renderPipeline == _originalQualityPipeline);
                    Assert.IsTrue((RenderPipelineAsset)_graphicsPipelineProperty.GetValue(null) == _originalGraphicsPipeline);
                }

                if (!string.IsNullOrEmpty(_rootGuid))
                {
                    Assert.That(_root, Does.StartWith("Assets/__McpGraphicsUrp_"));
                    Assert.AreEqual(_root, AssetDatabase.GUIDToAssetPath(_rootGuid));
                    Assert.AreEqual(_rootGuid, AssetDatabase.AssetPathToGUID(_root));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(_root));
                }
                if (_pipeline != null && !EditorUtility.IsPersistent(_pipeline))
                    UnityEngine.Object.DestroyImmediate(_pipeline);
                if (_renderer != null && !EditorUtility.IsPersistent(_renderer))
                    UnityEngine.Object.DestroyImmediate(_renderer);
            }
        }

        private void CreateTestVolume(string name)
        {
            ManageGraphics.HandleCommand(new JObject { ["action"] = "volume_create", ["name"] = name });
        }
    }
}
