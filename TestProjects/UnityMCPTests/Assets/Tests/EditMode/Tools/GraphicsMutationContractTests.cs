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
    }
}
