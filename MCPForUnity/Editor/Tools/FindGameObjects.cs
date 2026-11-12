using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Numerics;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Tool for searching GameObjects in the scene.
    /// Returns only instance IDs with pagination support.
    ///
    /// This is a focused search tool that returns lightweight results (IDs only).
    /// For detailed GameObject data, use the unity://scene/gameobject/{id} resource.
    /// </summary>
    [McpForUnityTool("find_gameobjects")]
    public static class FindGameObjects
    {
        /// <summary>
        /// Handles the find_gameobjects command.
        /// </summary>
        /// <param name="params">Command parameters</param>
        /// <returns>Paginated list of instance IDs</returns>
        public static object HandleCommand(JObject @params) => HandleCommandCore(@params, CancellationToken.None);

        public static Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken) =>
            Task.FromResult(HandleCommandCore(@params, cancellationToken));

        private static object HandleCommandCore(JObject @params, CancellationToken cancellationToken)
        {
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                string[] projection = ReadProjection(@params);
                string sceneScope;
                try
                {
                    sceneScope = GameObjectSearch.SceneScope(@params);
                }
                catch (System.ArgumentException ex)
                {
                    return new ErrorResponse("invalid_parameters", new { message = ex.Message });
                }
                if (sceneScope != "legacy")
                {
                    var search = (JObject)@params.DeepClone();
                    search.Remove("projection");
                    return Project(GameObjectSearch.Handle(search, cancellationToken), projection, cancellationToken);
                }

                var p = new ToolParams(@params);

                // Parse search parameters
                string searchMethod = p.Get("searchMethod", "by_name");

                // Try searchTerm, search_term, or target (for backwards compatibility)
                string searchTerm = p.Get("searchTerm");
                if (string.IsNullOrEmpty(searchTerm))
                {
                    searchTerm = p.Get("target");
                }

                if (string.IsNullOrEmpty(searchTerm))
                {
                    return new ErrorResponse("'searchTerm' or 'target' parameter is required.");
                }

                // Pagination parameters using standard PaginationRequest
                int requestedPageSize = CoercePaginationInt(@params["page_size"] ?? @params["pageSize"], 50);
                int clampedPageSize = requestedPageSize <= 0 ? 50 : Mathf.Clamp(requestedPageSize, 1, 500);
                var paginationParams = new JObject(@params);
                paginationParams["page_size"] = clampedPageSize;
                if (@params["cursor"] != null)
                    paginationParams["cursor"] = CoercePaginationInt(@params["cursor"], 0);
                var pageNumberToken = @params["page_number"] ?? @params["pageNumber"];
                if (pageNumberToken != null)
                    paginationParams["page_number"] = CoercePaginationInt(pageNumberToken, 1);
                var pagination = PaginationRequest.FromParams(paginationParams, defaultPageSize: 50);

                // Search options (supports multiple parameter name variants)
                bool includeInactive = p.GetBool("includeInactive", false) || p.GetBool("searchInactive", false);

                // Get all matching instance IDs
                var allIds = GameObjectLookup.SearchGameObjects(searchMethod, searchTerm, includeInactive, 0);

                // Use standard pagination response
                var paginatedResult = PaginationResponse<int>.Create(allIds, pagination);

                return Project(
                    new SuccessResponse(
                        "Found GameObjects",
                        new
                        {
                            instanceIDs = paginatedResult.Items,
                            pageSize = paginatedResult.PageSize,
                            cursor = paginatedResult.Cursor,
                            nextCursor = paginatedResult.NextCursor,
                            totalCount = paginatedResult.TotalCount,
                            hasMore = paginatedResult.HasMore,
                        }
                    ),
                    projection,
                    cancellationToken
                );
            }
            catch (System.OperationCanceledException)
            {
                throw;
            }
            catch (System.ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (System.Exception ex)
            {
                McpLog.Error($"[FindGameObjects] Error searching GameObjects: {ex.Message}");
                return new ErrorResponse($"Error searching GameObjects: {ex.Message}");
            }
        }

        private static string[] ReadProjection(JObject parameters)
        {
            if (parameters["projection"] == null)
                return Array.Empty<string>();
            if (!(parameters["projection"] is JArray fields) || fields.Count > 5 || fields.Any(field => field.Type != JTokenType.String))
                throw new ArgumentException("projection must be an array of at most five field names.");
            string[] names = fields.Values<string>().ToArray();
            if (names.Distinct().Count() != names.Length || names.Any(name => !new[] { "name", "active", "transform", "components", "handle" }.Contains(name)))
                throw new ArgumentException("projection fields must be unique: name, active, transform, components, handle.");
            return names;
        }

        private static object Project(object response, string[] fields, CancellationToken token)
        {
            if (fields.Length == 0 || !(response is SuccessResponse success))
                return response;
            JObject data = JObject.FromObject(success.Data);
            var rows = new JArray();
            var findings = new JArray();
            int bytes = 2;
            bool complete = true;
            foreach (JToken idToken in (JArray)data["instanceIDs"])
            {
                if (rows.Count >= 64)
                {
                    complete = false;
                    break;
                }
                if (token.IsCancellationRequested)
                {
                    complete = false;
                    break;
                }
                int id = idToken.Value<int>();
                var row = new JObject { ["instanceID"] = id };
                try
                {
                    GameObject go = GameObjectLookup.FindById(id);
                    if (go == null)
                        throw new InvalidOperationException("original_search_result_unavailable");
                    foreach (string field in fields)
                        switch (field)
                        {
                            case "name":
                                row["name"] = go.name.Length <= 256 ? go.name : go.name.Substring(0, 256);
                                if (go.name.Length > 256)
                                    complete = false;
                                break;
                            case "active":
                                row["activeSelf"] = go.activeSelf;
                                row["activeInHierarchy"] = go.activeInHierarchy;
                                break;
                            case "transform":
                                UnityEngine.Vector3 position = go.transform.position;
                                UnityEngine.Vector3 scale = go.transform.localScale;
                                if (
                                    new[] { position.x, position.y, position.z, scale.x, scale.y, scale.z }.Any(value =>
                                        float.IsNaN(value) || float.IsInfinity(value)
                                    )
                                )
                                    throw new InvalidOperationException("nonfinite_transform");
                                row["position"] = new JArray(position.x, position.y, position.z);
                                row["localScale"] = new JArray(scale.x, scale.y, scale.z);
                                break;
                            case "components":
                            {
                                using var serialized = new UnityEditor.SerializedObject(go);
                                serialized.Update();
                                using var components = serialized.FindProperty("m_Component");
                                if (components == null || !components.isArray)
                                    throw new InvalidOperationException("component_projection_unavailable");
                                int count = components.arraySize;
                                var names = new JArray();
                                for (int index = 0; index < Math.Min(16, count); index++)
                                {
                                    using var element = components.GetArrayElementAtIndex(index);
                                    using var reference = element.FindPropertyRelative("component");
                                    if (reference == null)
                                        throw new InvalidOperationException("component_projection_unavailable");
                                    UnityEngine.Object component = reference.objectReferenceValue;
                                    string name = component == null ? null : component.GetType().FullName;
                                    names.Add(name != null && name.Length > 256 ? name.Substring(0, 256) : name);
                                    if (name?.Length > 256)
                                        complete = false;
                                }
                                row["components"] = names;
                                row["componentCount"] = count;
                                if (count > 16)
                                    complete = false;
                                break;
                            }
                            case "handle":
                                row["handle"] = ObjectHandles.Capture(go, token)["handle"];
                                break;
                        }
                }
                catch (OperationCanceledException)
                {
                    complete = false;
                    break;
                }
                catch (Exception)
                {
                    row["projection_status"] = "unavailable";
                    complete = false;
                    if (findings.Count < 32)
                        findings.Add(new JObject { ["instanceID"] = id, ["code"] = "projection_unavailable" });
                }
                int size = Encoding.UTF8.GetByteCount(row.ToString(Newtonsoft.Json.Formatting.None));
                if (bytes + size > 16384)
                {
                    complete = false;
                    break;
                }
                bytes += size;
                rows.Add(row);
            }
            data["projection"] = rows;
            data["projection_verification"] = PostEditVerification.Summary(
                "same_returned_ids; max64_rows_16KiB_16_components_per_row",
                new JObject { ["projected_count"] = rows.Count },
                findings,
                complete
            );
            return new SuccessResponse(success.Message, data);
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
