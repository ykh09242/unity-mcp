using System;
using UnityEngine.SceneManagement;
#if UNITY_EDITOR
using UnityEditor.SceneManagement;
#endif

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Exact bounded scene lookup and one-step identity binding; no persisted native handles.</summary>
    public sealed class PlayScenarioSceneScope
    {
        public const int SceneLimit = 256;
        private Scene bound;
        private bool hasBound;
        private string path;

        public static bool Normal(Scene scene)
        {
            if (!scene.IsValid() || string.IsNullOrEmpty(scene.path))
                return false;
#if UNITY_EDITOR
            if (EditorSceneManager.IsPreviewScene(scene))
                return false;
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.scene == scene)
                return false;
#endif
            return true;
        }

        public static Scene Find(string scenePath, bool loadedOnly = true)
        {
            if (SceneManager.sceneCount > SceneLimit)
                throw new InvalidOperationException("Scene inspection exceeds the 256-scene limit.");
            Scene match = default;
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene candidate = SceneManager.GetSceneAt(index);
                if (!Normal(candidate) || !string.Equals(candidate.path, scenePath, StringComparison.Ordinal))
                    continue;
                if (match.IsValid())
                    throw new InvalidOperationException("scene_ambiguous: more than one loaded/loading scene has the exact path.");
                match = candidate;
            }
            return loadedOnly && (!match.IsValid() || !match.isLoaded) ? default : match;
        }

        public Scene Resolve(string scenePath, bool firstPoll)
        {
            if (firstPoll || !string.Equals(path, scenePath, StringComparison.Ordinal))
            {
                bound = default;
                hasBound = false;
                path = scenePath;
            }
            Scene current = Find(scenePath);
            if (hasBound && (!current.IsValid() || !bound.IsValid() || !bound.isLoaded || current != bound))
                throw new InvalidOperationException("scene_scope_unavailable: the selected scene was unloaded or replaced during this step.");
            if (current.IsValid() && !hasBound)
            {
                bound = current;
                hasBound = true;
            }
            return current;
        }

        public void Release()
        {
            bound = default;
            hasBound = false;
            path = null;
        }
    }
}
