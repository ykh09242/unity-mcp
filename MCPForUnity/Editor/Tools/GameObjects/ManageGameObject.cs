#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers; // For Response class
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    /// <summary>
    /// Handles GameObject manipulation within the current scene (CRUD, find, components).
    /// </summary>
    [McpForUnityTool("manage_gameobject", AutoRegister = false, Group = "legacy")]
    public static class ManageGameObject
    {
        // One native action catalogue is shared by preview and actual dispatch.
        private enum ActionKind
        {
            Unknown,
            Create,
            Modify,
            Delete,
            Duplicate,
            MoveRelative,
            LookAt,
            Placement,
        }

        private static string NormalizeAction(JToken token) => token?.ToString().ToLowerInvariant();

        private static ActionKind ResolveAction(string action) =>
            action switch
            {
                "create" => ActionKind.Create,
                "modify" => ActionKind.Modify,
                "delete" => ActionKind.Delete,
                "duplicate" => ActionKind.Duplicate,
                "move_relative" => ActionKind.MoveRelative,
                "look_at" => ActionKind.LookAt,
                "placement" => ActionKind.Placement,
                _ => ActionKind.Unknown,
            };

        internal static BatchActionValidation ValidateBatchAction(JObject parameters)
        {
            string action = NormalizeAction(parameters?["action"]);
            if (string.IsNullOrEmpty(action))
                return BatchActionValidation.Rejected("missing_action");
            ActionKind resolved = ResolveAction(action);
            if (resolved == ActionKind.Unknown)
                return BatchActionValidation.Rejected("unsupported_action");
            return BatchActionValidation.Valid;
        }

        // --- Main Handler ---

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            try
            {
                @params = NormalizeParameterAliases(@params);
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse(error.Message);
            }

            string action = NormalizeAction(@params["action"]);
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action parameter is required.");
            }

            // Parameters used by various actions
            JToken targetToken = @params["target"]; // Can be string (name/path) or int (instanceID)
            string name = @params["name"]?.ToString();

            // --- Usability Improvement: Alias 'name' to 'target' for modification actions ---
            // If 'target' is missing but 'name' is provided, and we aren't creating a new object,
            // assume the user meant "find object by name".
            if (targetToken == null && !string.IsNullOrEmpty(name) && action != "create")
            {
                targetToken = name;
                // We don't update @params["target"] because we use targetToken locally mostly,
                // but some downstream methods might parse @params directly. Let's update @params too for safety.
                @params["target"] = name;
            }
            // -------------------------------------------------------------------------------

            string searchMethod = @params["searchMethod"]?.ToString().ToLowerInvariant();
            string tag = @params["tag"]?.ToString();
            string layer = @params["layer"]?.ToString();
            JToken parentToken = @params["parent"];

            // Coerce string JSON to JObject for 'componentProperties' if provided as a JSON string
            var componentPropsToken = @params["componentProperties"];
            if (componentPropsToken != null && componentPropsToken.Type == JTokenType.String)
            {
                try
                {
                    var parsed = JObject.Parse(componentPropsToken.ToString());
                    @params["componentProperties"] = parsed;
                }
                catch (Newtonsoft.Json.JsonException e)
                {
                    return new ErrorResponse($"'componentProperties' must be a JSON object: {e.Message}");
                }
            }

            // --- Prefab Asset Check ---
            // Prefab assets require different tools. Only 'create' (instantiation) is valid here.
            string targetPath = targetToken?.Type == JTokenType.String ? targetToken.ToString() : null;
            if (
                !string.IsNullOrEmpty(targetPath)
                && targetPath.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                && action != "create" // Allow prefab instantiation
            )
            {
                return new ErrorResponse(
                    $"Target '{targetPath}' is a prefab asset. "
                        + $"Use 'manage_asset' with action='modify' for prefab asset modifications, "
                        + $"or 'manage_prefabs' with action='modify_contents' to edit the prefab headlessly, or 'manage_prefabs' with action='close_prefab_stage' to exit prefab editing mode."
                );
            }
            // --- End Prefab Asset Check ---

            try
            {
                switch (ResolveAction(action))
                {
                    // --- Primary lifecycle actions (kept in manage_gameobject) ---
                    case ActionKind.Create:
                        return GameObjectCreate.Handle(@params);
                    case ActionKind.Modify:
                        return GameObjectModify.Handle(@params, targetToken, searchMethod);
                    case ActionKind.Delete:
                        return GameObjectDelete.Handle(targetToken, searchMethod);
                    case ActionKind.Duplicate:
                        return GameObjectDuplicate.Handle(@params, targetToken, searchMethod);
                    case ActionKind.MoveRelative:
                        return GameObjectMoveRelative.Handle(@params, targetToken, searchMethod);
                    case ActionKind.LookAt:
                        return GameObjectLookAt.Handle(@params, targetToken, searchMethod);
                    case ActionKind.Placement:
                        return GameObjectPlacement.Handle(@params);

                    default:
                        return new ErrorResponse($"Unknown action: '{action}'.");
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageGameObject] Action '{action}' failed: {e}");
                return new ErrorResponse($"Internal error processing action '{action}': {e.Message}");
            }
        }

        // Native reader spellings are the contract. Resolve aliases once for direct and batch
        // dispatch without touching component property names or other nested user payloads.
        internal static JObject NormalizeParameterAliases(JObject source)
        {
            var result = (JObject)source.DeepClone();
            foreach (
                string canonical in new[]
                {
                    "new_name",
                    "world_position_stays",
                    "sibling_index",
                    "reference_object",
                    "world_space",
                    "look_at_target",
                    "look_at_up",
                    "placement_mode",
                    "preview_token",
                    "searchMethod",
                    "primitiveType",
                    "prefabPath",
                    "saveAsPrefab",
                    "setActive",
                    "isStatic",
                    "componentsToAdd",
                    "componentsToRemove",
                    "componentProperties",
                }
            )
            {
                string alias = canonical.Contains('_') ? StringCaseUtility.ToCamelCase(canonical) : StringCaseUtility.ToSnakeCase(canonical);
                ResolveParameterAlias(result, canonical, alias);
            }
            return result;
        }

        internal static void ResolveParameterAlias(JObject parameters, string canonical, string alias)
        {
            JProperty first = parameters.Property(canonical);
            JProperty second = parameters.Property(alias);
            if (second == null || canonical == alias)
                return;
            if (first != null && !JToken.DeepEquals(first.Value, second.Value))
                throw new ArgumentException($"Conflicting aliases '{canonical}' and '{alias}'. Provide one value.");
            if (first == null)
                parameters[canonical] = second.Value.DeepClone();
            second.Remove();
        }
    }
}
