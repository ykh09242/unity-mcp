using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Numerics;
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
        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
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
            bool includeInactive = p.GetBool("includeInactive", false) ||
                                   p.GetBool("searchInactive", false);

            try
            {
                // Get all matching instance IDs
                var allIds = GameObjectLookup.SearchGameObjects(searchMethod, searchTerm, includeInactive, 0);
                
                // Use standard pagination response
                var paginatedResult = PaginationResponse<int>.Create(allIds, pagination);

                return new SuccessResponse("Found GameObjects", new
                {
                    instanceIDs = paginatedResult.Items,
                    pageSize = paginatedResult.PageSize,
                    cursor = paginatedResult.Cursor,
                    nextCursor = paginatedResult.NextCursor,
                    totalCount = paginatedResult.TotalCount,
                    hasMore = paginatedResult.HasMore
                });
            }
            catch (System.Exception ex)
            {
                McpLog.Error($"[FindGameObjects] Error searching GameObjects: {ex.Message}");
                return new ErrorResponse($"Error searching GameObjects: {ex.Message}");
            }
        }

        private static int CoercePaginationInt(JToken token, int defaultValue)
        {
            // Saturate whole numbers before Int32 conversion can reset paging to its defaults.
            if (token != null && (token.Type == JTokenType.Integer || token.Type == JTokenType.String)
                && BigInteger.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
                return (int)BigInteger.Min(BigInteger.Max(value, int.MinValue), int.MaxValue);

            return ParamCoercion.CoerceInt(token, defaultValue);
        }
    }
}
