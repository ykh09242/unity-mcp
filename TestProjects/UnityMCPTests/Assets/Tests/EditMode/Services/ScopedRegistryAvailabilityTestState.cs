using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;

namespace MCPForUnityTests.Editor.Services
{
    // Enable only owned non-consent fixtures in memory. Actual handler registration,
    // canonical identity and metadata remain unchanged; no EditorPrefs are mutated.
    internal sealed class ScopedRegistryAvailabilityTestState : IDisposable
    {
        private static readonly FieldInfo ToolsField = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private static readonly FieldInfo ResourcesField = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private readonly object previousTools;
        private readonly object previousResources;

        internal ScopedRegistryAvailabilityTestState(string[] tools, string[] resources = null)
        {
            previousTools = ToolsField.GetValue(null);
            previousResources = ResourcesField.GetValue(null);
            var actualTools = MCPServiceLocator.ToolDiscovery;
            var actualResources = MCPServiceLocator.ResourceDiscovery;
            foreach (string name in tools)
                if (actualTools.GetToolMetadata(name)?.RequiresExplicitConsent == true)
                    throw new InvalidOperationException("Explicit consent tests must use a scoped consent service.");
            ToolsField.SetValue(null, new ToolView(actualTools, tools));
            ResourcesField.SetValue(null, new ResourceView(actualResources, resources ?? Array.Empty<string>()));
        }

        public void Dispose()
        {
            ToolsField.SetValue(null, previousTools);
            ResourcesField.SetValue(null, previousResources);
        }

        private sealed class ToolView : IToolDiscoveryService
        {
            private readonly IToolDiscoveryService actual;
            private readonly HashSet<string> enabled;

            internal ToolView(IToolDiscoveryService actual, string[] enabled)
            {
                this.actual = actual;
                this.enabled = new HashSet<string>(enabled, StringComparer.Ordinal);
            }

            public List<ToolMetadata> DiscoverAllTools() => actual.DiscoverAllTools();

            public ToolMetadata GetToolMetadata(string name) => actual.GetToolMetadata(name);

            public List<ToolMetadata> GetEnabledTools() => DiscoverAllTools().Where(tool => IsToolEnabled(tool.Name)).ToList();

            public bool IsToolEnabled(string name) => ToolConsentThread.IsMainThread && (enabled.Contains(name) || actual.IsToolEnabled(name));

            public void SetToolEnabled(string name, bool value) => throw new InvalidOperationException("The owned test scope does not mutate preferences.");

            public void InvalidateCache() => actual.InvalidateCache();
        }

        private sealed class ResourceView : IResourceDiscoveryService
        {
            private readonly IResourceDiscoveryService actual;
            private readonly HashSet<string> enabled;

            internal ResourceView(IResourceDiscoveryService actual, string[] enabled)
            {
                this.actual = actual;
                this.enabled = new HashSet<string>(enabled, StringComparer.Ordinal);
            }

            public List<ResourceMetadata> DiscoverAllResources() => actual.DiscoverAllResources();

            public ResourceMetadata GetResourceMetadata(string name) => actual.GetResourceMetadata(name);

            public List<ResourceMetadata> GetEnabledResources() => DiscoverAllResources().Where(resource => IsResourceEnabled(resource.Name)).ToList();

            public bool IsResourceEnabled(string name) => ToolConsentThread.IsMainThread && (enabled.Contains(name) || actual.IsResourceEnabled(name));

            public void SetResourceEnabled(string name, bool value) => throw new InvalidOperationException("The owned test scope does not mutate preferences.");

            public void InvalidateCache() => actual.InvalidateCache();
        }
    }
}
