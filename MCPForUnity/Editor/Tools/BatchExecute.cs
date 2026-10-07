using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Executes multiple MCP commands within a single Unity-side handler. Commands are executed sequentially
    /// on the main thread to preserve determinism and Unity API safety.
    /// </summary>
    [McpForUnityTool("batch_execute", AutoRegister = false, RequiresExplicitConsent = true)]
    public static class BatchExecute
    {
        /// <summary>Default limit when no EditorPrefs override is set.</summary>
        internal const int DefaultMaxCommandsPerBatch = 25;

        /// <summary>Hard ceiling to prevent extreme editor freezes regardless of user setting.</summary>
        internal const int AbsoluteMaxCommandsPerBatch = 100;

        /// <summary>
        /// Returns the user-configured max commands per batch, clamped between 1 and <see cref="AbsoluteMaxCommandsPerBatch"/>.
        /// </summary>
        internal static int GetMaxCommandsPerBatch()
        {
            int configured = EditorPrefs.GetInt(EditorPrefKeys.BatchExecuteMaxCommands, DefaultMaxCommandsPerBatch);
            return Math.Clamp(configured, 1, AbsoluteMaxCommandsPerBatch);
        }

        public static Task<object> HandleCommand(JObject @params) => HandleCommand(@params, CancellationToken.None);

        public static async Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (@params == null)
            {
                return new ErrorResponse("'commands' payload is required.");
            }

            var commandsToken = @params["commands"] as JArray;
            if (commandsToken == null || commandsToken.Count == 0)
            {
                return new ErrorResponse("Provide at least one command entry in 'commands'.");
            }

            int maxCommands = GetMaxCommandsPerBatch();
            if (commandsToken.Count > maxCommands)
            {
                return new ErrorResponse(
                    $"A maximum of {maxCommands} commands are allowed per batch (configurable in MCP Tools window, hard max {AbsoluteMaxCommandsPerBatch})."
                );
            }

            bool failFast = @params.ReadScalar<bool?>("failFast") ?? false;
            bool parallelRequested = @params.ReadScalar<bool?>("parallel") ?? false;
            int? maxParallel = @params.ReadScalar<int?>("maxParallelism");

            // Validate every command's envelope before dispatching any command. Operational
            // failures from otherwise valid commands retain the best-effort behavior below.
            var validationFailures = new List<object>();
            foreach (var token in commandsToken)
            {
                string toolName = null;
                string error = null;
                if (token is not JObject entry)
                    error = "Command entries must be JSON objects.";
                else
                {
                    var toolToken = entry["tool"];
                    if (toolToken?.Type == JTokenType.String)
                        toolName = toolToken.Value<string>();
                    if (string.IsNullOrWhiteSpace(toolName))
                        error = "Each command must include a non-empty string 'tool' field.";
                    else if (string.Equals(toolName, "batch_execute", StringComparison.OrdinalIgnoreCase))
                        return new ErrorResponse("Nested batch_execute commands are not allowed.");
                    else if (entry["params"] is JToken parameters && parameters.Type != JTokenType.Null && parameters is not JObject)
                        error = "Command 'params' must be a JSON object or null when provided.";
                }
                if (error != null)
                    validationFailures.Add(
                        new
                        {
                            tool = toolName,
                            callSucceeded = false,
                            error,
                        }
                    );
            }
            if (validationFailures.Count > 0)
                return new ErrorResponse(
                    "One or more commands failed validation.",
                    new
                    {
                        results = validationFailures,
                        callSuccessCount = 0,
                        callFailureCount = validationFailures.Count,
                        parallelRequested,
                        parallelApplied = false,
                        maxParallelism = maxParallel,
                    }
                );

            if (parallelRequested)
            {
                McpLog.Warn("batch_execute parallel mode requested, but commands will run sequentially on the main thread for safety.");
            }

            var commandResults = new List<object>(commandsToken.Count);
            int invocationSuccessCount = 0;
            int invocationFailureCount = 0;
            bool anyCommandFailed = false;

            foreach (var token in commandsToken)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var commandObj = (JObject)token;
                string toolName = commandObj.Value<string>("tool");

                // CommandRegistry dispatches resources as well as tools. Apply both policies.
                var resourceMeta = MCPServiceLocator.ResourceDiscovery.GetResourceMetadata(toolName);
                if (resourceMeta != null && !MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(toolName))
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(
                        new
                        {
                            tool = toolName,
                            callSucceeded = false,
                            result = new ErrorResponse($"Resource '{toolName}' is disabled in the Unity Editor."),
                        }
                    );
                    if (failFast)
                        break;
                    continue;
                }

                // Block disabled tools (mirrors TransportCommandDispatcher check)
                var toolMeta = MCPServiceLocator.ToolDiscovery.GetToolMetadata(toolName);
                if (toolMeta != null && !MCPServiceLocator.ToolDiscovery.IsToolEnabled(toolName))
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(
                        new
                        {
                            tool = toolName,
                            callSucceeded = false,
                            result = new ErrorResponse($"Tool '{toolName}' is disabled in the Unity Editor."),
                        }
                    );
                    if (failFast)
                        break;
                    continue;
                }

                try
                {
                    var rawParams = commandObj["params"] as JObject ?? new JObject();
                    var commandParams = NormalizeParameterKeys(rawParams);
                    var result = await CommandRegistry.InvokeCommandAsync(toolName, commandParams, cancellationToken).ConfigureAwait(true);
                    cancellationToken.ThrowIfCancellationRequested();
                    bool callSucceeded = DetermineCallSucceeded(result);
                    if (callSucceeded)
                    {
                        invocationSuccessCount++;
                    }
                    else
                    {
                        invocationFailureCount++;
                        anyCommandFailed = true;
                    }

                    commandResults.Add(
                        new
                        {
                            tool = toolName,
                            callSucceeded,
                            result,
                        }
                    );

                    if (!callSucceeded && failFast)
                    {
                        break;
                    }
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(
                        new
                        {
                            tool = toolName,
                            callSucceeded = false,
                            error = ex.Message,
                        }
                    );

                    if (failFast)
                    {
                        break;
                    }
                }
            }

            bool overallSuccess = !anyCommandFailed;
            var data = new
            {
                results = commandResults,
                callSuccessCount = invocationSuccessCount,
                callFailureCount = invocationFailureCount,
                parallelRequested,
                parallelApplied = false,
                maxParallelism = maxParallel,
            };

            return overallSuccess ? new SuccessResponse("Batch execution completed.", data) : new ErrorResponse("One or more commands failed.", data);
        }

        private static bool DetermineCallSucceeded(object result)
        {
            if (result == null)
            {
                return true;
            }

            if (result is IMcpResponse response)
            {
                return response.Success;
            }

            if (result is JObject obj)
            {
                var successToken = obj["success"];
                if (successToken != null && successToken.Type == JTokenType.Boolean)
                {
                    return successToken.Value<bool>();
                }
            }

            if (result is IDictionary dictionary && dictionary.Contains("success") && dictionary["success"] is bool success)
            {
                return success;
            }

            // Built-in handlers also return anonymous objects. Inspect only their
            // explicit boolean status; serializing the full result can be expensive.
            var status = result.GetType().GetProperty("success", BindingFlags.Public | BindingFlags.Instance);
            if (status?.PropertyType == typeof(bool) && status.GetMethod?.IsPublic == true && status.GetIndexParameters().Length == 0)
                return (bool)status.GetValue(result);

            return true;
        }

        private static JObject NormalizeParameterKeys(JObject source)
        {
            if (source == null)
            {
                return new JObject();
            }

            var normalized = new JObject();
            foreach (var property in source.Properties())
            {
                string normalizedName = ToCamelCase(property.Name);
                normalized[normalizedName] = property.Value;
            }
            return normalized;
        }

        private static string ToCamelCase(string key) => StringCaseUtility.ToCamelCase(key);
    }
}
