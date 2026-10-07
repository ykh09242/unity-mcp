using System;
using System.Collections;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class RuntimeMutationResponseTests
    {
        private static JObject ModifyActive(GameObject target, bool active) =>
            JObject.FromObject(
                ManageGameObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["setActive"] = active,
                    }
                )
            );

        private static JObject SetInteractable(GameObject target, bool interactable) =>
            JObject.FromObject(
                ManageComponents.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentType"] = "UnityEngine.UI.Button",
                        ["property"] = "interactable",
                        ["value"] = interactable,
                    }
                )
            );

        private static Component AddButton(GameObject go)
        {
            // Resolve the optional UI package without adding it as a test assembly dependency.
            var buttonType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Button");
            Assert.IsNotNull(buttonType, "The test project's uGUI package must be installed.");
            return go.AddComponent(buttonType);
        }

        private static bool IsInteractable(Component button) => (bool)button.GetType().GetProperty("interactable").GetValue(button);

        [UnityTest]
        public IEnumerator PlayMode_SetActive_ReturnsSuccessMatchingActualState()
        {
            yield return new EnterPlayMode();
            var go = new GameObject("RuntimeActive_" + Guid.NewGuid().ToString("N"));
            try
            {
                foreach (bool active in new[] { false, true })
                {
                    var result = ModifyActive(go, active);
                    Assert.AreEqual(active, go.activeSelf, "Check actual state before checking the response.");
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    Assert.AreEqual(active, result["data"].Value<bool>("activeSelf"));
                }
            }
            finally
            {
                Object.DestroyImmediate(go);
            }
        }

        [UnityTest]
        public IEnumerator PlayMode_ButtonInteractable_ReturnsSuccessMatchingActualState()
        {
            yield return new EnterPlayMode();
            var go = new GameObject("RuntimeButton_" + Guid.NewGuid().ToString("N"), typeof(RectTransform));
            try
            {
                var button = AddButton(go);
                foreach (bool interactable in new[] { false, true })
                {
                    var result = SetInteractable(go, interactable);
                    Assert.AreEqual(interactable, IsInteractable(button), "Check actual state before checking the response.");
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    Assert.AreEqual(go.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));
                }
            }
            finally
            {
                Object.DestroyImmediate(go);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EditMode_MutationMarksOwningSceneDirtyAndCanBeUndone(bool component)
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            string path = "Assets/__RuntimeMutation_" + Guid.NewGuid().ToString("N") + ".unity";
            var go = new GameObject("EditMutation", typeof(RectTransform));
            SceneManager.MoveGameObjectToScene(go, scene);
            Component button = null;
            try
            {
                button = component ? AddButton(go) : null;
                Assert.IsTrue(EditorSceneManager.SaveScene(scene, path));
                Assert.IsFalse(scene.isDirty);
                Undo.IncrementCurrentGroup();
                var result = component ? SetInteractable(go, false) : ModifyActive(go, false);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsFalse(component ? IsInteractable(button) : go.activeSelf);
                Assert.IsTrue(scene.isDirty, "Edit Mode mutations must still mark the owning scene for saving.");
                Undo.FlushUndoRecordObjects();
                Undo.PerformUndo();
                Assert.IsTrue(component ? IsInteractable(button) : go.activeSelf, "Undo must restore the prior value.");
            }
            finally
            {
                if (button != null)
                    Undo.ClearUndo(button);
                Undo.ClearUndo(go);
                if (go != null)
                    Object.DestroyImmediate(go);
                EditorSceneManager.CloseScene(scene, true);
                AssetDatabase.DeleteAsset(path);
            }
        }

        [UnityTearDown]
        public IEnumerator ExitPlayModeAfterTest()
        {
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }
    }
}
