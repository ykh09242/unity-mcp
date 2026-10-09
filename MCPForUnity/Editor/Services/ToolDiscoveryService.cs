using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    public class ToolDiscoveryService : IToolDiscoveryService
    {
        private Dictionary<string, ToolMetadata> _cachedTools;

        public List<ToolMetadata> DiscoverAllTools()
        {
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
                    if (CommandRegistry.GetCommandMethod(type) == null)
                        continue;
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Failed to discover MCP tool {type.FullName}: {ex.Message}");
                    continue;
                }

                if (toolAttr == null)
                {
                    continue;
                }

                var metadata = ExtractToolMetadata(type, toolAttr);
                if (metadata != null)
                {
                    if (_cachedTools.ContainsKey(metadata.Name))
                    {
                        McpLog.Warn($"Duplicate tool name '{metadata.Name}' from {type.FullName}; overwriting previous registration.");
                    }
                    _cachedTools[metadata.Name] = metadata;
                    EnsurePreferenceInitialized(metadata);
                }
            }

            McpLog.Info($"Discovered {_cachedTools.Count} MCP tools via reflection", false);
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
            EnsureToolsDiscovered();

            return _cachedTools.TryGetValue(toolName, out var metadata) ? metadata : null;
        }

        public List<ToolMetadata> GetEnabledTools()
        {
            EnsureToolsDiscovered();
            return _cachedTools.Values.Where(tool => IsToolEnabled(tool.Name)).ToList();
        }

        public bool IsToolEnabled(string toolName)
        {
            if (string.IsNullOrEmpty(toolName))
            {
                return false;
            }

            var metadata = GetToolMetadata(toolName);
            if (metadata?.RequiresExplicitConsent == true && !EditorPrefs.GetBool(GetConsentPreferenceKey(toolName), false))
                return false;

            string key = GetToolPreferenceKey(toolName);
            if (EditorPrefs.HasKey(key))
            {
                return EditorPrefs.GetBool(key, true);
            }

            return metadata?.AutoRegister ?? false;
        }

        public void SetToolEnabled(string toolName, bool enabled)
        {
            if (string.IsNullOrEmpty(toolName))
            {
                return;
            }

            string key = GetToolPreferenceKey(toolName);
            if (GetToolMetadata(toolName)?.RequiresExplicitConsent == true)
                EditorPrefs.SetBool(GetConsentPreferenceKey(toolName), enabled);
            EditorPrefs.SetBool(key, enabled);
        }

        private ToolMetadata ExtractToolMetadata(Type type, McpForUnityToolAttribute toolAttr)
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
                var parameters = ExtractParameters(type);

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
                };

                metadata.IsBuiltIn = StringCaseUtility.IsBuiltInMcpType(type, metadata.AssemblyName, "MCPForUnity.Editor.Tools");

                return metadata;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to extract metadata for {type.Name}: {ex.Message}");
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
        }

        private void EnsurePreferenceInitialized(ToolMetadata metadata)
        {
            if (metadata == null || string.IsNullOrEmpty(metadata.Name))
            {
                return;
            }

            string key = GetToolPreferenceKey(metadata.Name);
            if (metadata.RequiresExplicitConsent && !EditorPrefs.GetBool(GetConsentPreferenceKey(metadata.Name), false))
            {
                // Old versions initialized built-in tools to true without recording consent.
                EditorPrefs.SetBool(key, false);
                return;
            }
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

        internal static string GetConsentPreferenceKey(string toolName) => EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent." + toolName;
    }
}
