#nullable disable
using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectMoveRelative
    {
        internal static object Handle(JObject @params, JToken targetToken, string searchMethod)
        {
            GameObject targetGo = ManageGameObjectCommon.FindObjectInternal(targetToken, searchMethod);
            if (targetGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            JToken referenceToken = @params["reference_object"];
            if (referenceToken == null)
            {
                return new ErrorResponse("'reference_object' parameter is required for 'move_relative' action.");
            }

            GameObject referenceGo = ManageGameObjectCommon.FindObjectInternal(referenceToken, "by_id_or_name_or_path");
            if (referenceGo == null)
            {
                return new ErrorResponse($"Reference object '{referenceToken}' not found.");
            }

            string direction = @params["direction"]?.ToString()?.ToLowerInvariant();
            float distance = @params["distance"]?.ReadScalar<float?>() ?? 1f;
            Vector3? customOffset;
            try
            {
                customOffset = ManageGameObjectCommon.ReadOptionalVector3(@params, "offset");
            }
            catch (ArgumentException e)
            {
                return new ErrorResponse(e.Message);
            }
            bool useWorldSpace = @params["world_space"]?.ReadScalar<bool?>() ?? true;

            Vector3 newPosition;

            if (customOffset.HasValue)
            {
                if (useWorldSpace)
                {
                    newPosition = referenceGo.transform.position + customOffset.Value;
                }
                else
                {
                    newPosition = referenceGo.transform.TransformPoint(customOffset.Value);
                }
            }
            else if (!string.IsNullOrEmpty(direction))
            {
                Vector3? directionVector = GetDirectionVector(direction, referenceGo.transform, useWorldSpace);
                if (!directionVector.HasValue)
                    return new ErrorResponse($"Unknown direction '{direction}'. Use left, right, up, down, forward/front or back/backward/behind.");
                newPosition = referenceGo.transform.position + directionVector.Value * distance;
            }
            else
            {
                return new ErrorResponse("Either 'direction' or 'offset' parameter is required for 'move_relative' action.");
            }

            Undo.RecordObject(targetGo.transform, $"Move {targetGo.name} relative to {referenceGo.name}");
            targetGo.transform.position = newPosition;

            EditorUtility.SetDirty(targetGo);
            EditorSceneManager.MarkSceneDirty(targetGo.scene);

            return new SuccessResponse(
                $"Moved '{targetGo.name}' relative to '{referenceGo.name}'.",
                new
                {
                    movedObject = targetGo.name,
                    referenceObject = referenceGo.name,
                    newPosition = new[] { targetGo.transform.position.x, targetGo.transform.position.y, targetGo.transform.position.z },
                    direction = direction,
                    distance = distance,
                    gameObject = Helpers.GameObjectSerializer.GetGameObjectData(targetGo),
                }
            );
        }

        private static Vector3? GetDirectionVector(string direction, Transform referenceTransform, bool useWorldSpace)
        {
            if (useWorldSpace)
            {
                switch (direction)
                {
                    case "right":
                        return Vector3.right;
                    case "left":
                        return Vector3.left;
                    case "up":
                        return Vector3.up;
                    case "down":
                        return Vector3.down;
                    case "forward":
                    case "front":
                        return Vector3.forward;
                    case "back":
                    case "backward":
                    case "behind":
                        return Vector3.back;
                    default:
                        return null;
                }
            }

            switch (direction)
            {
                case "right":
                    return referenceTransform.right;
                case "left":
                    return -referenceTransform.right;
                case "up":
                    return referenceTransform.up;
                case "down":
                    return -referenceTransform.up;
                case "forward":
                case "front":
                    return referenceTransform.forward;
                case "back":
                case "backward":
                case "behind":
                    return -referenceTransform.forward;
                default:
                    return null;
            }
        }
    }
}
