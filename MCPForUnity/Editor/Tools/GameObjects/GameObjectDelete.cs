#nullable disable
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectDelete
    {
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

            List<object> deletedObjects = new List<object>();
            string deletedName = null;
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Delete GameObject");
            foreach (var targetGo in targets)
            {
                if (targetGo != null)
                {
                    string goName = targetGo.name;
                    int goId = targetGo.GetInstanceIDCompat();
                    Undo.DestroyObjectImmediate(targetGo);
                    deletedObjects.Add(new { name = goName, instanceID = goId });
                    deletedName = goName;
                }
            }
            Undo.CollapseUndoOperations(undoGroup);

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
