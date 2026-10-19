#nullable disable
using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    // Parse the complete transform policy before creation, rename, parenting or Undo recording.
    internal sealed class GameObjectTransformOptions
    {
        internal readonly bool World;
        internal readonly bool ParentSpecified;
        internal readonly GameObject Parent;
        private readonly bool worldPositionStays;
        private readonly int? siblingIndex;

        private GameObjectTransformOptions(bool world, bool parentSpecified, GameObject parent, bool preserveWorld, int? sibling)
        {
            World = world;
            ParentSpecified = parentSpecified;
            Parent = parent;
            worldPositionStays = preserveWorld;
            siblingIndex = sibling;
        }

        internal static bool ReadBoolean(JObject parameters, string field, bool fallback)
        {
            JToken token = parameters[field];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type == JTokenType.Boolean)
                return token.Value<bool>();
            if (token.Type == JTokenType.String)
            {
                if (string.Equals(token.Value<string>(), "true", StringComparison.OrdinalIgnoreCase))
                    return true;
                if (string.Equals(token.Value<string>(), "false", StringComparison.OrdinalIgnoreCase))
                    return false;
            }
            throw new ArgumentException($"'{field}' must be true or false.");
        }

        internal static bool ReadWorld(JObject parameters)
        {
            JToken token = parameters["space"];
            if (token == null || token.Type == JTokenType.Null || token.Type == JTokenType.String && token.Value<string>() == "local")
                return false;
            if (token.Type == JTokenType.String && token.Value<string>() == "world")
                return true;
            throw new ArgumentException("'space' must be 'local' or 'world'.");
        }

        internal static GameObjectTransformOptions Read(JObject parameters, GameObject target = null)
        {
            bool world = ReadWorld(parameters);
            if (world && parameters["scale"] != null && parameters["scale"].Type != JTokenType.Null)
                throw new ArgumentException("World scale is unsupported; scale is local only.");
            bool preserveWorld = ReadBoolean(parameters, "world_position_stays", true);
            bool detach = ReadBoolean(parameters, "detach", false);
            JToken parentToken = parameters["parent"];
            bool hasParent = parentToken != null && parentToken.Type != JTokenType.Null && parentToken.ToString() != "";
            if (detach && hasParent)
                throw new ArgumentException("'detach' conflicts with a nonempty parent.");
            if (detach && target == null)
                throw new ArgumentException("'detach' is only supported by modify.");
            GameObject parent = hasParent ? ManageGameObjectCommon.FindObjectInternal(parentToken, "by_id_or_name_or_path") : null;
            if (hasParent && parent == null)
                throw new ArgumentException($"Parent ('{parentToken}') not found.");
            if (target != null && parent != null && parent.transform.IsChildOf(target.transform))
                throw new ArgumentException("Parent would create a hierarchy loop.");

            bool parentSpecified = parentToken != null || detach;
            Transform destination = parentSpecified ? parent?.transform : target?.transform.parent;
            int? sibling = null;
            JToken siblingToken = parameters["sibling_index"];
            if (siblingToken != null && siblingToken.Type != JTokenType.Null)
            {
                if (siblingToken.Type != JTokenType.Integer || !int.TryParse(siblingToken.ToString(), out int index) || index < 0)
                    throw new ArgumentException("'sibling_index' must be a nonnegative integer.");
                Scene scene =
                    destination != null ? destination.gameObject.scene
                    : target != null ? target.scene
                    : SceneManager.GetActiveScene();
                int count = destination != null ? destination.childCount : scene.rootCount;
                bool alreadySibling = target != null && target.transform.parent == destination && target.scene == scene;
                int maximum = count - (alreadySibling ? 1 : 0);
                if (index > maximum)
                    throw new ArgumentException($"'sibling_index' must be between 0 and {maximum} for the destination.");
                sibling = index;
            }
            return new GameObjectTransformOptions(world, parentSpecified, parent, preserveWorld, sibling);
        }

        internal bool Apply(Transform transform, Vector3? position, Vector3? rotation, Vector3? scale)
        {
            bool changed = false;
            if (ParentSpecified && transform.parent != Parent?.transform)
            {
                Undo.SetTransformParent(transform, Parent?.transform, worldPositionStays, "Reparent GameObject");
                changed = true;
            }
            if (siblingIndex.HasValue && transform.GetSiblingIndex() != siblingIndex.Value)
            {
#if UNITY_2023_1_OR_NEWER
                Undo.SetSiblingIndex(transform, siblingIndex.Value, "Order GameObject");
#else
                // Older editors expose parenting/order Undo separately, including scene roots.
                Undo.SetTransformParent(transform, transform.parent, "Order GameObject");
                if (transform.parent != null)
                    Undo.RegisterChildrenOrderUndo(transform.parent, "Order GameObject");
                transform.SetSiblingIndex(siblingIndex.Value);
#endif
                changed = true;
            }
            if (position.HasValue && (World ? transform.position : transform.localPosition) != position.Value)
            {
                if (World)
                    transform.position = position.Value;
                else
                    transform.localPosition = position.Value;
                changed = true;
            }
            if (rotation.HasValue)
            {
                Quaternion desired = Quaternion.Euler(rotation.Value);
                if ((World ? transform.rotation : transform.localRotation) != desired)
                {
                    if (World)
                        transform.rotation = desired;
                    else
                        transform.localRotation = desired;
                    changed = true;
                }
            }
            if (scale.HasValue && transform.localScale != scale.Value)
            {
                transform.localScale = scale.Value;
                changed = true;
            }
            if (changed && PrefabUtility.IsPartOfPrefabInstance(transform))
                PrefabUtility.RecordPrefabInstancePropertyModifications(transform);
            return changed;
        }
    }
}
