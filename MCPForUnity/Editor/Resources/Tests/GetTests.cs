using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnity.Editor.Resources.Tests
{
    /// <summary>
    /// Provides access to Unity tests from the Test Framework with pagination and filtering support.
    /// This is a read-only resource that can be queried by MCP clients.
    ///
    /// Parameters:
    /// - mode (optional): Filter by "EditMode" or "PlayMode"
    /// - filter (optional): Filter test names by pattern (case-insensitive contains)
    /// - page_size (optional): Number of tests per page (default: 50, max: 200)
    /// - cursor (optional): 0-based cursor for pagination
    /// - page_number (optional): 1-based page number (converted to cursor)
    /// </summary>
    [McpForUnityResource("get_tests")]
    public static class GetTests
    {
        private const int DEFAULT_PAGE_SIZE = 50;
        private const int MAX_PAGE_SIZE = 200;

        internal static async Task<PaginationResponse<Dictionary<string, string>>> RetrievePageAsync(TestMode? mode, string filter, JObject parameters)
        {
            int size = ParamCoercion.CoerceInt(parameters?["page_size"] ?? parameters?["pageSize"], DEFAULT_PAGE_SIZE);
            var normalized = parameters == null ? new JObject() : new JObject(parameters);
            normalized["page_size"] = size <= 0 ? DEFAULT_PAGE_SIZE : Math.Min(size, MAX_PAGE_SIZE);
            var page = PaginationRequest.FromParams(normalized, DEFAULT_PAGE_SIZE);
            var budget = TimeSpan.FromSeconds(
                OperationTimeouts.ReadSeconds(parameters, OperationTimeouts.EnvironmentSeconds("UNITY_MCP_TEST_DISCOVERY_TIMEOUT", 300))
            );
            var service = MCPServiceLocator.Tests;
            if (service is TestRunnerService native)
                return await native.GetTestPageAsync(mode, budget, page, filter).ConfigureAwait(true);

            var tests = await (service is IBudgetedTestDiscovery timed ? timed.GetTestsAsync(mode, budget) : service.GetTestsAsync(mode)).ConfigureAwait(true);
            var rows = string.IsNullOrEmpty(filter)
                ? tests as IList<Dictionary<string, string>> ?? tests.ToList()
                : tests
                    .Where(t =>
                        (t.TryGetValue("name", out string name) && name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                        || (t.TryGetValue("full_name", out string fullName) && fullName.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                    )
                    .ToList();
            return PaginationResponse<Dictionary<string, string>>.Create(rows, page);
        }

        public static async Task<object> HandleCommand(JObject @params)
        {
            // Parse mode filter
            TestMode? modeFilter = null;
            string modeStr = @params?["mode"]?.ToString();
            if (!string.IsNullOrEmpty(modeStr))
            {
                if (!ModeParser.TryParse(modeStr, out modeFilter, out var parseError))
                {
                    return new ErrorResponse(parseError);
                }
            }

            // Parse name filter
            string nameFilter = @params?["filter"]?.ToString();

            McpLog.Info($"[GetTests] Retrieving tests (mode={modeFilter?.ToString() ?? "all"}, filter={nameFilter ?? "none"})");

            PaginationResponse<Dictionary<string, string>> response;
            try
            {
                response = await RetrievePageAsync(modeFilter, nameFilter, @params).ConfigureAwait(true);
            }
            catch (Exception ex)
            {
                McpLog.Error($"[GetTests] Error retrieving tests: {ex.Message}\n{ex.StackTrace}");
                return new ErrorResponse("Failed to retrieve tests");
            }

            string message = !string.IsNullOrEmpty(nameFilter)
                ? $"Retrieved {response.Items.Count} of {response.TotalCount} tests matching '{nameFilter}' (cursor {response.Cursor})"
                : $"Retrieved {response.Items.Count} of {response.TotalCount} tests (cursor {response.Cursor})";

            return new SuccessResponse(message, response);
        }
    }

    /// <summary>
    /// DEPRECATED: Use get_tests with mode parameter instead.
    /// Provides access to Unity tests for a specific mode (EditMode or PlayMode).
    /// This is a read-only resource that can be queried by MCP clients.
    ///
    /// Parameters:
    /// - mode (required): "EditMode" or "PlayMode"
    /// - filter (optional): Filter test names by pattern (case-insensitive contains)
    /// - page_size (optional): Number of tests per page (default: 50, max: 200)
    /// - cursor (optional): 0-based cursor for pagination
    /// </summary>
    [McpForUnityResource("get_tests_for_mode")]
    public static class GetTestsForMode
    {
        public static async Task<object> HandleCommand(JObject @params)
        {
            string modeStr = @params?["mode"]?.ToString();
            if (string.IsNullOrEmpty(modeStr))
            {
                return new ErrorResponse("'mode' parameter is required");
            }

            if (!ModeParser.TryParse(modeStr, out var parsedMode, out var parseError))
            {
                return new ErrorResponse(parseError);
            }

            // Parse name filter
            string nameFilter = @params?["filter"]?.ToString();

            McpLog.Info($"[GetTestsForMode] Retrieving tests for mode: {parsedMode.Value} (filter={nameFilter ?? "none"})");

            PaginationResponse<Dictionary<string, string>> response;
            try
            {
                response = await GetTests.RetrievePageAsync(parsedMode, nameFilter, @params).ConfigureAwait(true);
            }
            catch (Exception ex)
            {
                McpLog.Error($"[GetTestsForMode] Error retrieving tests: {ex.Message}\n{ex.StackTrace}");
                return new ErrorResponse("Failed to retrieve tests");
            }

            string message =
                nameFilter != null
                    ? $"Retrieved {response.Items.Count} of {response.TotalCount} {parsedMode.Value} tests matching '{nameFilter}'"
                    : $"Retrieved {response.Items.Count} of {response.TotalCount} {parsedMode.Value} tests";

            return new SuccessResponse(message, response);
        }
    }

    internal static class ModeParser
    {
        internal static bool TryParse(string modeStr, out TestMode? mode, out string error)
        {
            error = null;
            mode = null;

            if (string.IsNullOrWhiteSpace(modeStr))
            {
                error = "'mode' parameter cannot be empty";
                return false;
            }

            if (modeStr.Equals("EditMode", StringComparison.OrdinalIgnoreCase))
            {
                mode = TestMode.EditMode;
                return true;
            }

            if (modeStr.Equals("PlayMode", StringComparison.OrdinalIgnoreCase))
            {
                mode = TestMode.PlayMode;
                return true;
            }

            error = $"Unknown test mode: '{modeStr}'. Use 'EditMode' or 'PlayMode'";
            return false;
        }
    }
}
