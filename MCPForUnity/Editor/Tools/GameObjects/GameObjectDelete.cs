#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectDelete
    {
        internal static Action<GameObject> DestroyForTests;

        internal static object Handle(JToken targetToken, string searchMethod)
        {
            // Deletion resolves the same active-scene search scope as find, including inactive
            // descendants. Resolving before destruction also keeps bulk matching stable.
            var findParams = new JObject { ["searchInactive"] = true };
            List<GameObject> targets = ManageGameObjectCommon.FindObjectsInternal(targetToken, searchMethod, true, findParams);

            if (targets.Count == 0)
            {
                return new ErrorResponse($"Target GameObject(s) ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            // Snapshot identities before destroying a parent can invalidate matching descendants.
            var results = new JArray(
                targets.Select(target => new JObject
                {
                    ["name"] = target.name,
                    ["instanceID"] = target.GetInstanceIDCompat(),
                    ["status"] = "skipped",
                    ["message"] = "Not attempted.",
                })
            );
            List<object> deletedObjects = new List<object>();
            string deletedName = null;
            string failure = null;
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Delete GameObject");
            try
            {
                for (int index = 0; index < targets.Count; index++)
                {
                    GameObject targetGo = targets[index];
                    JObject result = (JObject)results[index];
                    string error = null;
                    try
                    {
                        if (targetGo != null)
                        {
                            if (DestroyForTests != null)
                                DestroyForTests(targetGo);
                            else
                                Undo.DestroyObjectImmediate(targetGo);
                        }
                    }
                    catch (Exception exception)
                    {
                        error = exception.GetBaseException().Message;
                    }
                    bool disappeared = UnityObjectIdCompat.InstanceIDToObjectCompat(result.Value<int>("instanceID")) == null;
                    result["status"] = disappeared ? "applied" : "failed";
                    result["message"] = error ?? (disappeared ? null : "Unity refused deletion; the GameObject still exists.");
                    if (disappeared)
                    {
                        deletedName = result.Value<string>("name");
                        deletedObjects.Add(new { name = deletedName, instanceID = result.Value<int>("instanceID") });
                    }
                    if (error != null || !disappeared)
                    {
                        failure = result.Value<string>("message");
                        break;
                    }
                }
            }
            finally
            {
                try
                {
                    Undo.CollapseUndoOperations(undoGroup);
                }
                finally
                {
                    Undo.IncrementCurrentGroup();
                }
            }

            // A parent deletion may also have removed targets that were not individually attempted.
            foreach (JObject result in results)
            {
                if (result.Value<string>("status") == "skipped" && UnityObjectIdCompat.InstanceIDToObjectCompat(result.Value<int>("instanceID")) == null)
                {
                    result["status"] = "applied";
                    result["message"] = "Removed by an earlier target deletion.";
                    deletedObjects.Add(new { name = result.Value<string>("name"), instanceID = result.Value<int>("instanceID") });
                }
            }

            if (failure != null)
                return new ErrorResponse(
                    "GameObject deletion did not complete: " + failure,
                    new
                    {
                        mode = "best_effort",
                        results,
                        applied = results
                            .Where(result => result.Value<string>("status") == "applied")
                            .Select(result => result.Value<int>("instanceID"))
                            .ToArray(),
                        failed = results
                            .Where(result => result.Value<string>("status") == "failed")
                            .Select(result => result.Value<int>("instanceID"))
                            .ToArray(),
                        skipped = results
                            .Where(result => result.Value<string>("status") == "skipped")
                            .Select(result => result.Value<int>("instanceID"))
                            .ToArray(),
                    }
                );

            if (deletedObjects.Count > 0)
            {
                string message =
                    targets.Count == 1 ? $"GameObject '{deletedName}' deleted successfully." : $"{deletedObjects.Count} GameObjects deleted successfully.";
                return new SuccessResponse(message, deletedObjects);
            }

            return new ErrorResponse("Failed to delete target GameObject(s).");
        }
    }
}
