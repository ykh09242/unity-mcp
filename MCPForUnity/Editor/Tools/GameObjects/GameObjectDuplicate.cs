#nullable disable
using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectDuplicate
    {
        internal static object Handle(JObject @params, JToken targetToken, string searchMethod)
        {
            GameObject sourceGo = ManageGameObjectCommon.FindObjectInternal(targetToken, searchMethod);
            if (sourceGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            JToken nameToken = @params["new_name"];
            // Default Command JSON parsing represents ISO string names as Date tokens.
            if (nameToken != null && nameToken.Type != JTokenType.Null && nameToken.Type != JTokenType.String && nameToken.Type != JTokenType.Date)
                return new ErrorResponse("'new_name' must be a string.");
            string newName = nameToken?.ToString();
            Vector3? position;
            Vector3? offset;
            try
            {
                position = ManageGameObjectCommon.ReadOptionalVector3(@params, "position");
                offset = ManageGameObjectCommon.ReadOptionalVector3(@params, "offset");
            }
            catch (ArgumentException e)
            {
                return new ErrorResponse(e.Message);
            }
            JToken parentToken = @params["parent"];
            GameObject newParent = null;
            if (
                parentToken != null
                && parentToken.Type != JTokenType.Null
                && !(parentToken.Type == JTokenType.String && string.IsNullOrEmpty(parentToken.ToString()))
            )
            {
                newParent = ManageGameObjectCommon.FindObjectInternal(parentToken, "by_id_or_name_or_path");
                if (newParent == null)
                    McpLog.Warn($"[ManageGameObject.Duplicate] Parent '{parentToken}' not found. Object will remain at root level.");
            }

            Transform duplicateParent = parentToken == null ? sourceGo.transform.parent : newParent?.transform;
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            string undoName = $"Duplicate {sourceGo.name}";
            Undo.SetCurrentGroupName(undoName);
            // Editor duplication preserves the instance's prefab connections and overrides.
            GameObject duplicatedGo = DuplicateInEditor(sourceGo);
            if (duplicatedGo == null)
                return new ErrorResponse($"Could not duplicate GameObject '{sourceGo.name}'.");
            Undo.RegisterCreatedObjectUndo(duplicatedGo, undoName);
            if (duplicatedGo.transform.parent != duplicateParent)
            {
                Undo.SetTransformParent(duplicatedGo.transform, duplicateParent, undoName);
            }
            Undo.RecordObject(duplicatedGo, undoName);
            Undo.RecordObject(duplicatedGo.transform, undoName);

            if (!string.IsNullOrEmpty(newName))
            {
                duplicatedGo.name = newName;
            }
            else
            {
                duplicatedGo.name = sourceGo.name.Replace("(Clone)", "").Trim() + "_Copy";
            }

            if (position.HasValue)
            {
                duplicatedGo.transform.position = position.Value;
            }
            else if (offset.HasValue)
            {
                duplicatedGo.transform.position = sourceGo.transform.position + offset.Value;
            }

            if (PrefabUtility.IsPartOfPrefabInstance(duplicatedGo))
            {
                PrefabUtility.RecordPrefabInstancePropertyModifications(duplicatedGo);
                PrefabUtility.RecordPrefabInstancePropertyModifications(duplicatedGo.transform);
            }
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(undoGroup);

            EditorUtility.SetDirty(duplicatedGo);
            EditorSceneManager.MarkSceneDirty(duplicatedGo.scene);

            Selection.activeGameObject = duplicatedGo;

            return new SuccessResponse(
                $"Duplicated '{sourceGo.name}' as '{duplicatedGo.name}'.",
                new
                {
                    originalName = sourceGo.name,
                    originalId = sourceGo.GetInstanceIDCompat(),
                    duplicatedObject = Helpers.GameObjectSerializer.GetGameObjectData(duplicatedGo),
                }
            );
        }

        private static GameObject DuplicateInEditor(GameObject source)
        {
#if UNITY_2023_2_OR_NEWER
            return GameObjectUtility.DuplicateGameObject(source);
#else
            // The public object-based API was added after the supported 2021.3 floor.
            // Use the same native duplication entry point as the legacy Hierarchy.
            UnityEngine.Object[] selection = Selection.objects;
            UnityEngine.Object active = Selection.activeObject;
            try
            {
                Selection.objects = new UnityEngine.Object[] { source };
                Unsupported.DuplicateGameObjectsUsingPasteboard();
                GameObject duplicate = Selection.activeGameObject;
                return duplicate != source ? duplicate : null;
            }
            finally
            {
                Selection.objects = selection;
                Selection.activeObject = active;
            }
#endif
        }
    }
}
