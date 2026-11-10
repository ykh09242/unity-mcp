using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "manage_reference_graph",
        AutoRegister = false,
        Group = "assets",
        DisplayName = "Reference Graph",
        Description = "Collect read-only reference evidence for the current Editor. User graphs and grants are managed by the MCP server."
    )]
    public static class ManageReferenceGraph
    {
        public static object HandleCommand(JObject parameters) => Handle(parameters, CancellationToken.None, false).GetAwaiter().GetResult();

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return Handle(parameters, cancellationToken, true);
        }

        private static async Task<object> Handle(JObject parameters, CancellationToken token, bool staged)
        {
            try
            {
                token.ThrowIfCancellationRequested();
                string action = parameters?["action"]?.Type == JTokenType.String ? (string)parameters["action"] : null;
                string[] fields;
                switch (action)
                {
                    case "status":
                        fields = new[] { "validate_ignore_current" };
                        break;
                    case "inventory":
                        fields = new[] { "offset", "snapshot", "page_size" };
                        break;
                    case "asset":
                        fields = new[] { "path", "guid" };
                        break;
                    case "dependencies":
                        fields = new[] { "guid", "offset", "snapshot", "page_size", "asset_guard" };
                        break;
                    case "source":
                        fields = new[] { "guid", "offset", "fingerprint", "asset_guard" };
                        break;
                    case "ignore_rules":
                        fields = new[] { "offset", "snapshot", "page_size", "validate" };
                        break;
                    case "ignore_source":
                        fields = new[] { "snapshot", "path", "offset", "fingerprint" };
                        break;
                    case "changes":
                        fields = new[] { "epoch", "after", "page_size" };
                        break;
                    case "compilation":
                        fields = new[] { "paths", "revision" };
                        break;
                    case "probe":
                        fields = new[] { "paths", "static_proof" };
                        break;
                    case "copy_destination":
                        fields = new[] { "path" };
                        break;
                    case "runtime_start":
                    case "runtime_stop":
                    case "runtime_release":
                        fields = new[] { "token" };
                        break;
                    case "runtime_read":
                        fields = new[] { "token", "offset", "page_size" };
                        break;
                    default:
                        throw new ArgumentException("Unknown reference collection action.");
                }
                var allowed = new HashSet<string>(fields) { "action", "expected_instance_id", "expected_project_id" };
                if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unsupported reference collection fields.");
                foreach (JProperty property in parameters.Properties())
                {
                    if (property.Name == "asset_guard")
                    {
                        if (
                            !(property.Value is JObject guard)
                            || guard.Count != 4
                            || guard["path"]?.Type != JTokenType.String
                            || guard["epoch"]?.Type != JTokenType.String
                            || guard["revision"]?.Type != JTokenType.Integer
                            || (guard["ignore_digest"]?.Type != JTokenType.String && guard["ignore_digest"]?.Type != JTokenType.Null)
                        )
                            throw new ArgumentException("Invalid asset collection guard.");
                        continue;
                    }
                    if (property.Name == "paths")
                    {
                        if (!(property.Value is JArray paths) || paths.Any(path => path.Type != JTokenType.String))
                            throw new ArgumentException("Collection paths must be an array of strings.");
                        continue;
                    }
                    if (property.Name == "validate" || property.Name == "static_proof" || property.Name == "validate_ignore_current")
                    {
                        if (property.Value.Type != JTokenType.Boolean)
                            throw new ArgumentException(property.Name + " requires a boolean.");
                        continue;
                    }
                    bool number = property.Name == "offset" || property.Name == "after" || property.Name == "page_size";
                    if (property.Value.Type != (number ? JTokenType.Integer : JTokenType.String))
                        throw new ArgumentException("Reference fields require their exact string or integer type.");
                }
                if (action == "asset" && (parameters.ContainsKey("path") == parameters.ContainsKey("guid")))
                    throw new ArgumentException("Asset resolution requires exactly one path or GUID.");
                JObject status = ReferenceGraphCollector.Handle(new JObject { ["action"] = "status" });
                string instance = status.Value<string>("instance_id"),
                    project = status.Value<string>("project_id");
                if (action != "status" || parameters.ContainsKey("expected_instance_id") || parameters.ContainsKey("expected_project_id"))
                {
                    if (parameters.Value<string>("expected_instance_id") != instance || parameters.Value<string>("expected_project_id") != project)
                        return new
                        {
                            success = false,
                            error = "reference_scope_changed",
                            message = "The selected Editor instance or project changed.",
                        };
                }
                token.ThrowIfCancellationRequested();
                JObject data =
                    action == "status" && parameters.Value<bool?>("validate_ignore_current") == true
                        ? staged
                            ? await ReferenceGraphCollector.HandleWithStatusAsync(parameters, status, token)
                            : ReferenceGraphCollector.HandleWithStatus(parameters, status)
                        : action == "status"
                            ? status
                            : action.StartsWith("runtime_", StringComparison.Ordinal)
                                ? ReferenceGraphRuntimeCapture.Handle(parameters)
                                : staged
                                    ? await ReferenceGraphCollector.HandleAsync(parameters, token)
                                    : ReferenceGraphCollector.Handle(parameters);
                data["instance_id"] = instance;
                data["project_id"] = project;
                return new SuccessResponse("Reference evidence collected", data);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception error)
            {
                return new
                {
                    success = false,
                    error = error is ReferenceGraphCollector.InvalidInventorySnapshotException
                        ? "reference_inventory_snapshot_invalid"
                        : "reference_collection_unavailable",
                    message = parameters?["action"]?.Type == JTokenType.String
                    && ((string)parameters["action"]).StartsWith("runtime_", StringComparison.Ordinal)
                        ? "Runtime capture unavailable (" + error.GetType().Name + "). Inspect capture status, then retry or release it."
                    : parameters?["action"]?.Type == JTokenType.String && ((string)parameters["action"]).StartsWith("ignore_", StringComparison.Ordinal)
                        ? "Ignore evidence unavailable (" + error.GetType().Name + "). Refresh rule evidence and retry collection."
                    : error.Message,
                };
            }
        }
    }
}
