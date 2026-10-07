using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.Resources
{
    /// <summary>
    /// Controller for the Resources section inside the MCP for Unity editor window.
    /// Provides discovery, filtering, and per-resource enablement toggles.
    /// </summary>
    public class McpResourcesSection
    {
        private readonly Dictionary<string, Toggle> resourceToggleMap = new();
        private Label summaryLabel;
        private Label noteLabel;
        private TextField searchField;
        private Label visibleCountLabel;
        private Label emptyStateLabel;
        private Button enableAllButton;
        private Button disableAllButton;
        private Button rescanButton;
        private VisualElement categoryContainer;
        private List<ResourceMetadata> allResources = new();
        private readonly Dictionary<string, VisualElement> resourceRowMap = new();
        private readonly List<(Foldout foldout, List<ResourceMetadata> resources)> foldoutEntries = new();
        private readonly Dictionary<Foldout, bool> foldoutStatesBeforeSearch = new();

        public VisualElement Root { get; }

        public McpResourcesSection(VisualElement root)
        {
            Root = root;
            CacheUIElements();
            RegisterCallbacks();
        }

        private void CacheUIElements()
        {
            summaryLabel = Root.Q<Label>("resources-summary");
            noteLabel = Root.Q<Label>("resources-note");
            searchField = Root.Q<TextField>("resources-search");
            visibleCountLabel = Root.Q<Label>("resources-visible-count");
            emptyStateLabel = Root.Q<Label>("resources-empty-state");
            enableAllButton = Root.Q<Button>("enable-all-resources-button");
            disableAllButton = Root.Q<Button>("disable-all-resources-button");
            rescanButton = Root.Q<Button>("rescan-resources-button");
            categoryContainer = Root.Q<VisualElement>("resource-category-container");
        }

        private void RegisterCallbacks()
        {
            searchField?.RegisterValueChangedCallback(evt => ApplySearch());

            if (enableAllButton != null)
            {
                enableAllButton.AddToClassList("tool-action-button");
                enableAllButton.clicked += () => SetAllResourcesState(true);
            }

            if (disableAllButton != null)
            {
                disableAllButton.AddToClassList("tool-action-button");
                disableAllButton.clicked += () => SetAllResourcesState(false);
            }

            if (rescanButton != null)
            {
                rescanButton.AddToClassList("tool-action-button");
                rescanButton.clicked += () =>
                {
                    McpLog.Info("Rescanning MCP resources from the editor window.");
                    MCPServiceLocator.ResourceDiscovery.InvalidateCache();
                    Refresh();
                };
            }
        }

        /// <summary>
        /// Rebuilds the resource list and synchronises toggle states.
        /// </summary>
        public void Refresh()
        {
            resourceToggleMap.Clear();
            resourceRowMap.Clear();
            foldoutEntries.Clear();
            foldoutStatesBeforeSearch.Clear();
            categoryContainer?.Clear();

            var service = MCPServiceLocator.ResourceDiscovery;
            allResources = service.DiscoverAllResources().OrderBy(r => r.IsBuiltIn ? 0 : 1).ThenBy(r => r.Name, StringComparer.OrdinalIgnoreCase).ToList();

            bool hasResources = allResources.Count > 0;
            enableAllButton?.SetEnabled(hasResources);
            disableAllButton?.SetEnabled(hasResources);

            if (noteLabel != null)
            {
                noteLabel.EnableInClassList("catalog-hidden", !hasResources);
            }

            if (!hasResources)
            {
                UpdateSummary();
                ApplySearch();
                return;
            }

            BuildCategory("Built-in Resources", "built-in", allResources.Where(r => r.IsBuiltIn));

            var customResources = allResources.Where(r => !r.IsBuiltIn).ToList();
            if (customResources.Count > 0)
            {
                BuildCategory("Custom Resources", "custom", customResources);
            }
            UpdateSummary();
            ApplySearch();
        }

        private void BuildCategory(string title, string prefsSuffix, IEnumerable<ResourceMetadata> resources)
        {
            var resourceList = resources.ToList();
            if (resourceList.Count == 0)
            {
                return;
            }

            var foldout = new Foldout
            {
                text = $"{title} ({resourceList.Count})",
                value = EditorPrefs.GetBool(EditorPrefKeys.ResourceFoldoutStatePrefix + prefsSuffix, true),
            };
            foldout.AddToClassList("catalog-category");

            foldout.RegisterValueChangedCallback(evt =>
            {
                if (evt.target != foldout || !string.IsNullOrWhiteSpace(searchField?.value))
                    return;
                EditorPrefs.SetBool(EditorPrefKeys.ResourceFoldoutStatePrefix + prefsSuffix, evt.newValue);
            });

            foreach (var resource in resourceList)
            {
                foldout.Add(CreateResourceRow(resource));
            }

            foldoutEntries.Add((foldout, resourceList));
            categoryContainer?.Add(foldout);
        }

        private VisualElement CreateResourceRow(ResourceMetadata resource)
        {
            var row = new VisualElement();
            row.AddToClassList("tool-item");
            resourceRowMap[resource.Name] = row;

            var header = new VisualElement();
            header.AddToClassList("tool-item-header");

            var toggle = new Toggle(resource.Name) { value = MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(resource.Name) };
            toggle.AddToClassList("tool-item-toggle");
            toggle.tooltip = string.IsNullOrWhiteSpace(resource.Description) ? resource.Name : resource.Description;

            toggle.RegisterValueChangedCallback(evt =>
            {
                HandleToggleChange(resource, evt.newValue);
            });

            resourceToggleMap[resource.Name] = toggle;
            header.Add(toggle);

            var tagsContainer = new VisualElement();
            tagsContainer.AddToClassList("tool-tags");

            tagsContainer.Add(CreateTag(resource.IsBuiltIn ? "Built-in" : "Custom"));

            header.Add(tagsContainer);
            row.Add(header);

            if (!string.IsNullOrWhiteSpace(resource.Description) && !resource.Description.StartsWith("Resource:", StringComparison.Ordinal))
            {
                var description = new Label(resource.Description);
                description.AddToClassList("tool-item-description");
                row.Add(description);
            }

            return row;
        }

        private void HandleToggleChange(ResourceMetadata resource, bool enabled, bool updateSummary = true)
        {
            MCPServiceLocator.ResourceDiscovery.SetResourceEnabled(resource.Name, enabled);

            if (updateSummary)
            {
                UpdateSummary();
            }
        }

        private void SetAllResourcesState(bool enabled)
        {
            foreach (var resource in allResources)
            {
                bool currentEnabled = MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(resource.Name);
                if (resourceToggleMap.TryGetValue(resource.Name, out var toggle))
                    toggle.SetValueWithoutNotify(enabled);

                if (currentEnabled == enabled)
                {
                    continue;
                }

                HandleToggleChange(resource, enabled, updateSummary: false);
            }

            UpdateSummary();
        }

        private void UpdateSummary()
        {
            if (summaryLabel == null)
            {
                return;
            }

            if (allResources.Count == 0)
            {
                summaryLabel.text = "No MCP resources discovered.";
                return;
            }

            int enabledCount = allResources.Count(r => MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(r.Name));
            summaryLabel.text = $"{enabledCount} of {allResources.Count} resources enabled.";
        }

        private void ApplySearch()
        {
            string query = (searchField?.value ?? string.Empty).Trim();
            bool searching = query.Length > 0;
            int visibleCount = 0;

            foreach (var (foldout, resources) in foldoutEntries)
            {
                int groupVisibleCount = 0;
                foreach (var resource in resources)
                {
                    bool matches = MatchesSearch(resource, query);
                    if (resourceRowMap.TryGetValue(resource.Name, out var row))
                        row.EnableInClassList("catalog-hidden", !matches);
                    if (matches)
                        groupVisibleCount++;
                }

                visibleCount += groupVisibleCount;
                foldout.EnableInClassList("catalog-hidden", groupVisibleCount == 0);
                if (searching)
                {
                    if (!foldoutStatesBeforeSearch.ContainsKey(foldout))
                        foldoutStatesBeforeSearch[foldout] = foldout.value;
                    foldout.SetValueWithoutNotify(true);
                }
                else if (foldoutStatesBeforeSearch.TryGetValue(foldout, out var expanded))
                {
                    foldout.SetValueWithoutNotify(expanded);
                }
            }

            if (!searching)
                foldoutStatesBeforeSearch.Clear();
            if (visibleCountLabel != null)
                visibleCountLabel.text = $"{visibleCount} / {allResources.Count} shown";
            if (emptyStateLabel != null)
            {
                emptyStateLabel.text = allResources.Count == 0 ? "No resources discovered." : "No resources match your search.";
                emptyStateLabel.EnableInClassList("catalog-hidden", visibleCount > 0);
            }
        }

        private static bool MatchesSearch(ResourceMetadata resource, string query)
        {
            if (resource == null)
                return false;
            query = (query ?? string.Empty).Trim();
            if (query.Length == 0)
                return true;
            string category = resource.IsBuiltIn ? "Built-in Resources" : "Custom Resources";
            return (resource.Name ?? string.Empty).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                || (resource.Description ?? string.Empty).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                || category.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static Label CreateTag(string text)
        {
            var tag = new Label(text);
            tag.AddToClassList("tool-tag");
            return tag;
        }
    }
}
