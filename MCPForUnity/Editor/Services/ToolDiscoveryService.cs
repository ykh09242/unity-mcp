using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    public class ToolDiscoveryService : IToolDiscoveryService, IToolDiscoveryDiagnostics
    {
        private Dictionary<string, ToolMetadata> _cachedTools;
        private ToolDiscoveryDiagnostics _diagnostics = new ToolDiscoveryDiagnostics();

        public List<ToolMetadata> DiscoverAllTools()
        {
            if (!ToolConsentThread.IsMainThread)
                return new List<ToolMetadata>();
            EnsureToolsDiscovered();
            return _cachedTools.Values.ToList();
        }

        private void EnsureToolsDiscovered()
        {
            if (_cachedTools != null)
            {
                return;
            }

            _cachedTools = new Dictionary<string, ToolMetadata>();

            // TypeCache is Unity's precomputed attribute index, rebuilt once per domain reload.
            // This used to query it and then ALSO materialise every type in every loaded assembly
            // and call GetCustomAttribute on each one, before merging the two sets — so the fast
            // path never actually saved anything (issue #1336). The result is cached, so the walk
            // ran once per domain reload, and again on each tool toggle in the editor window,
            // which invalidates the cache. A reload therefore paid for two full assembly walks:
            // this one and CommandRegistry.AutoDiscoverCommands, which #1364 removed. This brings
            // the tool registry in line with it, and with ResourceDiscoveryService, which has
            // always been TypeCache-only.
            //
            // Ordered because a duplicate tool name overwrites the previous registration below,
            // so registration order decides which one wins, and TypeCache documents no order.
            foreach (var type in InRegistrationOrder(TypeCache.GetTypesWithAttribute<McpForUnityToolAttribute>()))
            {
                McpForUnityToolAttribute toolAttr;
                try
                {
                    toolAttr = type.GetCustomAttribute<McpForUnityToolAttribute>();
                }
                catch (Exception ex)
                {
                    _diagnostics.Record(type, null, "rejected", "attribute_unreadable", "review_tool_declaration");
                    McpLog.Warn($"Failed to discover MCP tool {type.FullName}: {ex.Message}");
                    continue;
                }

                if (toolAttr == null)
                {
                    continue;
                }

                var metadata = ClassifyAndApply(
                    type,
                    toolAttr,
                    ex => McpLog.Warn($"Failed to discover MCP tool {type.FullName}: {ex.Message}"),
                    ex => McpLog.Error($"Failed to extract metadata for {type.Name}: {ex.Message}"),
                    name => McpLog.Warn($"Duplicate tool name '{name}' from {type.FullName}; overwriting previous registration.")
                );
                if (metadata != null)
                    EnsurePreferenceInitialized(metadata);
            }
            _diagnostics.Complete();

            McpLog.Info($"Discovered {_cachedTools.Count} MCP tools via reflection", false);
        }

        internal ToolMetadata ApplyDiagnosticCandidate(Type type, McpForUnityToolAttribute attribute) => ClassifyAndApply(type, attribute, null, null, null);

        private ToolMetadata ClassifyAndApply(
            Type type,
            McpForUnityToolAttribute attribute,
            Action<Exception> handlerError,
            Action<Exception> metadataError,
            Action<string> duplicate
        )
        {
            var name = string.IsNullOrEmpty(attribute.Name) ? ConvertToSnakeCase(type.Name) : attribute.Name;
            try
            {
                if (CommandRegistry.GetCommandMethod(type) == null)
                {
                    _diagnostics.Record(type, name, "rejected", "unsupported_handler", "fix_handler_signature");
                    return null;
                }
            }
            catch (Exception ex)
            {
                handlerError?.Invoke(ex);
                _diagnostics.Record(type, name, "rejected", "attribute_unreadable", "review_tool_declaration");
                return null;
            }
            var metadata = ExtractToolMetadataCore(type, attribute, metadataError);
            if (metadata == null)
            {
                _diagnostics.Record(type, name, "rejected", "metadata_invalid", "review_tool_declaration");
                return null;
            }
            if (_cachedTools.ContainsKey(metadata.Name))
            {
                _diagnostics.Shadow(metadata.Name);
                duplicate?.Invoke(metadata.Name);
            }
            _cachedTools[metadata.Name] = metadata;
            bool failed = !string.IsNullOrEmpty(metadata.SchemaError);
            _diagnostics.Record(
                type,
                metadata.Name,
                failed ? "rejected" : "discovered",
                failed ? "schema_or_default_invalid" : "none",
                failed ? "review_schema_and_defaults" : "none"
            );
            return metadata;
        }

        public JObject GetDiagnostics(string toolName = null, int limit = 64)
        {
            return GetDiagnosticsCore(toolName, limit, IsCachedToolEnabled);
        }

        private JObject GetDiagnosticsCore(string toolName, int limit, Func<string, ToolMetadata, bool> readEnablement)
        {
            if (!ToolDiscoveryDiagnostics.ValidName(toolName) || limit < 1 || limit > (toolName == null ? 64 : 16))
                throw new ArgumentException("Invalid diagnostic query");
            var cache = _cachedTools;
            var diagnostics = _diagnostics;
            var snapshot = diagnostics.Snapshot(toolName, limit);
            if (cache == null || !(bool)snapshot["complete"])
                return snapshot;
            foreach (var row in (JArray)snapshot["rows"])
            {
                var name = (string)row["tool_name"];
                if (name == null || (string)row["state"] != "discovered" || !cache.TryGetValue(name, out var metadata))
                    continue;
                if (!ReferenceEquals(cache, _cachedTools) || !ReferenceEquals(diagnostics, _diagnostics))
                    return new ToolDiscoveryDiagnostics().Snapshot(toolName, limit);
                bool enabled = readEnablement(name, metadata);
                if (!ReferenceEquals(cache, _cachedTools) || !ReferenceEquals(diagnostics, _diagnostics))
                    return new ToolDiscoveryDiagnostics().Snapshot(toolName, limit);
                row["native_enabled"] = enabled;
                if (!enabled)
                {
                    row["stage"] = "native_enablement";
                    row["state"] = "disabled";
                    row["reason"] = "native_disabled";
                    row["next_action"] = "review_tool_toggle";
                }
            }
            return snapshot;
        }

        /// <summary>
        /// Stable registration order for discovered tool types. FullName alone is not a total
        /// order: two assemblies can declare the same full type name, and if their tool names
        /// also collide the later registration wins, so the assembly identity breaks the tie.
        /// Without both keys a collision could resolve differently between domain reloads.
        /// </summary>
        internal static IEnumerable<Type> InRegistrationOrder(IEnumerable<Type> types)
        {
            return types.OrderBy(type => type.FullName, StringComparer.Ordinal).ThenBy(type => type.Assembly.FullName, StringComparer.Ordinal);
        }

        public ToolMetadata GetToolMetadata(string toolName)
        {
            if (!ToolConsentThread.IsMainThread)
                return null;
            EnsureToolsDiscovered();

            return _cachedTools.TryGetValue(toolName, out var metadata) ? metadata : null;
        }

        public List<ToolMetadata> GetEnabledTools()
        {
            if (!ToolConsentThread.IsMainThread)
                return new List<ToolMetadata>();
            EnsureToolsDiscovered();
            return _cachedTools.Values.Where(tool => IsToolEnabled(tool.Name)).ToList();
        }

        public bool IsToolEnabled(string toolName)
        {
            if (!ToolConsentThread.IsMainThread)
                return false;
            if (string.IsNullOrEmpty(toolName))
            {
                return false;
            }

            return IsCachedToolEnabled(toolName, GetToolMetadata(toolName));
        }

        private bool IsCachedToolEnabled(string toolName, ToolMetadata metadata)
        {
            if (!ToolConsentThread.IsMainThread)
                return false;
            if (metadata?.RequiresExplicitConsent == true)
                return MCPServiceLocator.ToolConsent.GetStatus(metadata.Name).Enabled;

            string key = GetToolPreferenceKey(toolName);
            if (EditorPrefs.HasKey(key))
            {
                return EditorPrefs.GetBool(key, true);
            }

            return metadata?.AutoRegister ?? false;
        }

        public void SetToolEnabled(string toolName, bool enabled)
        {
            if (!ToolConsentThread.IsMainThread)
                return;
            if (string.IsNullOrEmpty(toolName))
            {
                return;
            }

            var metadata = GetToolMetadata(toolName);
            if (metadata?.RequiresExplicitConsent == true)
            {
                MCPServiceLocator.ToolConsent.SetEnabled(metadata.Name, enabled);
                return;
            }
            EditorPrefs.SetBool(GetToolPreferenceKey(toolName), enabled);
        }

        private ToolMetadata ExtractToolMetadata(Type type, McpForUnityToolAttribute toolAttr) =>
            ExtractToolMetadataCore(type, toolAttr, ex => McpLog.Error($"Failed to extract metadata for {type.Name}: {ex.Message}"));

        private ToolMetadata ExtractToolMetadataCore(Type type, McpForUnityToolAttribute toolAttr, Action<Exception> reportError)
        {
            try
            {
                // Get tool name
                string toolName = toolAttr.Name;
                if (string.IsNullOrEmpty(toolName))
                {
                    // Match CommandRegistry's default command name exactly.
                    toolName = ConvertToSnakeCase(type.Name);
                }

                // Get description
                string description = toolAttr.Description ?? $"Tool: {toolName}";

                // Extract parameters
                var parameters = toolAttr.SchemaVersion == 0 ? ExtractParameters(type) : new List<ParameterMetadata>();

                var metadata = new ToolMetadata
                {
                    Name = toolName,
                    Description = description,
                    StructuredOutput = toolAttr.StructuredOutput,
                    Parameters = parameters,
                    ClassName = type.Name,
                    Namespace = type.Namespace ?? "",
                    AssemblyName = type.Assembly.GetName().Name,
                    AutoRegister = toolAttr.AutoRegister,
                    RequiresExplicitConsent = toolAttr.RequiresExplicitConsent,
                    RequiresPolling = toolAttr.RequiresPolling,
                    PollAction = string.IsNullOrEmpty(toolAttr.PollAction) ? "status" : toolAttr.PollAction,
                    MaxPollSeconds = toolAttr.MaxPollSeconds,
                    Group = toolAttr.Group ?? "core",
                    SchemaVersion = toolAttr.SchemaVersion,
                };

                metadata.IsBuiltIn = StringCaseUtility.IsBuiltInMcpType(type, metadata.AssemblyName, "MCPForUnity.Editor.Tools");

                if (toolAttr.SchemaVersion != 0 || toolAttr.OutputDataType != null || toolAttr.OutputAllowNull)
                {
                    try
                    {
                        ToolSchemaBuilder.Build(type, toolAttr, metadata);
                    }
                    catch (Exception)
                    {
                        // Keep the failed member so a refresh cannot turn it into an authoritative removal.
                        metadata.SchemaError = "Custom rich schema discovery failed.";
                        metadata.InputSchema = metadata.OutputDataSchema = null;
                    }
                }

                return metadata;
            }
            catch (Exception ex)
            {
                reportError?.Invoke(ex);
                return null;
            }
        }

        private List<ParameterMetadata> ExtractParameters(Type type)
        {
            var parameters = new List<ParameterMetadata>();

            // Look for nested Parameters class
            var parametersType = type.GetNestedType("Parameters");
            if (parametersType == null)
            {
                return parameters;
            }

            // Get all properties with [ToolParameter]
            var properties = parametersType.GetProperties(BindingFlags.Public | BindingFlags.Instance);

            foreach (var prop in properties)
            {
                var paramAttr = prop.GetCustomAttribute<ToolParameterAttribute>();
                if (paramAttr == null)
                    continue;

                string paramName = prop.Name;
                string paramType = GetParameterType(prop.PropertyType);

                parameters.Add(
                    new ParameterMetadata
                    {
                        Name = paramName,
                        Description = paramAttr.Description,
                        Type = paramType,
                        Required = paramAttr.Required,
                        DefaultValue = paramAttr.DefaultValue,
                    }
                );
            }

            // ToolParameter also supports fields. Keep an annotated property's metadata
            // when a derived field hides a property with the same parameter name.
            var parameterNames = new HashSet<string>(parameters.Select(parameter => parameter.Name));
            foreach (var field in parametersType.GetFields(BindingFlags.Public | BindingFlags.Instance))
            {
                var paramAttr = field.GetCustomAttribute<ToolParameterAttribute>();
                if (paramAttr == null || !parameterNames.Add(field.Name))
                    continue;

                parameters.Add(
                    new ParameterMetadata
                    {
                        Name = field.Name,
                        Description = paramAttr.Description,
                        Type = GetParameterType(field.FieldType),
                        Required = paramAttr.Required,
                        DefaultValue = paramAttr.DefaultValue,
                    }
                );
            }

            return parameters;
        }

        private string GetParameterType(Type type)
        {
            // Handle nullable types
            if (Nullable.GetUnderlyingType(type) != null)
            {
                type = Nullable.GetUnderlyingType(type);
            }

            // Map C# types to JSON schema types
            if (type == typeof(string))
                return "string";
            if (type == typeof(int) || type == typeof(long))
                return "integer";
            if (type == typeof(float) || type == typeof(double))
                return "number";
            if (type == typeof(bool))
                return "boolean";
            if (
                typeof(System.Collections.IDictionary).IsAssignableFrom(type)
                || type.GetInterfaces()
                    .Concat(new[] { type })
                    .Any(candidate =>
                        candidate.IsGenericType
                        && (
                            candidate.GetGenericTypeDefinition() == typeof(IDictionary<,>)
                            || candidate.GetGenericTypeDefinition() == typeof(IReadOnlyDictionary<,>)
                        )
                    )
            )
                return "object";
            if (type.IsArray || typeof(System.Collections.IEnumerable).IsAssignableFrom(type))
                return "array";

            return "object";
        }

        private string ConvertToSnakeCase(string input) => StringCaseUtility.ToSnakeCase(input);

        public void InvalidateCache()
        {
            _cachedTools = null;
            _diagnostics = new ToolDiscoveryDiagnostics();
        }

        private void EnsurePreferenceInitialized(ToolMetadata metadata)
        {
            if (metadata == null || string.IsNullOrEmpty(metadata.Name))
            {
                return;
            }

            // Missing scoped dangerous state means off. Merely discovering a
            // tool cannot mutate enablement or migrate an old global grant.
            if (metadata.RequiresExplicitConsent)
                return;
            string key = GetToolPreferenceKey(metadata.Name);
            if (!EditorPrefs.HasKey(key))
            {
                bool defaultValue = metadata.AutoRegister || metadata.IsBuiltIn;
                EditorPrefs.SetBool(key, defaultValue);
            }
        }

        private static string GetToolPreferenceKey(string toolName)
        {
            return EditorPrefKeys.ToolEnabledPrefix + toolName;
        }

        // Retained for legacy-pref inspection/tests. This key never grants v2 consent.
        internal static string GetConsentPreferenceKey(string toolName) => EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent." + toolName;
    }
}
