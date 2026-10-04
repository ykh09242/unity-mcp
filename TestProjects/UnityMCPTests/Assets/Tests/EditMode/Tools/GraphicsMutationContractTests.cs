using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using MCPForUnity.Editor.Tools.Graphics;

namespace MCPForUnityTests.Editor.Tools
{
    public class GraphicsMutationContractTests
    {
        private Scene _originalScene;
        private Scene _testScene;
        private LightingSettings _ownedSettings;

        [SetUp]
        public void SetUp()
        {
            _originalScene = SceneManager.GetActiveScene();
            _testScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(_testScene);
        }

        [TearDown]
        public void TearDown()
        {
            if (_originalScene.IsValid() && _originalScene.isLoaded)
                SceneManager.SetActiveScene(_originalScene);
            if (_testScene.IsValid() && _testScene.isLoaded)
                EditorSceneManager.CloseScene(_testScene, true);
            if (_ownedSettings != null)
                Object.DestroyImmediate(_ownedSettings);
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
        [TestCase("realtime_gi", "0", false)]
        [TestCase("REALTIME_GI", "\"off\"", false)]
        [TestCase("realtimeGI", "null", true)]
        [TestCase("realtime_gi", "\"invalid\"", true)]
        public void RealtimeGIWrite_PreservesAliasesCoercionAndIdentity(string alias, string json, bool expected)
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
            var originalMapper = _ownedSettings.lightmapper;
            var response = SetLightingSettings(new JObject { ["realtimeGI"] = false, ["lightmapper"] = "invalid" });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "realtimeGI" }, response["data"]["changed"].ToObject<string[]>());
            CollectionAssert.AreEqual(new[] { "lightmapper" }, response["data"]["failed"].ToObject<string[]>());
            Assert.IsFalse(ReadRealtimeGI(_ownedSettings));
            Assert.AreEqual(originalMapper, _ownedSettings.lightmapper);
            Assert.AreSame(_ownedSettings, Lightmapping.lightingSettings);
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

        private static JObject SetLightingSettings(JObject settings) => JObject.FromObject(ManageGraphics.HandleCommand(
            new JObject { ["action"] = "bake_set_settings", ["settings"] = settings }));

        [TestCase("Invalid")]
        [TestCase("999")]
        public void InvalidFogMode_DoesNotChangeEnabledState(string mode)
        {
            RenderSettings.fog = true;
            var originalMode = RenderSettings.fogMode;
            var result = JObject.FromObject(ManageGraphics.HandleCommand(new JObject
            {
                ["action"] = "skybox_set_fog", ["fog_enabled"] = false, ["fog_mode"] = mode
            }));

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
                var result = JObject.FromObject(VolumeOps.CreateVolume(new JObject
                {
                    ["name"] = name, ["profile_path"] = "Assets//" + name + ".asset"
                }));
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.IsNull(GameObject.Find(name));
            }
            finally
            {
                var leftover = GameObject.Find(name);
                if (leftover != null) Object.DestroyImmediate(leftover);
            }
        }

        [TestCase("Invalid")]
        [TestCase("999")]
        public void InvalidReflectionMode_DoesNotChangeIntensityOrBounces(string mode)
        {
            RenderSettings.reflectionIntensity = 1;
            RenderSettings.reflectionBounces = 2;
            var originalMode = RenderSettings.defaultReflectionMode;
            var result = JObject.FromObject(ManageGraphics.HandleCommand(new JObject
            {
                ["action"] = "skybox_set_reflection", ["intensity"] = 0, ["bounces"] = 0,
                ["reflection_mode"] = mode
            }));

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
                if (cube != null) Object.DestroyImmediate(cube);
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
                Assert.AreSame(texture, effect.texture.GetType().GetProperty("value").GetValue(effect.texture));
                Assert.IsTrue((bool)effect.texture.GetType().GetProperty("overrideState").GetValue(effect.texture));
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
                if (texture != null) Object.DestroyImmediate(texture);
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
            if (genericParameter == null) Assert.Ignore("Volume system not available.");
            var parameterType = genericParameter.MakeGenericType(texture2DOnly ? typeof(Texture2D) : typeof(Texture));
            return new TextureEffect
            {
                texture = System.Activator.CreateInstance(parameterType,
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
                    null, new object[] { value, false }, null)
            };
        }
    }
}
