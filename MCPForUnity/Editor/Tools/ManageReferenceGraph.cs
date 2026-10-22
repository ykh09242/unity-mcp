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
        public static object HandleCommand(JObject parameters) => Handle(parameters, CancellationToken.None);

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken) =>
            Task.FromResult(Handle(parameters, cancellationToken));

        private static object Handle(JObject parameters, CancellationToken token)
        {
            try
            {
                token.ThrowIfCancellationRequested();
                string action = parameters?["action"]?.Type == JTokenType.String ? (string)parameters["action"] : null;
                string[] fields;
                switch (action)
                {
                    case "status":
                        fields = Array.Empty<string>();
                        break;
                    case "inventory":
                        fields = new[] { "offset", "snapshot", "page_size" };
                        break;
                    case "asset":
                        fields = new[] { "path", "guid" };
                        break;
                    case "dependencies":
                        fields = new[] { "guid", "offset", "snapshot", "page_size" };
                        break;
                    case "source":
                        fields = new[] { "guid", "offset", "fingerprint" };
                        break;
                    case "changes":
                        fields = new[] { "epoch", "after", "page_size" };
                        break;
                    case "compilation":
                        fields = new[] { "paths", "revision" };
                        break;
                    case "probe":
                        fields = new[] { "paths" };
                        break;
                    default:
                        throw new ArgumentException("Unknown reference collection action.");
                }
                var allowed = new HashSet<string>(fields) { "action", "expected_instance_id", "expected_project_id" };
                if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unsupported reference collection fields.");
                foreach (JProperty property in parameters.Properties())
                {
                    if (property.Name == "paths")
                    {
                        if (!(property.Value is JArray paths) || paths.Any(path => path.Type != JTokenType.String))
                            throw new ArgumentException("Collection paths must be an array of strings.");
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
                JObject data = action == "status" ? status : ReferenceGraphCollector.Handle(parameters);
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
                    error = "reference_collection_unavailable",
                    message = error.Message,
                };
            }
        }
    }
}
