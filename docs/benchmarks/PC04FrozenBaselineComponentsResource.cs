using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using BigInteger = System.Numerics.BigInteger;

namespace MCPForUnity.Editor.Resources.Scene
{
    public static class PC04FrozenBaselineComponentsResource
    {
        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            var idToken = @params["instanceID"] ?? @params["instance_id"] ?? @params["id"];
            int instanceID = ParamCoercion.CoerceInt(idToken, -1);
            if (instanceID == -1)
            {
                return new ErrorResponse("'instanceID' parameter is required.");
            }

            if (!ComponentInspectionOptions.Read(@params, out bool evaluateGetters))
                return new ErrorResponse("evaluate_getters must be true or false with matching aliases.");

            // Pagination parameters
            int pageSize = CoercePaginationInt(@params["pageSize"] ?? @params["page_size"], 25);
            int cursor = CoercePaginationInt(@params["cursor"], 0);
            bool includeProperties = ParamCoercion.CoerceBool(@params["includeProperties"] ?? @params["include_properties"], true);

            pageSize = Mathf.Clamp(pageSize, 1, 100);

            try
            {
                var go = GameObjectLookup.ResolveInstanceID(instanceID) as GameObject;
                if (go == null)
                {
                    return new ErrorResponse($"GameObject with instance ID {instanceID} not found.");
                }

                var allComponents = go.GetComponents<Component>().Where(c => c != null).ToList();
                int total = allComponents.Count;

                cursor = Mathf.Clamp(cursor, 0, total);

                var pagedComponents = allComponents.Skip(cursor).Take(pageSize).ToList();

                var componentData = new List<object>();
                var budget = new BoundedResponseJson.Budget();
                foreach (var component in pagedComponents)
                {
                    if (includeProperties)
                    {
                        componentData.Add(GameObjectSerializer.GetComponentDataBounded(component, budget, true, evaluateGetters));
                    }
                    else
                    {
                        componentData.Add(new { typeName = component.GetType().FullName, instanceID = component.GetInstanceIDCompat() });
                    }
                }

                int? nextCursor = cursor + pagedComponents.Count < total ? cursor + pagedComponents.Count : (int?)null;

                return new
                {
                    success = true,
                    data = new
                    {
                        gameObjectID = instanceID,
                        gameObjectName = go.name,
                        components = componentData,
                        cursor = cursor,
                        pageSize = pageSize,
                        nextCursor = nextCursor,
                        totalCount = total,
                        hasMore = nextCursor.HasValue,
                        includeProperties = includeProperties,
                    },
                };
            }
            catch (Exception e)
            {
                McpLog.Error($"[GameObjectComponentsResource] Error getting components: {e}");
                return new ErrorResponse($"Error getting components: {e.Message}");
            }
        }

        private static int CoercePaginationInt(JToken token, int defaultValue)
        {
            // Saturate whole numbers before Int32 conversion can reset paging to its defaults.
            if (
                token != null
                && (token.Type == JTokenType.Integer || token.Type == JTokenType.String)
                && BigInteger.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)
            )
                return (int)BigInteger.Min(BigInteger.Max(value, int.MinValue), int.MaxValue);

            return ParamCoercion.CoerceInt(token, defaultValue);
        }
    }

}
