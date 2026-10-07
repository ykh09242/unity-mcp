using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class GraphicsMutationContractTests
    {
        private readonly PrefabTestSceneFixture _sceneFixture = new PrefabTestSceneFixture();
        private Scene _testScene;
        private LightingSettings _ownedSettings;

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap() => _sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap() => _sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            _ownedSettings = null;
            _testScene = default;
            _testScene = _sceneFixture.Create("McpGraphicsMutation_", System.Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                _sceneFixture.Close();
            }
            finally
            {
                if (_ownedSettings != null)
                {
                    Undo.ClearUndo(_ownedSettings);
                    Object.DestroyImmediate(_ownedSettings);
                }
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PipelineEmptyKeyRetainsBestEffortChangedAndFailedContract(bool emptyFirst)
        {
            WithOwnedPipeline(asset =>
            {
                var settings = new JObject();
                if (emptyFirst)
                    settings.Add("", 1);
                settings.Add("ContractSetting", 17);
                if (!emptyFirst)
                    settings.Add("", 1);
                var response = JObject.FromObject(RenderPipelineOps.SetSettings(new JObject { ["settings"] = settings }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(17, asset.ContractSetting);
                CollectionAssert.AreEqual(new[] { "ContractSetting" }, response["data"]["changed"].ToObject<string[]>());
                CollectionAssert.AreEqual(new[] { "" }, response["data"]["failed"].ToObject<string[]>());
            });
        }

        [TestCase("")]
        [TestCase("NoSuchPipelineSetting")]
        public void InvalidOnlyPipelineSettingsPreserveAssetAndDirtyState(string key)
        {
            WithOwnedPipeline(asset =>
            {
                int dirty = EditorUtility.GetDirtyCount(asset);
                var response = JObject.FromObject(RenderPipelineOps.SetSettings(new JObject { ["settings"] = new JObject { [key] = 1 } }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.IsEmpty(response["data"]["changed"]);
                CollectionAssert.AreEqual(new[] { key }, response["data"]["failed"].ToObject<string[]>());
                Assert.AreEqual(3, asset.ContractSetting);
                Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(asset));
            });
        }

        private static void WithOwnedPipeline(System.Action<ContractPipelineAsset> action)
        {
            var originalDefault = GraphicsSettings.defaultRenderPipeline;
            var originalQuality = QualitySettings.renderPipeline;
            var asset = ScriptableObject.CreateInstance<ContractPipelineAsset>();
            try
            {
                GraphicsSettings.defaultRenderPipeline = asset;
                QualitySettings.renderPipeline = null;
                Assert.AreSame(asset, GraphicsSettings.currentRenderPipeline);
                action(asset);
            }
            finally
            {
                QualitySettings.renderPipeline = originalQuality;
                GraphicsSettings.defaultRenderPipeline = originalDefault;
                Undo.ClearUndo(asset);
                Object.DestroyImmediate(asset);
            }
        }

        private sealed class ContractPipelineAsset : RenderPipelineAsset
        {
            public int ContractSetting { get; set; } = 3;

            protected override RenderPipeline CreatePipeline() => null;
        }

        [Test]
        public void LightingSettingsRead_UsesDefaultsWithoutAssigningAnAsset()
        {
            Assert.IsFalse(Lightmapping.TryGetLightingSettings(out _), "The isolated scene must have no assigned settings.");
            var defaults = Lightmapping.lightingSettingsDefaults;
            var result = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(Lightmapping.TryGetLightingSettings(out _));
            Assert.AreEqual(defaults.lightmapResolution, result["data"].Value<float>("lightmapResolution"));
            Assert.AreEqual(defaults.bakedGI, result["data"].Value<bool>("bakedGI"));
            Assert.AreEqual(ReadRealtimeGI(defaults), result["data"].Value<bool>("realtimeGI"));
            var status = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_status" }));
            Assert.IsTrue(status.Value<bool>("success"), status.ToString());
            Assert.AreEqual(ReadRealtimeGI(defaults), status["data"].Value<bool>("realtimeGI"));
            Assert.IsFalse(Lightmapping.TryGetLightingSettings(out _));
        }

        [Test]
        public void LightingSettingsRead_PreservesAssignedReferenceAndFalseValue()
        {
            _ownedSettings = new LightingSettings { name = "GraphicsContractSettings", bakedGI = false };
            Lightmapping.lightingSettings = _ownedSettings;
            var result = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Assert.IsFalse(result["data"].Value<bool>("bakedGI"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RealtimeGIRead_PreservesAssignedValueIdentityAndDirtyState(bool value)
        {
            AssignOwnedSettings(value);
            int dirtyCount = EditorUtility.GetDirtyCount(_ownedSettings);
            foreach (string action in new[] { "bake_get_settings", "bake_status" })
            {
                var response = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = action }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(value, response["data"].Value<bool>("realtimeGI"));
            }
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(_ownedSettings));
        }

        [TestCase("realtimeGI", "false", false)]
        [TestCase("realtime_gi", "false", false)]
        [TestCase("REALTIME_GI", "\"false\"", false)]
        [TestCase("realtimeGI", "null", true)]
        [TestCase("realtime_gi", "\"true\"", true)]
        public void RealtimeGIWrite_PreservesAliasesStrictValuesAndIdentity(string alias, string json, bool expected)
        {
            AssignOwnedSettings(true);
            var response = SetLightingSettings(new JObject { [alias] = JToken.Parse(json) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { alias }, response["data"]["changed"].ToObject<string[]>());
            Assert.IsEmpty(response["data"]["failed"]);
            Assert.AreEqual(expected, ReadRealtimeGI(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Assert.IsEmpty(AssetDatabase.GetAssetPath(_ownedSettings));
        }

        [Test]
        public void RealtimeGIAliases_ReadFallbackAfterEarlierWriteAndPreserveUndo()
        {
            AssignOwnedSettings(false);
            Undo.IncrementCurrentGroup();
            var response = SetLightingSettings(new JObject { ["realtimeGI"] = true, ["realtime_gi"] = JValue.CreateNull() });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(ReadRealtimeGI(_ownedSettings));
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.IsFalse(ReadRealtimeGI(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Undo.IncrementCurrentGroup();
        }

        [Test]
        public void MixedInvalidLightingSetting_RetainsFailedKeyContract()
        {
            AssignOwnedSettings(true);
            var originalMapper = ReadLightmapper(_ownedSettings);
            var response = SetLightingSettings(new JObject { ["realtimeGI"] = false, ["lightmapper"] = "invalid" });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "realtimeGI" }, response["data"]["changed"].ToObject<string[]>());
            CollectionAssert.AreEqual(new[] { "lightmapper" }, response["data"]["failed"].ToObject<string[]>());
            Assert.IsFalse(ReadRealtimeGI(_ownedSettings));
            Assert.AreEqual(originalMapper, ReadLightmapper(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
        }

        [TestCase("\"ProgressiveCPU\"", 1)]
        [TestCase("\"progressivegpu\"", 2)]
        [TestCase("1", 1)]
        [TestCase("2", 2)]
        public void LightmapperWrite_RoundTripsSceneBackendWithoutReplacingSettings(string json, int expected)
        {
            AssignOwnedSettings(true);
            var response = SetLightingSettings(new JObject { ["lightmapper"] = JToken.Parse(json) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "lightmapper" }, response["data"]["changed"].ToObject<string[]>());
            Assert.IsEmpty(response["data"]["failed"]);
            Assert.AreEqual(expected, ReadLightmapper(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Assert.IsEmpty(AssetDatabase.GetAssetPath(_ownedSettings));

            int dirtyCount = EditorUtility.GetDirtyCount(_ownedSettings);
            var read = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_get_settings" }));
            Assert.IsTrue(read.Value<bool>("success"), read.ToString());
            Assert.AreEqual(((LightingSettings.Lightmapper)expected).ToString(), read["data"].Value<string>("lightmapper"));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(_ownedSettings));
        }

        [TestCase("\"invalid\"")]
        [TestCase("999")]
        [TestCase("true")]
        [TestCase("null")]
        public void InvalidOnlyLightmapper_DoesNotMutateAssignedSettings(string json)
        {
            AssignOwnedSettings(true);
            string original = EditorJsonUtility.ToJson(_ownedSettings);
            int dirtyCount = EditorUtility.GetDirtyCount(_ownedSettings);
            var response = SetLightingSettings(new JObject { ["lightmapper"] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("lightmapper", response.Value<string>("error"));
            Assert.AreEqual(original, EditorJsonUtility.ToJson(_ownedSettings));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
        }

        [TestCase("\"invalid\"")]
        [TestCase("999")]
        public void InvalidOnlyLightmapper_DoesNotCreateOrAssignSettings(string json)
        {
            Assert.IsFalse(Lightmapping.TryGetLightingSettings(out _));
            var defaults = Lightmapping.lightingSettingsDefaults;
            int settingsCount = UnityEngine.Resources.FindObjectsOfTypeAll<LightingSettings>().Length;
            bool sceneDirty = _testScene.isDirty;
            string originalDefaults = EditorJsonUtility.ToJson(defaults);
            var response = SetLightingSettings(new JObject { ["lightmapper"] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(Lightmapping.TryGetLightingSettings(out _));
            Assert.AreEqual(settingsCount, UnityEngine.Resources.FindObjectsOfTypeAll<LightingSettings>().Length);
            Assert.AreEqual(sceneDirty, _testScene.isDirty);
            Assert.AreEqual(originalDefaults, EditorJsonUtility.ToJson(defaults));
        }

        [TestCase(1)]
        [TestCase(2)]
        public void SerializedLightmapperAdapter_RoundTripsAndPreservesUndo(int expected)
        {
            AssignOwnedSettings(true);
            int original = expected == 1 ? 2 : 1;
            Assert.IsTrue(LightBakingOps.TrySetSerializedLightmapper(_ownedSettings, (LightingSettings.Lightmapper)original));
            Undo.IncrementCurrentGroup();
            Undo.RecordObject(_ownedSettings, "Test serialized lightmapper adapter");
            Assert.IsTrue(LightBakingOps.TrySetSerializedLightmapper(_ownedSettings, (LightingSettings.Lightmapper)expected));
            int dirtyCount = EditorUtility.GetDirtyCount(_ownedSettings);
            Assert.IsTrue(LightBakingOps.TryReadSerializedLightmapper(_ownedSettings, out var actual));
            Assert.AreEqual(expected, (int)actual);
            Assert.AreEqual(expected, ReadLightmapper(_ownedSettings));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(_ownedSettings));
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(original, ReadLightmapper(_ownedSettings));
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
            Undo.IncrementCurrentGroup();
        }

        [Test]
        public void SerializedLightmapperAdapter_RejectsInvalidValueWithoutMutation()
        {
            AssignOwnedSettings(true);
            int original = ReadLightmapper(_ownedSettings);
            int dirtyCount = EditorUtility.GetDirtyCount(_ownedSettings);
            Assert.IsFalse(LightBakingOps.TrySetSerializedLightmapper(_ownedSettings, (LightingSettings.Lightmapper)999));
            Assert.IsFalse(LightBakingOps.TrySetSerializedLightmapper(null, (LightingSettings.Lightmapper)1));
            Assert.IsFalse(LightBakingOps.TryReadSerializedLightmapper(null, out _));
            Assert.AreEqual(original, ReadLightmapper(_ownedSettings));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(_ownedSettings));
        }

        private static int ReadLightmapper(LightingSettings settings)
        {
            using var serializedSettings = new SerializedObject(settings);
            var property = serializedSettings.FindProperty("m_BakeBackend");
            Assert.IsNotNull(property);
            Assert.IsTrue(property.propertyType == SerializedPropertyType.Integer || property.propertyType == SerializedPropertyType.Enum);
            return property.intValue;
        }

        private static System.Collections.IEnumerable InvalidScalarCases()
        {
            foreach (
                var pair in new[]
                {
                    new[] { "realtimeGI", "0" },
                    new[] { "realtime_gi", "\"off\"" },
                    new[] { "baked_gi", "[]" },
                    new[] { "ao", "{}" },
                    new[] { "lightmapResolution", "true" },
                    new[] { "lightmap_max_size", "1.5" },
                    new[] { "direct_sample_count", "[]" },
                    new[] { "indirectSampleCount", "{}" },
                    new[] { "environment_sample_count", "true" },
                    new[] { "aoMaxDistance", "\"bad\"" },
                    new[] { "bounce_count", "{}" },
                }
            )
            foreach (bool assigned in new[] { false, true })
                yield return new TestCaseData(pair[0], pair[1], assigned);
        }

        [TestCaseSource(nameof(InvalidScalarCases))]
        public void InvalidOnlyLightingScalar_PreservesSettingsAndDoesNotAllocate(string key, string json, bool assigned)
        {
            if (assigned)
                AssignOwnedSettings(true);
            var defaults = Lightmapping.lightingSettingsDefaults;
            var original = assigned ? _ownedSettings : defaults;
            string originalJson = EditorJsonUtility.ToJson(original);
            int dirtyCount = EditorUtility.GetDirtyCount(original);
            int settingsCount = UnityEngine.Resources.FindObjectsOfTypeAll<LightingSettings>().Length;
            bool sceneDirty = _testScene.isDirty;

            var response = SetLightingSettings(new JObject { [key] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(key, response.Value<string>("error"));
            Assert.AreEqual(assigned, Lightmapping.TryGetLightingSettings(out var current));
            if (assigned)
                Assert.AreSame(_ownedSettings, current);
            Assert.AreEqual(originalJson, EditorJsonUtility.ToJson(original));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(original));
            Assert.AreEqual(settingsCount, UnityEngine.Resources.FindObjectsOfTypeAll<LightingSettings>().Length);
            Assert.AreEqual(sceneDirty, _testScene.isDirty);
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("realtimeGI")]
        [TestCase("bounce_count")]
        public void MixedInvalidLightingScalar_AppliesValidKeysWithoutWarning(string invalidKey)
        {
            AssignOwnedSettings(true);
            var response = SetLightingSettings(new JObject { [invalidKey] = new JObject(), ["bakedGI"] = false });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "bakedGI" }, response["data"]["changed"].ToObject<string[]>());
            CollectionAssert.AreEqual(new[] { invalidKey }, response["data"]["failed"].ToObject<string[]>());
            Assert.IsFalse(_ownedSettings.bakedGI);
            Assert.IsTrue(ReadRealtimeGI(_ownedSettings));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void LightingScalarNullAliases_PreserveEarlierWrites()
        {
            AssignOwnedSettings(true);
            var response = SetLightingSettings(
                new JObject
                {
                    ["lightmapResolution"] = 13.5f,
                    ["lightmap_resolution"] = JValue.CreateNull(),
                    ["directSampleCount"] = 16,
                    ["direct_sample_count"] = JValue.CreateNull(),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(13.5f, _ownedSettings.lightmapResolution);
            Assert.AreEqual(16, _ownedSettings.directSampleCount);
            Assert.AreEqual(4, ((JArray)response["data"]["changed"]).Count);
            Assert.IsEmpty(response["data"]["failed"]);
        }

        private void AssignOwnedSettings(bool realtimeGI)
        {
            _ownedSettings = new LightingSettings { name = "RealtimeGIContractSettings" };
#if UNITY_6000_7_OR_NEWER
            using var serializedSettings = new SerializedObject(_ownedSettings);
            var property = serializedSettings.FindProperty("m_EnableRealtimeLightmaps");
            Assert.IsNotNull(property);
            Assert.AreEqual(SerializedPropertyType.Boolean, property.propertyType);
            property.boolValue = realtimeGI;
            serializedSettings.ApplyModifiedPropertiesWithoutUndo();
#else
            _ownedSettings.realtimeGI = realtimeGI;
#endif
            Lightmapping.lightingSettings = _ownedSettings;
        }

        private static bool ReadRealtimeGI(LightingSettings settings)
        {
#if UNITY_6000_7_OR_NEWER
            using var serializedSettings = new SerializedObject(settings);
            var property = serializedSettings.FindProperty("m_EnableRealtimeLightmaps");
            Assert.IsNotNull(property);
            Assert.AreEqual(SerializedPropertyType.Boolean, property.propertyType);
            return property.boolValue;
#else
            return settings.realtimeGI;
#endif
        }

        private static JObject SetLightingSettings(JObject settings) =>
            JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "bake_set_settings", ["settings"] = settings }));

        [TestCase("Invalid")]
        [TestCase("999")]
        public void InvalidFogMode_DoesNotChangeEnabledState(string mode)
        {
            RenderSettings.fog = true;
            var originalMode = RenderSettings.fogMode;
            var result = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "skybox_set_fog",
                        ["fog_enabled"] = false,
                        ["fog_mode"] = mode,
                    }
                )
            );

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(RenderSettings.fog);
            Assert.AreEqual(originalMode, RenderSettings.fogMode);
        }

        [Test]
        public void InvalidVolumeProfilePath_DoesNotAllocateAGameObject()
        {
            string name = "GraphicsContractVolume_" + System.Guid.NewGuid().ToString("N");
            try
            {
                var result = JObject.FromObject(VolumeOps.CreateVolume(new JObject { ["name"] = name, ["profile_path"] = "Assets//" + name + ".asset" }));
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.IsNull(GameObject.Find(name));
            }
            finally
            {
                var leftover = GameObject.Find(name);
                if (leftover != null)
                    Object.DestroyImmediate(leftover);
            }
        }

        [TestCase("Invalid")]
        [TestCase("999")]
        public void InvalidReflectionMode_DoesNotChangeIntensityOrBounces(string mode)
        {
            RenderSettings.reflectionIntensity = 1;
            RenderSettings.reflectionBounces = 2;
            var originalMode = RenderSettings.defaultReflectionMode;
            var result = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "skybox_set_reflection",
                        ["intensity"] = 0,
                        ["bounces"] = 0,
                        ["reflection_mode"] = mode,
                    }
                )
            );

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1, RenderSettings.reflectionIntensity);
            Assert.AreEqual(2, RenderSettings.reflectionBounces);
            Assert.AreEqual(originalMode, RenderSettings.defaultReflectionMode);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingVolumeTexture_DoesNotClearValueOrEnableOverride(bool texture2DOnly)
        {
            var original = new Texture2D(2, 2);
            try
            {
                var effect = CreateTextureEffect(original, texture2DOnly);
                string missingPath = "Assets/GraphicsContractMissing_" + System.Guid.NewGuid().ToString("N") + ".asset";
                Assert.IsFalse(VolumeOps.SetVolumeParameter(effect, "texture", new JValue(missingPath)));
                Assert.AreSame(original, effect.texture.GetType().GetProperty("value").GetValue(effect.texture));
                Assert.IsFalse((bool)effect.texture.GetType().GetProperty("overrideState").GetValue(effect.texture));
            }
            finally
            {
                Object.DestroyImmediate(original);
            }
        }

        [Test]
        public void WrongTypeVolumeTexture_DoesNotClearValueOrEnableOverride()
        {
            string path = "Assets/GraphicsContractCube_" + System.Guid.NewGuid().ToString("N") + ".asset";
            var original = new Texture2D(2, 2);
            var cube = new Cubemap(2, TextureFormat.RGBA32, false);
            try
            {
                var effect = CreateTextureEffect(original, true);
                AssetDatabase.CreateAsset(cube, path);
                Assert.IsFalse(VolumeOps.SetVolumeParameter(effect, "texture", new JValue(path)));
                Assert.AreSame(original, effect.texture.GetType().GetProperty("value").GetValue(effect.texture));
                Assert.IsFalse((bool)effect.texture.GetType().GetProperty("overrideState").GetValue(effect.texture));
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
                if (cube != null)
                    Object.DestroyImmediate(cube);
                Object.DestroyImmediate(original);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ValidVolumeTexture_AssignsAssetAndEnablesOverride(bool cubemap)
        {
            string path = "Assets/GraphicsContractTexture_" + System.Guid.NewGuid().ToString("N") + ".asset";
            Texture texture = cubemap ? (Texture)new Cubemap(2, TextureFormat.RGBA32, false) : new Texture2D(2, 2);
            try
            {
                var effect = CreateTextureEffect(null, !cubemap);
                AssetDatabase.CreateAsset(texture, path);
                Assert.IsTrue(VolumeOps.SetVolumeParameter(effect, "texture", new JValue(path)));
                var actual = (Texture)effect.texture.GetType().GetProperty("value").GetValue(effect.texture);
                Assert.IsNotNull(actual);
                Assert.IsTrue(texture == actual, "The assigned reference must point to the same native texture asset.");
                Assert.AreEqual(path, AssetDatabase.GetAssetPath(actual));
                Assert.IsTrue((bool)effect.texture.GetType().GetProperty("overrideState").GetValue(effect.texture));
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
                if (texture != null)
                    Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void NullVolumeTexture_ClearsValueAndEnablesOverride()
        {
            var original = new Texture2D(2, 2);
            try
            {
                var effect = CreateTextureEffect(original, false);
                Assert.IsTrue(VolumeOps.SetVolumeParameter(effect, "texture", JValue.CreateNull()));
                Assert.IsNull(effect.texture.GetType().GetProperty("value").GetValue(effect.texture));
                Assert.IsTrue((bool)effect.texture.GetType().GetProperty("overrideState").GetValue(effect.texture));
            }
            finally
            {
                Object.DestroyImmediate(original);
            }
        }

        private sealed class TextureEffect
        {
            public object texture;
        }

        private static TextureEffect CreateTextureEffect(Texture value, bool texture2DOnly)
        {
            var genericParameter = System.Type.GetType("UnityEngine.Rendering.VolumeParameter`1, Unity.RenderPipelines.Core.Runtime");
            if (genericParameter == null)
                Assert.Ignore("Volume system not available.");
            var parameterType = genericParameter.MakeGenericType(texture2DOnly ? typeof(Texture2D) : typeof(Texture));
            return new TextureEffect
            {
                texture = System.Activator.CreateInstance(
                    parameterType,
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
                    null,
                    new object[] { value, false },
                    null
                ),
            };
        }
    }
}
