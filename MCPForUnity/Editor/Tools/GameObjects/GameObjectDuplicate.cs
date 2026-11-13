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
        internal static Action<string, GameObject> AfterMutationForTests;

        internal static object Handle(JObject @params, JToken targetToken, string searchMethod)
        {
            GameObject sourceGo = ManageGameObjectCommon.FindMutationTarget(targetToken, searchMethod);
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
                    return new ErrorResponse($"Parent GameObject '{parentToken}' not found.");
            }

            Transform duplicateParent = parentToken == null ? sourceGo.transform.parent : newParent?.transform;
            string originalName = sourceGo.name;
            int originalId = sourceGo.GetInstanceIDCompat();
            bool placementRequested = position.HasValue || offset.HasValue;
            Vector3 requestedPosition = position ?? (sourceGo.transform.position + (offset ?? Vector3.zero));
            string requestedName = !string.IsNullOrEmpty(newName) ? newName : sourceGo.name.Replace("(Clone)", "").Trim() + "_Copy";
            UnityEngine.Object[] previousSelection = Selection.objects;
            UnityEngine.Object previousActive = Selection.activeObject;
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            string undoName = $"Duplicate {originalName}";
            Undo.SetCurrentGroupName(undoName);
            GameObject duplicatedGo = null;
            int duplicateId = 0;
            bool completed = false;
            try
            {
                // Editor duplication preserves the instance's prefab connections and overrides.
                duplicatedGo = DuplicateInEditor(sourceGo);
                if (duplicatedGo == null)
                    throw new InvalidOperationException($"Could not duplicate GameObject '{originalName}'.");
                duplicateId = duplicatedGo.GetInstanceIDCompat();
                Undo.RegisterCreatedObjectUndo(duplicatedGo, undoName);
                AfterMutationForTests?.Invoke("after_create", duplicatedGo);
                if (duplicatedGo.transform.parent != duplicateParent)
                {
                    Undo.SetTransformParent(duplicatedGo.transform, duplicateParent, undoName);
                }
                AfterMutationForTests?.Invoke("after_parent", duplicatedGo);
                Undo.RecordObject(duplicatedGo, undoName);
                Undo.RecordObject(duplicatedGo.transform, undoName);
                duplicatedGo.name = requestedName;
                if (placementRequested)
                    duplicatedGo.transform.position = requestedPosition;

                if (PrefabUtility.IsPartOfPrefabInstance(duplicatedGo))
                {
                    PrefabUtility.RecordPrefabInstancePropertyModifications(duplicatedGo);
                    PrefabUtility.RecordPrefabInstancePropertyModifications(duplicatedGo.transform);
                }
                if (
                    duplicatedGo.transform.parent != duplicateParent
                    || (placementRequested && Vector3.Distance(duplicatedGo.transform.position, requestedPosition) > 0.0001f)
                )
                    throw new InvalidOperationException("Unity did not retain the requested duplicate parent or position.");

                EditorUtility.SetDirty(duplicatedGo);
                EditorSceneManager.MarkSceneDirty(duplicatedGo.scene);
                object response = new SuccessResponse(
                    $"Duplicated '{originalName}' as '{duplicatedGo.name}'.",
                    new
                    {
                        originalName,
                        originalId,
                        duplicatedObject = Helpers.GameObjectSerializer.GetGameObjectData(duplicatedGo),
                    }
                );
                Undo.FlushUndoRecordObjects();
                Undo.CollapseUndoOperations(undoGroup);
                Selection.activeGameObject = duplicatedGo;
                completed = true;
                return response;
            }
            catch (Exception exception)
            {
                string recoveryError = null;
                try
                {
                    Undo.RevertAllDownToGroup(undoGroup);
                }
                catch (Exception recovery)
                {
                    recoveryError = recovery.GetBaseException().Message;
                }
                bool survived = duplicatedGo != null;
                return new ErrorResponse(
                    $"Could not duplicate GameObject '{originalName}': {exception.GetBaseException().Message}",
                    new
                    {
                        originalId,
                        recovery = survived ? "incomplete" : "rolled_back",
                        survivingInstanceID = survived ? (int?)duplicateId : null,
                        recoveryError,
                    }
                );
            }
            finally
            {
                try
                {
                    if (!completed)
                    {
                        Selection.objects = previousSelection;
                        Selection.activeObject = previousActive;
                    }
                }
                finally
                {
                    Undo.IncrementCurrentGroup();
                }
            }
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
