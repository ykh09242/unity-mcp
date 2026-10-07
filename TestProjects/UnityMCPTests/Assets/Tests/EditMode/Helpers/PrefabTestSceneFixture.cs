using System;
using System.Collections;
using System.Reflection;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Helpers
{
    internal sealed class PrefabTestSceneFixture
    {
        private Scene previousScene;
        private Scene ownedScene;
        private string scenePrefix;
        private bool restoreEmptyScene;
        private bool restoreRunnerDefaultScene;

        public void PrepareRunnerBootstrap()
        {
            restoreRunnerDefaultScene = false;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                return;
            Scene initial = SceneManager.GetActiveScene();
            if (
                SceneManager.sceneCount == 1
                && initial.IsValid()
                && initial.rootCount != 0
                && string.IsNullOrEmpty(initial.path)
                && !initial.isDirty
                && IsRunnerBootstrapScene(initial)
            )
            {
                // Normalize only the pinned runner's exact scene, once per fixture.
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                restoreRunnerDefaultScene = true;
            }
        }

        public void RestoreRunnerBootstrap()
        {
            if (!restoreRunnerDefaultScene)
                return;
            Scene current = SceneManager.GetActiveScene();
            Assert.IsTrue(
                SceneManager.sceneCount == 1 && current.IsValid() && string.IsNullOrEmpty(current.path) && !current.isDirty && current.rootCount == 0,
                "The fixture must finish with only its clean empty replacement scene before restoring the runner default."
            );
            EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            restoreRunnerDefaultScene = false;
        }

        public Scene Create(string prefix, string suffix)
        {
            ownedScene = default;
            previousScene = default;
            restoreEmptyScene = false;
            scenePrefix = prefix;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("Run prefab fixtures in the main stage; the current prefab stage is preserved.");
            previousScene = SceneManager.GetActiveScene();
            restoreEmptyScene =
                SceneManager.sceneCount == 1
                && previousScene.IsValid()
                && string.IsNullOrEmpty(previousScene.path)
                && !previousScene.isDirty
                && previousScene.rootCount == 0;
            if (!restoreEmptyScene)
            {
                for (int i = 0; i < SceneManager.sceneCount; i++)
                    if (string.IsNullOrEmpty(SceneManager.GetSceneAt(i).path))
                        Assert.Ignore("The fixture preserves existing unsaved scenes; save them before running prefab fixtures.");
            }
            // Replace only the sole clean empty scene; preserve saved scenes additively.
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, restoreEmptyScene ? NewSceneMode.Single : NewSceneMode.Additive);
            ownedScene.name = prefix + suffix;
            Assert.IsTrue(ownedScene.IsValid() && ownedScene.isLoaded, "The owned editor fixture scene must be loaded.");
            // NewScene(Single) already selects its scene; a redundant SetActiveScene can return false.
            if (SceneManager.GetActiveScene() != ownedScene)
                Assert.IsTrue(SceneManager.SetActiveScene(ownedScene), "The owned additive fixture scene must become active.");
            Assert.AreEqual(ownedScene, SceneManager.GetActiveScene(), "Prefab commands must resolve against the owned fixture scene.");
            return ownedScene;
        }

        public void Close()
        {
            if (previousScene.IsValid() && previousScene.isLoaded)
                SceneManager.SetActiveScene(previousScene);
            if (ownedScene.IsValid() && ownedScene.isLoaded)
            {
                StringAssert.StartsWith(scenePrefix, ownedScene.name);
                if (restoreEmptyScene && SceneManager.sceneCount == 1)
                    EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                else
                    Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true), "Only the owned fixture scene should close.");
            }
            ownedScene = default;
            previousScene = default;
            restoreEmptyScene = false;
        }

        private static bool IsRunnerBootstrapScene(Scene candidate)
        {
            Type holderType = typeof(UnityEditor.TestTools.TestRunner.Api.TestRunnerApi).Assembly.GetType(
                "UnityEditor.TestTools.TestRunner.TestRun.TestJobDataHolder"
            );
            FieldInfo runsField = holderType?.GetField("TestRuns");
            if (runsField == null)
                return false;
            foreach (Object holder in UnityEngine.Resources.FindObjectsOfTypeAll(holderType))
            {
                if (!(runsField.GetValue(holder) is IEnumerable runs))
                    continue;
                foreach (object run in runs)
                {
                    FieldInfo runningField = run.GetType().GetField("isRunning");
                    FieldInfo sceneField = run.GetType().GetField("InitTestScene");
                    if (runningField?.GetValue(run) is bool running && running && sceneField?.GetValue(run) is Scene bootstrap && bootstrap == candidate)
                        return true;
                }
            }
            return false;
        }
    }
}
