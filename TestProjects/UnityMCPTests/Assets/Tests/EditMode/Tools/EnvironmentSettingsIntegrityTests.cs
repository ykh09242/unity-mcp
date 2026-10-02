using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Graphics;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class EnvironmentSettingsIntegrityTests
    {
        private readonly Dictionary<PropertyInfo, object> settings = new Dictionary<PropertyInfo, object>();
        private readonly List<Object> objects = new List<Object>();
        private Scene originalScene;
        private Scene ownedScene;
        private Object[] originalSelection;
        private Object originalActiveSelection;
        private bool captured;
        private string root;
        private string rootGuid;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            ownedScene = default;
            root = rootGuid = null;
            settings.Clear();
            objects.Clear();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null) Assert.Ignore("An unowned prefab stage is open.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            captured = true;
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
            string reflection = typeof(RenderSettings).GetProperty("customReflectionTexture") != null
                ? "customReflectionTexture" : "customReflection";
            foreach (string name in new[]
            {
                "ambientMode", "ambientSkyColor", "ambientEquatorColor", "ambientGroundColor", "ambientLight",
                "ambientIntensity", "fog", "fogMode", "fogColor", "fogDensity", "fogStartDistance", "fogEndDistance",
                "reflectionIntensity", "reflectionBounces", "defaultReflectionMode", "defaultReflectionResolution",
                reflection, "skybox", "sun", "subtractiveShadowColor"
            })
            {
                var property = typeof(RenderSettings).GetProperty(name, BindingFlags.Public | BindingFlags.Static);
                Assert.IsNotNull(property, name);
                settings.Add(property, property.GetValue(null));
            }
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured) return;
            try
            {
                if (ownedScene.IsValid() && ownedScene.isLoaded) SceneManager.SetActiveScene(ownedScene);
                foreach (var entry in settings)
                    if (!Equals(entry.Key.GetValue(null), entry.Value)) entry.Key.SetValue(null, entry.Value);
            }
            finally
            {
                try
                {
                    foreach (var value in objects.AsEnumerable().Reverse())
                        if (value != null && !EditorUtility.IsPersistent(value)) Object.DestroyImmediate(value);
                    objects.Clear();
                    if (!string.IsNullOrEmpty(rootGuid))
                    {
                        Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(rootGuid), "Delete only the GUID-owned fixture folder.");
                        Assert.IsTrue(AssetDatabase.DeleteAsset(root));
                    }
                }
                finally
                {
                    Selection.objects = originalSelection;
                    Selection.activeObject = originalActiveSelection;
                    if (originalScene.IsValid() && originalScene.isLoaded) SceneManager.SetActiveScene(originalScene);
                    if (ownedScene.IsValid() && ownedScene.isLoaded) EditorSceneManager.CloseScene(ownedScene, true);
                    captured = false;
                }
            }
        }

        private static JObject Send(string action, JObject data)
        {
            data["action"] = action;
            return JObject.FromObject(ManageGraphics.HandleCommand(data));
        }

        private static JToken Snapshot() => Send("skybox_get", new JObject())["data"];

        private void RejectUnchanged(string action, JObject data, bool logsError)
        {
            var before = Snapshot();
            var native = settings.Keys.ToDictionary(p => p, p => p.GetValue(null));
            if (logsError) LogAssert.Expect(LogType.Error, new Regex("\\[ManageGraphics\\] Action '" + action + "' failed:"));
            var response = Send(action, data);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()), response.ToString());
            foreach (var entry in native) Assert.AreEqual(entry.Value, entry.Key.GetValue(null), entry.Key.Name);
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("color")]
        [TestCase("ground_color")]
        public void MalformedLateAmbientColorDoesNotChangeEarlierSettings(string key)
        {
            var data = new JObject
            {
                ["ambient_mode"] = "Flat", ["color"] = new JArray(0, 0, 0),
                ["equator_color"] = new JArray(1, 1, 1), ["intensity"] = 0
            };
            data[key] = new JArray(0, "bad", 0);
            RejectUnchanged("skybox_set_ambient", data, true);
        }

        [Test]
        public void MalformedFogColorDoesNotChangeEnabledOrMode()
        {
            RejectUnchanged("skybox_set_fog", new JObject
            {
                ["fog_enabled"] = !RenderSettings.fog,
                ["fog_mode"] = RenderSettings.fogMode == FogMode.Linear ? "Exponential" : "Linear",
                ["fog_color"] = new JArray(0, "bad", 0)
            }, true);
        }

        [Test]
        public void MissingCubemapDoesNotChangeReflectionSettings()
        {
            string path = "Assets/MissingEnvironmentCubemap_" + Guid.NewGuid().ToString("N") + ".cubemap";
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            RejectUnchanged("skybox_set_reflection", new JObject
            {
                ["intensity"] = RenderSettings.reflectionIntensity + 1,
                ["bounces"] = RenderSettings.reflectionBounces + 1,
                ["reflection_mode"] = "Custom", ["path"] = path
            }, false);
        }

        [TestCase("skybox_set_ambient", "ambient_mode")]
        [TestCase("skybox_set_fog", "fog_mode")]
        [TestCase("skybox_set_reflection", "reflection_mode")]
        public void UndefinedModeDoesNotWriteSettings(string action, string key)
        {
            RejectUnchanged(action, new JObject { [key] = "99", ["intensity"] = 0, ["enabled"] = false }, false);
        }

        [Test]
        public void ExistingNonCubeTextureDoesNotChangeReflectionSettings()
        {
            string folder = "__McpEnvironmentSettingsIntegrity_" + Guid.NewGuid().ToString("N");
            root = "Assets/" + folder;
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(root));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, folder)));
            rootGuid = AssetDatabase.CreateFolder("Assets", folder);
            Assert.IsNotEmpty(rootGuid);
            Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(rootGuid));
            var texture = new Texture2D(1, 1);
            objects.Add(texture);
            string path = root + "/NonCube.asset";
            AssetDatabase.CreateAsset(texture, path);
            Assert.IsTrue(EditorUtility.IsPersistent(texture));
            Assert.AreEqual(TextureDimension.Tex2D, texture.dimension);
            Assert.AreSame(texture, AssetDatabase.LoadAssetAtPath<Texture>(path));
            RejectUnchanged("skybox_set_reflection", new JObject
            {
                ["intensity"] = RenderSettings.reflectionIntensity + 1,
                ["bounces"] = RenderSettings.reflectionBounces + 1,
                ["reflection_mode"] = "Custom", ["path"] = path
            }, false);
        }

        private Material OwnedMaterial()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null) Assert.Ignore("No shader-capable graphics device.");
            string name = "Hidden/McpEnvironmentSettingsIntegrity_" + Guid.NewGuid().ToString("N");
            var shader = ShaderUtil.CreateShaderAsset("Shader \"" + name + "\" { Properties { _Count (\"Count\", Integer) = 17 } SubShader { Pass {} } }", false);
            objects.Add(shader);
            Assert.IsNotNull(shader);
            Assert.IsFalse(EditorUtility.IsPersistent(shader));
            Assert.AreEqual(ShaderPropertyType.Int, shader.GetPropertyType(shader.FindPropertyIndex("_Count")));
            var material = new Material(shader);
            objects.Add(material);
            material.SetInteger("_Count", 17);
            return material;
        }

        [TestCase(0)]
        [TestCase(-2)]
        [TestCase(16777217)]
        public void IntegerSetterKeepsTrueIntegerStorage(int requested)
        {
            var material = OwnedMaterial();
            var setter = typeof(SkyboxOps).GetMethod("SetMaterialProperty", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(setter);
            Assert.IsTrue((bool)setter.Invoke(null, new object[] { material, "_Count", new JValue(requested) }));
            Assert.AreEqual(requested, material.GetInteger("_Count"));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void EnvironmentQueryReadsTrueIntegerProperty()
        {
            var material = OwnedMaterial();
            material.SetInteger("_Count", 16777217);
            RenderSettings.skybox = material;
            var response = Send("skybox_get", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var property = response["data"]["skybox"]["properties"].Single(p => p.Value<string>("name") == "_Count");
            Assert.AreEqual(16777217, property.Value<int>("value"));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void NullAndShortAmbientColorsRemainNoOps()
        {
            var before = Snapshot();
            var response = Send("skybox_set_ambient", new JObject
            {
                ["color"] = JValue.CreateNull(), ["equator_color"] = new JArray(0, 0), ["ground_color"] = new JArray()
            });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
        }

        [Test]
        public void MissingSunTargetDoesNotChangeSun()
        {
            RejectUnchanged("skybox_set_sun", new JObject { ["target"] = "MissingSun_" + Guid.NewGuid().ToString("N") }, false);
        }
    }
}
