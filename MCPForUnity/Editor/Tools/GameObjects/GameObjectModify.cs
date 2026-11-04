#nullable disable
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditorInternal;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectModify
    {
        internal static object Handle(JObject @params, JToken targetToken, string searchMethod)
        {
            // When setActive=true is specified, we need to search for inactive objects
            // otherwise we can't find an inactive object to activate it
            JObject findParams = null;
            if (@params["setActive"]?.ReadScalar<bool?>() == true)
            {
                findParams = new JObject { ["searchInactive"] = true };
            }

            GameObject targetGo = ManageGameObjectCommon.FindMutationTarget(targetToken, searchMethod, findParams);
            if (targetGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            // Resolve deterministic inputs before any rename, asset rename, or Undo recording.
            string layerName = @params["layer"]?.ToString();
            int layerId = string.IsNullOrEmpty(layerName) ? -1 : LayerMask.NameToLayer(layerName);
            if (!string.IsNullOrEmpty(layerName) && layerId == -1)
            {
                return new ErrorResponse($"Invalid layer specified: '{layerName}'. Use a valid layer name.");
            }

            Vector3? position;
            Vector3? rotation;
            Vector3? scale;
            GameObjectTransformOptions transformOptions;
            try
            {
                position = ManageGameObjectCommon.ReadOptionalVector3(@params, "position");
                rotation = ManageGameObjectCommon.ReadOptionalVector3(@params, "rotation");
                scale = ManageGameObjectCommon.ReadOptionalVector3(@params, "scale");
                transformOptions = GameObjectTransformOptions.Read(@params, targetGo);
                var plannedTypes = ManageGameObjectCommon.ValidateComponentParameters(@params, validateRemovals: true, componentSource: targetGo);
                var propertyError = GameObjectComponentHelpers.ValidateModifyPropertyTargets(targetGo, @params["componentProperties"] as JObject, plannedTypes);
                if (propertyError != null)
                    return propertyError;
            }
            catch (ArgumentException e)
            {
                return new ErrorResponse(e.Message);
            }

            string name = @params["name"]?.ToString() ?? @params["new_name"]?.ToString() ?? @params["newName"]?.ToString();
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            string assetPath = null;
            string newAssetPath = null;
            string assetGuid = null;
            if (!string.IsNullOrEmpty(name) && name != targetGo.name && stage != null && stage.prefabContentsRoot == targetGo)
            {
                assetPath = stage.assetPath;
                newAssetPath = AssetPathUtility.NormalizeSeparators(Path.Combine(Path.GetDirectoryName(assetPath), name + ".prefab"));
                assetGuid = AssetDatabase.AssetPathToGUID(assetPath);
                string existingGuid = AssetDatabase.AssetPathToGUID(newAssetPath);
                if (!string.IsNullOrEmpty(existingGuid) && existingGuid != assetGuid)
                    return new ErrorResponse($"Cannot rename prefab root to '{name}': a prefab already exists at '{newAssetPath}'.");
            }

            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Modify GameObject");
            string createdTag = null;
            bool renameAttempted = false;
            ErrorResponse failure;
            try
            {
                object result = Apply(@params, targetGo, name, layerId, transformOptions, position, rotation, scale, ref createdTag);
                failure = result as ErrorResponse;
                if (failure == null)
                {
                    Undo.FlushUndoRecordObjects();
                    Undo.CollapseUndoOperations(group);
                    // AssetDatabase moves are outside Undo. Publish the move only after every object edit succeeds.
                    if (assetPath != null && assetPath != newAssetPath)
                    {
                        renameAttempted = true;
                        string error = AssetDatabase.RenameAsset(assetPath, name);
                        if (!string.IsNullOrEmpty(error))
                            failure = new ErrorResponse($"Failed to rename prefab asset: {error}");
                    }
                    if (failure == null)
                    {
                        Undo.IncrementCurrentGroup();
                        return result;
                    }
                }
            }
            catch (Exception e)
            {
                failure = new ErrorResponse($"Failed to modify GameObject: {e.Message}");
            }

            var rollbackErrors = new List<string>();
            try
            {
                Undo.FlushUndoRecordObjects();
                Undo.RevertAllDownToGroup(group);
            }
            catch (Exception e)
            {
                rollbackErrors.Add("Undo: " + e.Message);
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
            try
            {
                if (createdTag != null && InternalEditorUtility.tags.Contains(createdTag))
                    InternalEditorUtility.RemoveTag(createdTag);
                if (createdTag != null && InternalEditorUtility.tags.Contains(createdTag))
                    rollbackErrors.Add($"The newly created tag '{createdTag}' still exists.");
            }
            catch (Exception e)
            {
                rollbackErrors.Add("Tag: " + e.Message);
            }
            if (renameAttempted)
            {
                try
                {
                    string currentPath = AssetDatabase.GUIDToAssetPath(assetGuid);
                    if (currentPath == newAssetPath)
                        AssetDatabase.MoveAsset(currentPath, assetPath);
                    if (AssetDatabase.GUIDToAssetPath(assetGuid) != assetPath)
                        rollbackErrors.Add($"The prefab is no longer at '{assetPath}'.");
                }
                catch (Exception e)
                {
                    rollbackErrors.Add("Prefab asset: " + e.Message);
                }
            }
            string rollback =
                rollbackErrors.Count == 0
                    ? "Reverted recorded Unity edits. Effects of callbacks or nonserialized setters may remain."
                    : "Rollback incomplete: " + string.Join(" ", rollbackErrors);
            return new ErrorResponse(failure.Error + " " + rollback, failure.Data);
        }

        private static object Apply(
            JObject @params,
            GameObject targetGo,
            string name,
            int layerId,
            GameObjectTransformOptions transformOptions,
            Vector3? position,
            Vector3? rotation,
            Vector3? scale,
            ref string createdTag
        )
        {
            Undo.RecordObject(targetGo.transform, "Modify GameObject Transform");
            Undo.RecordObject(targetGo, "Modify GameObject Properties");
            bool modified = false;
            if (!string.IsNullOrEmpty(name) && targetGo.name != name)
            {
                targetGo.name = name;
                RecordPrefabOverrides(targetGo);
                modified = true;
            }

            modified |= transformOptions.Apply(targetGo.transform, position, rotation, scale);

            bool? setActive = @params["setActive"]?.ReadScalar<bool?>();
            if (setActive.HasValue && targetGo.activeSelf != setActive.Value)
            {
                targetGo.SetActive(setActive.Value);
                RecordPrefabOverrides(targetGo);
                modified = true;
            }

            string tag = @params["tag"]?.ToString();
            if (tag != null && targetGo.tag != tag)
            {
                string tagToSet = string.IsNullOrEmpty(tag) ? "Untagged" : tag;

                if (tagToSet != "Untagged" && !InternalEditorUtility.tags.Contains(tagToSet))
                {
                    McpLog.Info($"[ManageGameObject] Tag '{tagToSet}' not found. Creating it.");
                    try
                    {
                        createdTag = tagToSet;
                        InternalEditorUtility.AddTag(tagToSet);
                    }
                    catch (Exception ex)
                    {
                        return new ErrorResponse($"Failed to create tag '{tagToSet}': {ex.Message}.");
                    }
                }

                try
                {
                    targetGo.tag = tagToSet;
                    RecordPrefabOverrides(targetGo);
                    modified = true;
                }
                catch (Exception ex)
                {
                    return new ErrorResponse($"Failed to set tag to '{tagToSet}': {ex.Message}.");
                }
            }

            if (layerId != -1 && targetGo.layer != layerId)
            {
                targetGo.layer = layerId;
                RecordPrefabOverrides(targetGo);
                modified = true;
            }

            bool? isStatic = @params["isStatic"]?.ReadScalar<bool?>();
            if (isStatic.HasValue)
            {
                var desiredFlags = isStatic.Value ? (StaticEditorFlags)~0 : 0;
                var currentFlags = GameObjectUtility.GetStaticEditorFlags(targetGo);

                if (currentFlags != desiredFlags)
                {
                    GameObjectUtility.SetStaticEditorFlags(targetGo, desiredFlags);
                    RecordPrefabOverrides(targetGo);
                    modified = true;
                }
            }

            if (@params["componentsToRemove"] is JArray componentsToRemoveArray)
            {
                foreach (var compToken in componentsToRemoveArray)
                {
                    string typeName = compToken.ToString();
                    if (!string.IsNullOrEmpty(typeName))
                    {
                        var removeResult = GameObjectComponentHelpers.RemoveComponentInternal(targetGo, typeName);
                        if (removeResult != null)
                            return removeResult;
                        modified = true;
                    }
                }
            }

            if (@params["componentsToAdd"] is JArray componentsToAddArrayModify)
            {
                foreach (var compToken in componentsToAddArrayModify)
                {
                    string typeName = null;
                    JObject properties = null;
                    if (compToken.Type == JTokenType.String)
                        typeName = compToken.ToString();
                    else if (compToken is JObject compObj)
                    {
                        typeName = compObj["typeName"]?.ToString();
                        properties = compObj["properties"] as JObject;
                    }

                    if (!string.IsNullOrEmpty(typeName))
                    {
                        var addResult = GameObjectComponentHelpers.AddComponentInternal(targetGo, typeName, properties);
                        if (addResult != null)
                            return addResult;
                        modified = true;
                    }
                }
            }

            var componentPropertiesError = GameObjectComponentHelpers.ApplyComponentProperties(
                targetGo,
                @params["componentProperties"] as JObject,
                out bool componentPropertiesModified
            );
            if (componentPropertiesError != null)
            {
                return componentPropertiesError;
            }
            if (componentPropertiesModified)
            {
                modified = true;
            }

            if (!modified)
            {
                return new SuccessResponse(
                    $"No modifications applied to GameObject '{targetGo.name}'.",
                    Helpers.GameObjectSerializer.GetGameObjectData(targetGo)
                );
            }

            EditorUtility.SetDirty(targetGo);

            // Runtime changes do not need scene-save bookkeeping, which is Edit Mode only.
            if (!EditorApplication.isPlaying)
                EditorSceneManager.MarkSceneDirty(targetGo.scene);

            return new SuccessResponse($"GameObject '{targetGo.name}' modified successfully.", Helpers.GameObjectSerializer.GetGameObjectData(targetGo));
        }

        private static void RecordPrefabOverrides(GameObject target)
        {
            if (PrefabUtility.IsPartOfPrefabInstance(target))
                PrefabUtility.RecordPrefabInstancePropertyModifications(target);
        }
    }
}
