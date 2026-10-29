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
        private static Scene restoredRunnerScene;
        private static object restoredRunnerJob;
        private Scene previousScene;
        private Scene ownedScene;
        private string scenePrefix;
        private bool restoreEmptyScene;
        private bool restoreRunnerDefaultScene;
        private object runnerJob;

        public void PrepareRunnerBootstrap()
        {
            restoreRunnerDefaultScene = false;
            runnerJob = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                return;
            Scene initial = SceneManager.GetActiveScene();
            if (
                SceneManager.sceneCount == 1
                && initial.IsValid()
                && initial.rootCount != 0
                && string.IsNullOrEmpty(initial.path)
                && !initial.isDirty
                && TryGetRunnerOwner(initial, out runnerJob)
            )
            {
                // Only the active job's bootstrap or our exact restoration for that same job.
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                restoredRunnerScene = default;
                restoredRunnerJob = null;
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
            restoredRunnerScene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            restoredRunnerJob = runnerJob;
            restoreRunnerDefaultScene = false;
            runnerJob = null;
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

        public void ClearDirtiness()
        {
            Assert.IsTrue(ownedScene.IsValid() && ownedScene.isLoaded, "Only the owned fixture scene may be marked clean.");
            StringAssert.StartsWith(scenePrefix, ownedScene.name);
            MethodInfo clear = typeof(EditorSceneManager).GetMethod("ClearSceneDirtiness", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(clear, "The editor must expose the scene-dirtiness test seam.");
            clear.Invoke(null, new object[] { ownedScene });
            Assert.IsFalse(ownedScene.isDirty, "The rejection test requires a clean owned scene.");
        }

        public Scene ReopenSavedScene()
        {
            Assert.IsTrue(ownedScene.IsValid() && ownedScene.isLoaded && !ownedScene.isDirty);
            StringAssert.StartsWith(scenePrefix, ownedScene.name);
            string path = ownedScene.path;
            Assert.IsFalse(string.IsNullOrEmpty(path));
            Scene placeholder = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, false));
                ownedScene = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
                return ownedScene;
            }
            finally
            {
                if (placeholder.IsValid() && placeholder.isLoaded)
                    EditorSceneManager.CloseScene(placeholder, true);
            }
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

        private static bool TryGetRunnerOwner(Scene candidate, out object owner)
        {
            owner = null;
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
                    if (
                        runningField?.GetValue(run) is bool running
                        && running
                        && (
                            sceneField?.GetValue(run) is Scene bootstrap && bootstrap == candidate
                            || ReferenceEquals(restoredRunnerJob, run) && restoredRunnerScene == candidate
                        )
                    )
                    {
                        owner = run;
                        return true;
                    }
                }
            }
            return false;
        }
    }
}
