using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Loaded normal and persistent scene roots, excluding assets and preview scenes.</summary>
    public static class SceneObjectRoots
    {
        private static readonly Func<Scene> PersistentScene = BindPersistentScene();

        private static Func<Scene> BindPersistentScene()
        {
            // Unity exposes this scene only through an Editor binding. Cache the delegate,
            // with a compatibility fallback for versions that do not provide the binding.
            var method = typeof(EditorSceneManager).GetMethod("GetDontDestroyOnLoadScene", BindingFlags.Static | BindingFlags.NonPublic);
            return method == null ? null : (Func<Scene>)Delegate.CreateDelegate(typeof(Func<Scene>), method);
        }

        public static void CopyTo(List<GameObject> roots, List<GameObject> scratch)
        {
            roots.Clear();
            for (int i = 0; i < SceneManager.sceneCount; i++)
                Append(SceneManager.GetSceneAt(i), roots, scratch);
            if (Application.isPlaying)
            {
                Scene persistent = default;
                if (PersistentScene != null)
                    persistent = PersistentScene();
                else
                    foreach (GameObject candidate in UnityEngine.Resources.FindObjectsOfTypeAll<GameObject>())
                        if (candidate != null && candidate.scene.name == "DontDestroyOnLoad")
                        {
                            persistent = candidate.scene;
                            break;
                        }
                bool alreadyIncluded = false;
                for (int i = 0; i < SceneManager.sceneCount; i++)
                    if (SceneManager.GetSceneAt(i) == persistent)
                        alreadyIncluded = true;
                if (!alreadyIncluded)
                    Append(persistent, roots, scratch);
            }
            scratch.Clear();
        }

        private static void Append(Scene scene, List<GameObject> roots, List<GameObject> scratch)
        {
            if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene))
                return;
            if (scratch.Capacity <= scene.rootCount)
                scratch.Capacity = scene.rootCount + 1;
            scene.GetRootGameObjects(scratch);
            roots.AddRange(scratch);
        }
    }
}
