using System;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class UIToolkitScenarioAdmissionTests
    {
        private static PlayScenarioDefinition Definition(string backend = "uitoolkit", string mode = "direct")
        {
            var step = new JObject
            {
                ["name"] = "activate",
                ["action"] = "click_ui",
                ["target"] = "OwnedDocument",
                ["click_mode"] = mode,
            };
            if (backend != null)
            {
                step["ui_backend"] = backend;
                step["element_name"] = "Activate";
            }
            const string scenePath = "Assets/Tests/EditMode/Tools/UIEditingEmptyScene.unity";
            Assert.IsNotNull(
                AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath),
                "The existing saved test scene is required for read-only preflight; it is never loaded or changed."
            );
            return new PlayScenarioDefinition
            {
                Name = "toolkit-admission",
                Steps =
                {
                    new PlayScenarioStep
                    {
                        Name = "initial-scene",
                        Action = "load_scene",
                        Scene = scenePath,
                    },
                    step.ToObject<PlayScenarioStep>(),
                },
            };
        }

        [Test]
        public void ToolkitPreflightDefersWithoutUguiAndPreservesEditorAndDefinitionState()
        {
            PlayScenarioDefinition definition = Definition();
            string before = JObject.FromObject(definition).ToString();
            var scene = SceneManager.GetActiveScene();
            int sceneCount = SceneManager.sceneCount;
            bool dirty = scene.isDirty;
            bool playing = EditorApplication.isPlaying;
            var selected = Selection.activeObject;
            var prior = ManageInput.UguiBackend;
            try
            {
                ManageInput.UguiBackend = null;
                JObject result = UnityPlayScenarioHost.Preflight(definition);
                Assert.IsTrue(result["data"].Value<bool>("valid"), result.ToString());
                Assert.AreEqual("deferred", result["data"]["checks"].Single(c => c.Value<string>("name") == "activate").Value<string>("status"));
                Assert.AreEqual(before, JObject.FromObject(definition).ToString());
                Assert.AreEqual(scene, SceneManager.GetActiveScene());
                Assert.AreEqual(sceneCount, SceneManager.sceneCount);
                Assert.AreEqual(dirty, scene.isDirty);
                Assert.AreEqual(playing, EditorApplication.isPlaying);
                Assert.AreSame(selected, Selection.activeObject);
            }
            finally
            {
                ManageInput.UguiBackend = prior;
            }
        }

        [Test]
        public void PlayerCapabilitiesAdmitExplicitToolkitWithoutUgui()
        {
            Assert.DoesNotThrow(() => PlayScenarioPlayerCapabilities.Validate(Definition(), false));
        }

        [Test]
        public void DefaultUguiStillRequiresItsBackend()
        {
            var prior = ManageInput.UguiBackend;
            try
            {
                ManageInput.UguiBackend = null;
                JObject result = UnityPlayScenarioHost.Preflight(Definition(null));
                Assert.IsFalse(result["data"].Value<bool>("valid"));
                StringAssert.Contains("optional uGUI input backend", result.ToString());
                Assert.Throws<PlayScenarioException>(() => PlayScenarioPlayerCapabilities.Validate(Definition(null), false));
            }
            finally
            {
                ManageInput.UguiBackend = prior;
            }
        }

        [Test]
        public void PlayerCapabilitiesRefuseToolkitRaycastBeforeAnySceneWork()
        {
            var error = Assert.Throws<PlayScenarioException>(() => PlayScenarioPlayerCapabilities.Validate(Definition(mode: "raycast"), true));
            StringAssert.Contains("UI Toolkit", error.Message);
        }
    }
}
