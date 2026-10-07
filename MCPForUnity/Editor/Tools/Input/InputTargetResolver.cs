using System;
using System.Collections.Generic;
using System.Globalization;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Exact scene-only targeting. Paths never fall back to fuzzy names.</summary>
    public static class InputTargetResolver
    {
        public static GameObject Resolve(JToken target)
        {
            if (target == null)
                throw new ArgumentException("ui_click requires target: integer instance ID or exact root hierarchy path.");
            if (target.Type == JTokenType.Integer)
            {
                if (!int.TryParse(target.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int id))
                    throw new ArgumentException("target instance ID must fit a signed 32-bit integer.");
                return RequireSceneObject(GameObjectLookup.FindById(id));
            }
            if (target.Type != JTokenType.String || string.IsNullOrWhiteSpace(target.Value<string>()))
                throw new ArgumentException("target must be an integer instance ID or exact root hierarchy path.");
            string path = target.Value<string>().TrimStart('/');
            string[] segments = path.Split('/');
            if (path.Length > 4096 || segments.Length > 128)
                throw new ArgumentException("target path exceeds 4096 characters or 128 hierarchy levels.");
            if (Array.Exists(segments, string.IsNullOrEmpty))
                throw new ArgumentException("target path contains an empty segment.");
            GameObject match = null;
            // Resources includes DontDestroyOnLoad objects; reject assets and preview scenes before matching.
            foreach (GameObject root in UnityEngine.Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (root.transform.parent != null || !IsSceneObject(root) || root.name != segments[0])
                    continue;
                foreach (GameObject candidate in MatchChildren(root.transform, segments, 1))
                {
                    if (match != null)
                        throw new ArgumentException("target path is ambiguous. Use an integer instance ID.");
                    match = candidate;
                }
            }
            return RequireSceneObject(match);
        }

        private static IEnumerable<GameObject> MatchChildren(Transform parent, string[] segments, int index)
        {
            if (index == segments.Length)
            {
                yield return parent.gameObject;
                yield break;
            }
            for (int i = 0; i < parent.childCount; i++)
            {
                Transform child = parent.GetChild(i);
                if (child.name != segments[index])
                    continue;
                foreach (GameObject match in MatchChildren(child, segments, index + 1))
                    yield return match;
            }
        }

        private static bool IsSceneObject(GameObject target) =>
            target != null
            && !EditorUtility.IsPersistent(target)
            && target.scene.IsValid()
            && target.scene.isLoaded
            && !EditorSceneManager.IsPreviewScene(target.scene);

        private static GameObject RequireSceneObject(GameObject target)
        {
            if (!IsSceneObject(target))
                throw new ArgumentException("target must identify a loaded scene GameObject; assets and preview scenes are unsupported.");
            if (!target.activeInHierarchy)
                throw new ArgumentException("target must be active in the scene.");
            return target;
        }
    }
}
