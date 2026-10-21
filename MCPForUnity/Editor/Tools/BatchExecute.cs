using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    internal readonly struct BatchActionValidation
    {
        internal string Status { get; }
        internal string Reason { get; }

        private BatchActionValidation(string status, string reason)
        {
            Status = status;
            Reason = reason;
        }

        internal static BatchActionValidation Valid => new BatchActionValidation("valid", "ok");
        internal static BatchActionValidation Unavailable => new BatchActionValidation("incomplete", "action_validation_unavailable");

        internal static BatchActionValidation Rejected(string reason) => new BatchActionValidation("rejected", reason);
    }

    /// <summary>
    /// Executes multiple MCP commands within a single Unity-side handler. Commands are executed sequentially
    /// on the main thread to preserve determinism and Unity API safety.
    /// </summary>
    [McpForUnityTool("batch_execute", AutoRegister = false, RequiresExplicitConsent = true)]
    public static class BatchExecute
    {
        /// <summary>Default limit when no EditorPrefs override is set.</summary>
        internal const int DefaultMaxCommandsPerBatch = 100;

        /// <summary>Hard ceiling to prevent extreme editor freezes regardless of user setting.</summary>
        internal const int AbsoluteMaxCommandsPerBatch = 1000;

        /// <summary>
        /// Returns the user-configured max commands per batch, clamped between 1 and <see cref="AbsoluteMaxCommandsPerBatch"/>.
        /// </summary>
        internal static int GetMaxCommandsPerBatch()
        {
            int configured = EditorPrefs.GetInt(EditorPrefKeys.BatchExecuteMaxCommands, DefaultMaxCommandsPerBatch);
            return Math.Clamp(configured, 1, AbsoluteMaxCommandsPerBatch);
        }

        public static Task<object> HandleCommand(JObject @params) => HandleCommand(@params, CancellationToken.None);

        private static bool IsBatchName(string name) =>
            string.Equals(name, "batch_execute", StringComparison.OrdinalIgnoreCase)
            || string.Equals(name, "batch_validate", StringComparison.OrdinalIgnoreCase);

        private static string EchoTool(string name) => name == null || name.Length <= 128 ? name : name.Substring(0, 128);

        private static JArray NewOutcomes(JArray commands, string state, string reason)
        {
            var rows = new JArray();
            for (int index = 0; index < commands.Count; index++)
            {
                string tool = (commands[index] as JObject)?["tool"]?.Type == JTokenType.String ? commands[index]["tool"].Value<string>() : null;
                rows.Add(
                    new JObject
                    {
                        ["index"] = index,
                        ["tool"] = EchoTool(tool),
                        ["toolTruncated"] = tool?.Length > 128,
                        ["state"] = state,
                        ["reason"] = reason,
                    }
                );
            }
            return rows;
        }

        private static void SetOutcome(JArray outcomes, int index, string state, string reason)
        {
            outcomes[index]["state"] = state;
            outcomes[index]["reason"] = reason;
        }

        private static JObject ValidationData(JArray items, bool complete, bool valid) =>
            new JObject
            {
                ["contractVersion"] = 1,
                ["validateOnly"] = true,
                ["scope"] = "action_names",
                ["complete"] = complete,
                ["valid"] = valid,
                ["items"] = items,
            };

        private static object InvalidBatch(string message, JArray commands, object legacyData = null, bool preview = false)
        {
            if (commands == null || commands.Count > AbsoluteMaxCommandsPerBatch)
                return new ErrorResponse(message, legacyData);
            var data = legacyData == null ? new JObject() : JObject.FromObject(legacyData);
            data["outcomes"] = NewOutcomes(commands, "not_run", "invalid_batch");
            data["requestedCount"] = commands.Count;
            data["attemptedCount"] = 0;
            data["notRunCount"] = commands.Count;
            data["executionStatus"] = "not_started";
            data["rollbackPerformed"] = false;
            if (preview)
            {
                var items = NewOutcomes(commands, "not_run", "invalid_batch");
                foreach (JObject row in items)
                {
                    row.Remove("state");
                    row["status"] = "rejected";
                }
                data["validation"] = ValidationData(items, true, false);
            }
            return new ErrorResponse(message, data);
        }

        private static object ReadEnvelope(
            JObject @params,
            bool preview,
            out JArray commandsToken,
            out bool failFast,
            out bool parallelRequested,
            out int? maxParallel
        )
        {
            commandsToken = null;
            failFast = false;
            parallelRequested = false;
            maxParallel = null;
            if (@params == null)
            {
                return InvalidBatch("'commands' payload is required.", commandsToken, preview: preview);
            }

            commandsToken = @params["commands"] as JArray;
            if (commandsToken == null || commandsToken.Count == 0)
            {
                return InvalidBatch("Provide at least one command entry in 'commands'.", commandsToken, preview: preview);
            }

            int maxCommands = GetMaxCommandsPerBatch();
            if (commandsToken.Count > maxCommands)
            {
                return InvalidBatch(
                    $"A maximum of {maxCommands} commands are allowed per batch (configurable in MCP Tools window, hard max {AbsoluteMaxCommandsPerBatch}).",
                    commandsToken,
                    preview: preview
                );
            }

            failFast = @params.ReadScalar<bool?>("failFast") ?? false;
            parallelRequested = @params.ReadScalar<bool?>("parallel") ?? false;
            maxParallel = @params.ReadScalar<int?>("maxParallelism");

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
                    else if (IsBatchName(toolName))
                        return InvalidBatch("Nested batch_execute commands are not allowed.", commandsToken, preview: preview);
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
                return InvalidBatch(
                    "One or more commands failed validation.",
                    commandsToken,
                    new
                    {
                        results = validationFailures,
                        callSuccessCount = 0,
                        callFailureCount = validationFailures.Count,
                        parallelRequested,
                        parallelApplied = false,
                        maxParallelism = maxParallel,
                    },
                    preview
                );

            return null;
        }

        // Internal registry alias only: no new tool metadata, default or public duplicate.
        public static object ValidateCommand(JObject @params)
        {
            object rejected = ReadEnvelope(@params, true, out var commands, out _, out _, out _);
            if (rejected != null)
                return rejected;
            var items = new JArray();
            bool complete = true,
                valid = true;
            bool batchEnabled = MCPServiceLocator.ToolDiscovery.IsToolEnabled("batch_execute");
            CommandRegistry.Initialize();
            for (int index = 0; index < commands.Count; index++)
            {
                var command = (JObject)commands[index];
                string name = command.Value<string>("tool");
                BatchActionValidation result;
                if (!batchEnabled)
                    result = BatchActionValidation.Rejected("batch_disabled");
                else if (!CommandRegistry.HasHandler(name))
                    result = BatchActionValidation.Rejected("unknown_command");
                else if (MCPServiceLocator.ResourceDiscovery.GetResourceMetadata(name) != null && !MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(name))
                    result = BatchActionValidation.Rejected("resource_disabled");
                else if (MCPServiceLocator.ToolDiscovery.GetToolMetadata(name) != null && !MCPServiceLocator.ToolDiscovery.IsToolEnabled(name))
                    result = BatchActionValidation.Rejected("tool_disabled");
                else
                {
                    try
                    {
                        result = CommandRegistry.ValidateBatchAction(name, NormalizeParameterKeys(command["params"] as JObject ?? new JObject()));
                    }
                    catch (Exception)
                    {
                        result = BatchActionValidation.Rejected("invalid_action");
                    }
                }
                complete &= result.Status != "incomplete";
                valid &= result.Status == "valid";
                items.Add(
                    new JObject
                    {
                        ["index"] = index,
                        ["tool"] = EchoTool(name),
                        ["toolTruncated"] = name.Length > 128,
                        ["status"] = result.Status,
                        ["reason"] = result.Reason,
                    }
                );
            }
            var data = new
            {
                validation = ValidationData(items, complete, valid),
                outcomes = NewOutcomes(commands, "not_run", "validation_only"),
                requestedCount = commands.Count,
                attemptedCount = 0,
                notRunCount = commands.Count,
                executionStatus = "not_started",
                rollbackPerformed = false,
            };
            return valid ? new SuccessResponse("Batch action validation completed.", data) : new ErrorResponse("Batch action validation did not pass.", data);
        }

        public static Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken) => HandleCommand(@params, cancellationToken, null);

        public static async Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken, ICommandProgress progress)
        {
            cancellationToken.ThrowIfCancellationRequested();
            object rejected = ReadEnvelope(@params, false, out var commandsToken, out bool failFast, out bool parallelRequested, out int? maxParallel);
            if (rejected != null)
                return rejected;
            if (parallelRequested)
            {
                McpLog.Warn("batch_execute parallel mode requested, but commands will run sequentially on the main thread for safety.");
            }

            var outcomes = NewOutcomes(commandsToken, "not_run", "fail_fast");
            var commandResults = new List<object>(commandsToken.Count);
            int invocationSuccessCount = 0;
            int invocationFailureCount = 0;
            bool anyCommandFailed = false;

            int inputIndex = 0;
            foreach (var token in commandsToken)
            {
                int index = inputIndex++;
                cancellationToken.ThrowIfCancellationRequested();
                // The previous child has settled. Recheck the parent before the
                // next item; initial dispatcher/preview admission is unchanged.
                if (index > 0 && !MCPServiceLocator.ToolDiscovery.IsToolEnabled("batch_execute"))
                {
                    for (int remaining = index; remaining < commandsToken.Count; remaining++)
                        SetOutcome(outcomes, remaining, "not_run", "batch_disabled");
                    anyCommandFailed = true;
                    break;
                }
                int completedBefore = commandResults.Count;
                try
                {
                    var commandObj = (JObject)token;
                    string toolName = commandObj.Value<string>("tool");

                    // CommandRegistry dispatches resources as well as tools. Apply both policies.
                    var resourceMeta = MCPServiceLocator.ResourceDiscovery.GetResourceMetadata(toolName);
                    if (resourceMeta != null && !MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(toolName))
                    {
                        SetOutcome(outcomes, index, "failed", "resource_disabled");
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
                        SetOutcome(outcomes, index, "failed", "tool_disabled");
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
                        SetOutcome(outcomes, index, callSucceeded ? "succeeded" : "failed", callSucceeded ? "ok" : "handler_failed");
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
                        SetOutcome(outcomes, index, "failed", "handler_exception");
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
                finally
                {
                    if (commandResults.Count > completedBefore)
                    {
                        try
                        {
                            progress?.Report(commandResults.Count, commandsToken.Count, CommandProgressStage.BatchItems);
                        }
                        catch (Exception) { /* An optional observer must not alter the settled batch outcome. */ }
                    }
                }
            }

            bool overallSuccess = !anyCommandFailed;
            var data = new
            {
                results = commandResults,
                outcomes,
                requestedCount = commandsToken.Count,
                attemptedCount = commandResults.Count,
                notRunCount = commandsToken.Count - commandResults.Count,
                executionStatus = commandResults.Count == commandsToken.Count ? "completed" : "partial",
                rollbackPerformed = false,
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
