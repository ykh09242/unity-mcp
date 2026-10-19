using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.Tools
{
    /// <summary>
    /// Controller for the Tools section inside the MCP for Unity editor window.
    /// Provides discovery, filtering, and per-tool enablement toggles.
    /// Tools are grouped by their Group property (core first, then alphabetical).
    /// </summary>
    public class McpToolsSection
    {
        private readonly Dictionary<string, Toggle> toolToggleMap = new();
        private Toggle projectScopedToolsToggle;
        private Label summaryLabel;
        private Label noteLabel;
        private Label applyStatusLabel;
        private Button retryToolsButton;
        private readonly IToolSettingsStatusSource providedStatusSource;
        private readonly bool? httpMode;
        private readonly Func<ToolMetadata, ToolConsentScope?> consentScopePrompt;
        private readonly Dictionary<string, Label> consentScopeLabels = new();
        private readonly Dictionary<string, string> consentPersistenceWarnings = new();
        private readonly SynchronizationContext uiContext;
        private readonly int uiThread;
        private IToolSettingsStatusSource statusSource;
        private bool statusAttached = true;
        private int statusRenderQueued;
        private TextField searchField;
        private Label visibleCountLabel;
        private Label emptyStateLabel;
        private Button enableAllButton;
        private Button disableAllButton;
        private Button rescanButton;
        private Button reconfigureButton;
        private VisualElement categoryContainer;
        private List<ToolMetadata> allTools = new();
        private readonly Dictionary<string, Toggle> groupToggleMap = new();
        private readonly List<(Foldout foldout, string title, List<ToolMetadata> tools)> foldoutEntries = new();
        private readonly Dictionary<string, VisualElement> toolRowMap = new();
        private readonly Dictionary<Foldout, bool> foldoutStatesBeforeSearch = new();

        /// <summary>Human-friendly names for tool groups shown in the UI.</summary>
        private static readonly Dictionary<string, string> GroupDisplayNames = new(StringComparer.OrdinalIgnoreCase)
        {
            { "core", "Core Tools" },
            { "vfx", "VFX & Shaders" },
            { "animation", "Animation" },
            { "ui", "UI Toolkit" },
            { "scripting_ext", "Scripting Extensions" },
            { "testing", "Testing" },
            { "probuilder", "ProBuilder — Experimental" },
            { "profiling", "Profiling & Frame Debugger" },
            { "asset_gen", "Asset Gen" },
        };

        public VisualElement Root { get; }

        public McpToolsSection(VisualElement root)
            : this(root, null, null, null) { }

        public McpToolsSection(VisualElement root, IToolSettingsStatusSource source, bool useHttp)
            : this(root, source, (bool?)useHttp, null) { }

        internal McpToolsSection(VisualElement root, IToolSettingsStatusSource source, bool useHttp, Func<ToolMetadata, ToolConsentScope?> prompt)
            : this(root, source, (bool?)useHttp, prompt) { }

        private McpToolsSection(VisualElement root, IToolSettingsStatusSource source, bool? useHttp, Func<ToolMetadata, ToolConsentScope?> prompt)
        {
            Root = root;
            providedStatusSource = source;
            httpMode = useHttp;
            consentScopePrompt = prompt ?? SelectConsentScope;
            uiContext = SynchronizationContext.Current;
            uiThread = Thread.CurrentThread.ManagedThreadId;
            CacheUIElements();
            RegisterCallbacks();
            Root.RegisterCallback<AttachToPanelEvent>(_ =>
            {
                statusAttached = true;
                BindStatusSource();
            });
            Root.RegisterCallback<DetachFromPanelEvent>(_ =>
            {
                statusAttached = false;
                UnbindStatusSource();
            });
            BindStatusSource();
        }

        private void CacheUIElements()
        {
            projectScopedToolsToggle = Root.Q<Toggle>("project-scoped-tools-toggle");
            summaryLabel = Root.Q<Label>("tools-summary");
            noteLabel = Root.Q<Label>("tools-note");
            applyStatusLabel = Root.Q<Label>("tools-apply-status");
            retryToolsButton = Root.Q<Button>("retry-tools-button");
            searchField = Root.Q<TextField>("tools-search");
            visibleCountLabel = Root.Q<Label>("tools-visible-count");
            emptyStateLabel = Root.Q<Label>("tools-empty-state");
            enableAllButton = Root.Q<Button>("enable-all-button");
            disableAllButton = Root.Q<Button>("disable-all-button");
            rescanButton = Root.Q<Button>("rescan-button");
            reconfigureButton = Root.Q<Button>("reconfigure-button");
            categoryContainer = Root.Q<VisualElement>("tool-category-container");
        }

        private void RegisterCallbacks()
        {
            searchField?.RegisterValueChangedCallback(evt => ApplySearch());
            if (retryToolsButton != null)
                retryToolsButton.clicked += () => _ = RetryToolSettingsAsync();

            if (projectScopedToolsToggle != null)
            {
                projectScopedToolsToggle.value = EditorPrefs.GetBool(EditorPrefKeys.ProjectScopedToolsLocalHttp, false);
                projectScopedToolsToggle.tooltip =
                    "When enabled, register project-scoped tools with HTTP Local and stdio transports. Allows per-project tool customization.";
                projectScopedToolsToggle.RegisterValueChangedCallback(evt =>
                {
                    EditorPrefs.SetBool(EditorPrefKeys.ProjectScopedToolsLocalHttp, evt.newValue);
                });
            }

            if (enableAllButton != null)
            {
                enableAllButton.AddToClassList("tool-action-button");
                enableAllButton.clicked += () => SetAllToolsState(true);
            }

            if (disableAllButton != null)
            {
                disableAllButton.AddToClassList("tool-action-button");
                disableAllButton.clicked += () => SetAllToolsState(false);
            }

            if (rescanButton != null)
            {
                rescanButton.AddToClassList("tool-action-button");
                rescanButton.clicked += () =>
                {
                    McpLog.Info("Rescanning MCP tools from the editor window.");
                    MCPServiceLocator.ToolDiscovery.InvalidateCache();
                    Refresh();
                };
            }

            if (reconfigureButton != null)
            {
                reconfigureButton.AddToClassList("tool-action-button");
                reconfigureButton.clicked += OnReconfigureClientsClicked;
            }
        }

        /// <summary>
        /// Rebuilds the tool list and synchronises toggle states.
        /// Tools are displayed in group-based foldouts: core first, then other
        /// groups alphabetically. Custom (non-built-in) tools appear in a
        /// separate "Custom Tools" foldout at the bottom.
        /// </summary>
        public void Refresh()
        {
            BindStatusSource();
            toolToggleMap.Clear();
            consentScopeLabels.Clear();
            groupToggleMap.Clear();
            foldoutEntries.Clear();
            toolRowMap.Clear();
            foldoutStatesBeforeSearch.Clear();
            categoryContainer?.Clear();

            var service = MCPServiceLocator.ToolDiscovery;
            allTools = service.DiscoverAllTools().OrderBy(tool => tool.Name, StringComparer.OrdinalIgnoreCase).ToList();

            bool hasTools = allTools.Count > 0;
            enableAllButton?.SetEnabled(hasTools);
            disableAllButton?.SetEnabled(hasTools);

            if (noteLabel != null)
            {
                noteLabel.EnableInClassList("catalog-hidden", !hasTools);
                if (hasTools)
                {
                    bool isHttp = EditorConfigurationCache.Instance.UseHttpTransport;
                    noteLabel.text = isHttp
                        ? "Changes apply after reconnecting or re-registering tools."
                        : "Stdio mode: toggles sync at startup. After changing toggles, ask the AI to run manage_tools with action 'sync' to refresh.";
                    if (consentPersistenceWarnings.Count > 0)
                        noteLabel.text = string.Join("\n", consentPersistenceWarnings.Values);
                }
            }

            if (!hasTools)
            {
                UpdateSummary();
                ApplySearch();
                return;
            }

            // Partition into built-in and custom
            var builtInTools = allTools.Where(IsBuiltIn).ToList();
            var customTools = allTools.Where(tool => !IsBuiltIn(tool)).ToList();

            // Group built-in tools by their Group property
            var grouped = builtInTools
                .GroupBy(t => t.Group ?? "core")
                .ToDictionary(g => g.Key, g => g.OrderBy(t => t.Name, StringComparer.OrdinalIgnoreCase).ToList());

            // Render "core" first, then remaining groups alphabetically
            if (grouped.TryGetValue("core", out var coreTools))
            {
                BuildCategory(GetGroupDisplayName("core"), "group-core", coreTools);
                grouped.Remove("core");
            }

            foreach (var kvp in grouped.OrderBy(k => k.Key, StringComparer.OrdinalIgnoreCase))
            {
                BuildCategory(GetGroupDisplayName(kvp.Key), $"group-{kvp.Key}", kvp.Value);
            }

            // Custom tools at the bottom
            if (customTools.Count > 0)
            {
                BuildCategory("Custom Tools", "custom", customTools);
            }

            UpdateSummary();
            ApplySearch();
        }

        private static string GetGroupDisplayName(string group)
        {
            if (GroupDisplayNames.TryGetValue(group, out var displayName))
                return displayName;
            // Fallback: capitalize first letter
            return string.IsNullOrEmpty(group) ? "Other" : char.ToUpper(group[0]) + group.Substring(1);
        }

        private void BuildCategory(string title, string prefsSuffix, IEnumerable<ToolMetadata> tools)
        {
            var toolList = tools.ToList();
            if (toolList.Count == 0)
            {
                return;
            }

            bool isExperimental = string.Equals(prefsSuffix, "group-probuilder", StringComparison.OrdinalIgnoreCase);

            int enabledCount = toolList.Count(t => MCPServiceLocator.ToolDiscovery.IsToolEnabled(t.Name));

            // Default foldout state: core is open, others collapsed
            bool defaultOpen = prefsSuffix == "group-core";
            var foldout = new Foldout
            {
                text = $"{title} ({enabledCount}/{toolList.Count})",
                value = EditorPrefs.GetBool(EditorPrefKeys.ToolFoldoutStatePrefix + prefsSuffix, defaultOpen),
            };
            foldout.AddToClassList("catalog-category");

            foldout.RegisterValueChangedCallback(evt =>
            {
                if (evt.target != foldout || !string.IsNullOrWhiteSpace(searchField?.value))
                    return;
                EditorPrefs.SetBool(EditorPrefKeys.ToolFoldoutStatePrefix + prefsSuffix, evt.newValue);
            });

            // Add a checkbox into the foldout header to toggle all tools in this group
            bool allEnabled = enabledCount == toolList.Count;
            var groupCheckbox = new Toggle { value = allEnabled };
            groupCheckbox.AddToClassList("group-header-checkbox");
            groupCheckbox.tooltip = $"Toggle all tools in \"{title}\" on or off.";

            // Prevent the click from propagating to the foldout expand/collapse toggle
            groupCheckbox.RegisterCallback<ClickEvent>(evt => evt.StopPropagation());
            groupCheckbox.RegisterValueChangedCallback(evt =>
            {
                evt.StopPropagation();
                SetGroupToolsState(toolList, evt.newValue, foldout, title);
            });

            // Insert the checkbox into the foldout's own header toggle element
            foldout.Q<Toggle>()?.Add(groupCheckbox);
            groupToggleMap[prefsSuffix] = groupCheckbox;

            foreach (var tool in toolList)
            {
                foldout.Add(CreateToolRow(tool));
            }

            if (isExperimental)
            {
                var warning = new HelpBox(
                    "ProBuilder support is experimental. Mesh editing operations may produce "
                        + "unexpected results on complex topologies. Always save your scene before "
                        + "performing destructive operations.",
                    HelpBoxMessageType.Warning
                );
                warning.AddToClassList("catalog-group-warning");
                foldout.Insert(0, warning);
            }

            foldoutEntries.Add((foldout, title, toolList));
            categoryContainer?.Add(foldout);
        }

        private VisualElement CreateToolRow(ToolMetadata tool)
        {
            var row = new VisualElement();
            row.AddToClassList("tool-item");
            toolRowMap[tool.Name] = row;

            var header = new VisualElement();
            header.AddToClassList("tool-item-header");

            var toggle = new Toggle(tool.Name) { value = MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name) };
            toggle.AddToClassList("tool-item-toggle");
            toggle.tooltip = string.IsNullOrWhiteSpace(tool.Description) ? tool.Name : tool.Description;

            toggle.RegisterValueChangedCallback(evt =>
            {
                HandleToggleChange(tool, evt.newValue);
            });

            toolToggleMap[tool.Name] = toggle;
            header.Add(toggle);

            var tagsContainer = new VisualElement();
            tagsContainer.AddToClassList("tool-tags");

            bool defaultEnabled = !tool.RequiresExplicitConsent && (tool.AutoRegister || tool.IsBuiltIn);
            tagsContainer.Add(CreateTag(defaultEnabled ? "On by default" : "Off by default"));

            tagsContainer.Add(CreateTag(tool.StructuredOutput ? "Structured output" : "Free-form"));

            if (tool.RequiresPolling)
            {
                tagsContainer.Add(CreateTag($"Polling: {tool.PollAction}"));
            }

            header.Add(tagsContainer);
            row.Add(header);

            if (tool.RequiresExplicitConsent)
            {
                var scope = new Label { name = "consent-scope-" + tool.Name };
                consentScopeLabels[tool.Name] = scope;
                row.Add(scope);
                row.Add(new Button(() => ChangeConsentScope(tool)) { text = "Choose consent scope", name = "consent-change-" + tool.Name });
                row.Add(
                    new Button(() =>
                    {
                        var before = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
                        var result = MCPServiceLocator.ToolConsent.Revoke(tool.Name);
                        ShowConsentResult(tool.Name, result);
                        RefreshConsentRow(tool);
                        ConsentChanged(tool, before);
                    })
                    {
                        text = "Revoke consent",
                        name = "consent-revoke-" + tool.Name,
                    }
                );
                RefreshConsentRow(tool);
            }

            // Skip auto-generated placeholder descriptions like "Tool: find_gameobjects"
            if (!string.IsNullOrWhiteSpace(tool.Description) && !tool.Description.StartsWith("Tool: ", StringComparison.OrdinalIgnoreCase))
            {
                var description = new Label(tool.Description);
                description.AddToClassList("tool-item-description");
                row.Add(description);
            }

            if (tool.Parameters != null && tool.Parameters.Count > 0)
            {
                var paramSummary = string.Join(", ", tool.Parameters.Select(p => $"{p.Name}{(p.Required ? string.Empty : " (optional)")}: {p.Type}"));

                var parametersLabel = new Label(paramSummary);
                parametersLabel.AddToClassList("tool-parameters");
                row.Add(parametersLabel);
            }

            if (IsManageCameraTool(tool))
            {
                row.Add(CreateManageSceneActions());
            }

            if (IsBatchExecuteTool(tool))
            {
                row.Add(CreateBatchExecuteSettings());
            }

            return row;
        }

        private static ToolConsentScope? SelectConsentScope(ToolMetadata tool)
        {
            int choice = EditorUtility.DisplayDialogComplex(
                "Enable " + tool.Name,
                "Choose consent for " + ProjectIdentityUtility.GetProjectName() + ". This permits the tool for already-authorized callers of this Editor.",
                "This Editor session",
                "Cancel",
                "Remember for this project"
            );
            return choice == 0 ? ToolConsentScope.EditorSession
                : choice == 2 ? ToolConsentScope.Project
                : (ToolConsentScope?)null;
        }

        private void RefreshConsentRow(ToolMetadata tool)
        {
            var state = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
            if (toolToggleMap.TryGetValue(tool.Name, out var toggle))
                toggle.SetValueWithoutNotify(MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name));
            if (consentScopeLabels.TryGetValue(tool.Name, out var label))
                label.text =
                    state.HasGrant
                        ? "Consent for "
                            + ProjectIdentityUtility.GetProjectName()
                            + ": "
                            + (state.Scope == ToolConsentScope.Project ? "remembered for this project" : "this Editor session")
                    : state.LegacyGrantIgnored ? "Previous global consent is inactive; choose a scope for this project."
                    : state.ErrorCode != null ? "Consent blocked: " + state.ErrorCode
                    : "Consent: not granted";
        }

        private void ShowConsentResult(string name, ToolConsentChangeResult result)
        {
            string message;
            if (result.Succeeded)
            {
                consentPersistenceWarnings.Remove(name);
                message = "Consent settings saved for this project.";
            }
            else if (result.DeniedInCurrentDomain)
            {
                message =
                    result.RestartDenialConfirmed ? "Blocked; grant was not published. Revocation is confirmed across restart."
                    : result.ReloadDenialConfirmed ? "Blocked in this Editor, including reload. Revocation is not confirmed across restart."
                    : "Blocked in this Editor; revocation could not be confirmed across reload/restart. Retry explicitly before reloading.";
                consentPersistenceWarnings[name] = name + ": " + message;
            }
            else
                message = "Consent change was not applied: " + result.ErrorCode;
            if (noteLabel != null)
                noteLabel.text = consentPersistenceWarnings.Count > 0 ? string.Join("\n", consentPersistenceWarnings.Values) : message;
        }

        private void ChangeConsentScope(ToolMetadata tool)
        {
            var scope = consentScopePrompt(tool);
            if (!scope.HasValue)
            {
                RefreshConsentRow(tool);
                return;
            }
            var before = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
            var result = MCPServiceLocator.ToolConsent.Grant(tool.Name, scope.Value);
            ShowConsentResult(tool.Name, result);
            RefreshConsentRow(tool);
            ConsentChanged(tool, before);
        }

        private void ConsentChanged(ToolMetadata tool, ToolConsentStatus before)
        {
            var after = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
            UpdateSummary();
            UpdateFoldoutHeaders();
            SyncGroupToggles();
            if (ConsentStateChanged(before, after))
                ReregisterToolsAsync();
        }

        private static bool ConsentStateChanged(ToolConsentStatus before, ToolConsentStatus after) =>
            before.Enabled != after.Enabled || before.Scope != after.Scope || before.RequiresExplicitGrant != after.RequiresExplicitGrant;

        private bool HandleToggleChange(ToolMetadata tool, bool enabled, bool updateSummary = true, bool reregisterTools = true, bool allowConsentPrompt = true)
        {
            bool before = MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name);
            ToolConsentStatus previousConsent = default;
            if (tool.RequiresExplicitConsent)
            {
                previousConsent = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
                ToolConsentChangeResult result;
                if (enabled && (!previousConsent.HasGrant || previousConsent.RequiresExplicitGrant))
                {
                    if (!allowConsentPrompt)
                    {
                        RefreshConsentRow(tool);
                        return false;
                    }
                    var scope = consentScopePrompt(tool);
                    if (!scope.HasValue)
                    {
                        RefreshConsentRow(tool);
                        return false;
                    }
                    // Grant publishes scope AND enablement. Never follow it with
                    // SetToolEnabled: that must not retry a failed transaction.
                    result = MCPServiceLocator.ToolConsent.Grant(tool.Name, scope.Value);
                }
                else
                    result = MCPServiceLocator.ToolConsent.SetEnabled(tool.Name, enabled);
                ShowConsentResult(tool.Name, result);
                RefreshConsentRow(tool);
            }
            else
                MCPServiceLocator.ToolDiscovery.SetToolEnabled(tool.Name, enabled);

            bool changed = before != MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name);
            if (tool.RequiresExplicitConsent)
            {
                var after = MCPServiceLocator.ToolConsent.GetStatus(tool.Name);
                changed |= ConsentStateChanged(previousConsent, after);
            }

            if (updateSummary)
            {
                UpdateSummary();
                UpdateFoldoutHeaders();
                SyncGroupToggles();
            }

            if (reregisterTools && changed)
            {
                // Trigger tool reregistration with connected MCP server
                ReregisterToolsAsync();
            }
            return changed;
        }

        private void ReregisterToolsAsync()
        {
            BindStatusSource();
            statusSource?.NotifyToolSettingsChanged();
            RenderToolSettingsStatus();
            if (!IsHttpMode())
                return;
            // Fire and forget - don't block UI thread
            var transportManager = MCPServiceLocator.TransportManager;
            var client = transportManager.GetClient(TransportMode.Http);
            if (client == null || !client.IsConnected)
            {
                return;
            }

            _ = Task.Run(async () =>
            {
                try
                {
                    await client.ReregisterToolsAsync().ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Failed to reregister tools: {ex}");
                }
            });
        }

        private void SetAllToolsState(bool enabled)
        {
            bool hasChanges = false;

            foreach (var tool in allTools)
            {
                bool currentEnabled = MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name);
                if (toolToggleMap.TryGetValue(tool.Name, out var toggle))
                    toggle.SetValueWithoutNotify(enabled);

                if (currentEnabled == enabled && (enabled || !tool.RequiresExplicitConsent))
                {
                    continue;
                }

                hasChanges |= HandleToggleChange(tool, enabled, updateSummary: false, reregisterTools: false, allowConsentPrompt: false);
            }

            UpdateSummary();
            UpdateFoldoutHeaders();
            SyncGroupToggles();

            if (hasChanges)
            {
                // Trigger a single reregistration after bulk change
                ReregisterToolsAsync();
            }
        }

        private void SetGroupToolsState(List<ToolMetadata> groupTools, bool enabled, Foldout foldout, string title)
        {
            bool hasChanges = false;

            foreach (var tool in groupTools)
            {
                bool currentEnabled = MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name);
                if (toolToggleMap.TryGetValue(tool.Name, out var toggle))
                    toggle.SetValueWithoutNotify(enabled);

                if (currentEnabled != enabled || !enabled && tool.RequiresExplicitConsent)
                {
                    hasChanges |= HandleToggleChange(tool, enabled, updateSummary: false, reregisterTools: false, allowConsentPrompt: false);
                }
            }

            // Update the foldout header count
            int enabledCount = groupTools.Count(t => MCPServiceLocator.ToolDiscovery.IsToolEnabled(t.Name));
            foldout.text = $"{title} ({enabledCount}/{groupTools.Count})";

            // Sync global group toggles after group change
            SyncGroupToggles();

            UpdateSummary();

            if (hasChanges)
            {
                ReregisterToolsAsync();
            }
        }

        /// <summary>
        /// Synchronises group toggle checkmarks with actual tool states.
        /// Called after individual tool toggles change so the group toggle
        /// stays accurate.
        /// </summary>
        private void SyncGroupToggles()
        {
            // We need the grouped tool lists to check states.
            var builtInTools = allTools.Where(IsBuiltIn).ToList();
            var grouped = builtInTools.GroupBy(t => t.Group ?? "core").ToDictionary(g => g.Key, g => g.ToList());
            var customTools = allTools.Where(t => !IsBuiltIn(t)).ToList();

            foreach (var kvp in groupToggleMap)
            {
                List<ToolMetadata> groupTools;
                if (kvp.Key == "custom")
                {
                    groupTools = customTools;
                }
                else
                {
                    string groupKey = kvp.Key.StartsWith("group-") ? kvp.Key.Substring(6) : kvp.Key;
                    if (!grouped.TryGetValue(groupKey, out groupTools))
                        continue;
                }

                bool allEnabled = groupTools.All(t => MCPServiceLocator.ToolDiscovery.IsToolEnabled(t.Name));
                kvp.Value.SetValueWithoutNotify(allEnabled);
            }
        }

        private void OnReconfigureClientsClicked()
        {
            try
            {
                // Re-register tools with the server (HTTP mode)
                ReregisterToolsAsync();

                // Reconfigure all already-configured clients.
                // For CLI-based clients Configure() is a toggle (unregister if
                // configured, register if not), so we call it twice: first to
                // unregister, then to re-register with the updated tool set.
                var clients = MCPServiceLocator.Client.GetAllClients();
                int success = 0;
                int skipped = 0;
                var messages = new List<string>();

                foreach (var client in clients)
                {
                    try
                    {
                        client.CheckStatus(attemptAutoRewrite: false);

                        if (client.Status != McpStatus.Configured)
                        {
                            skipped++;
                            continue;
                        }

                        if (client is ClaudeCliMcpConfigurator)
                        {
                            // Toggle off (unregister), then toggle on (register)
                            MCPServiceLocator.Client.ConfigureClient(client);
                            MCPServiceLocator.Client.ConfigureClient(client);
                        }
                        else
                        {
                            // JSON-file clients: rewrite is idempotent
                            MCPServiceLocator.Client.ConfigureClient(client);
                        }

                        success++;
                        messages.Add($"✓ {client.DisplayName}: Reconfigured");
                    }
                    catch (Exception ex)
                    {
                        messages.Add($"⚠ {client.DisplayName}: {ex.Message}");
                    }
                }

                string header = $"Reconfigured {success} client(s), skipped {skipped}.";
                string body = messages.Count > 0 ? header + "\n\n" + string.Join("\n", messages) : header;

                EditorUtility.DisplayDialog("Reconfigure Clients", body, "OK");
            }
            catch (Exception ex)
            {
                EditorUtility.DisplayDialog("Reconfigure Failed", ex.Message, "OK");
                McpLog.Error($"Reconfigure failed: {ex.Message}");
            }
        }

        private void UpdateSummary()
        {
            if (summaryLabel == null)
            {
                return;
            }

            if (allTools.Count == 0)
            {
                summaryLabel.text = "No MCP tools discovered.";
                return;
            }

            int enabledCount = allTools.Count(tool => MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool.Name));
            summaryLabel.text = $"{enabledCount} of {allTools.Count} tools enabled in saved local settings.";
        }

        private bool IsHttpMode() => httpMode ?? EditorConfigurationCache.Instance.UseHttpTransport;

        private void BindStatusSource()
        {
            if (!statusAttached)
                return;
            var next = !IsHttpMode()
                ? null
                : providedStatusSource ?? MCPServiceLocator.TransportManager.GetClient(TransportMode.Http) as IToolSettingsStatusSource;
            if (!ReferenceEquals(next, statusSource))
            {
                UnbindStatusSource();
                statusSource = next;
                if (statusSource != null)
                    statusSource.StatusChanged += OnToolSettingsStatusChanged;
            }
            RenderToolSettingsStatus();
        }

        private void UnbindStatusSource()
        {
            if (statusSource != null)
                statusSource.StatusChanged -= OnToolSettingsStatusChanged;
            statusSource = null;
        }

        private void OnToolSettingsStatusChanged()
        {
            if (!statusAttached)
                return;
            if (Thread.CurrentThread.ManagedThreadId == uiThread)
                RenderToolSettingsStatus();
            else if (uiContext != null && Interlocked.CompareExchange(ref statusRenderQueued, 1, 0) == 0)
                uiContext.Post(
                    _ =>
                    {
                        Interlocked.Exchange(ref statusRenderQueued, 0);
                        if (statusAttached)
                            RenderToolSettingsStatus();
                    },
                    null
                );
        }

        private void RenderToolSettingsStatus()
        {
            bool http = IsHttpMode();
            ToolSettingsStatus status = http ? statusSource?.Status : null;
            if (applyStatusLabel != null)
            {
                applyStatusLabel.text =
                    !http ? "Not confirmed in stdio. Run manage_tools sync to refresh the server."
                    : status == null ? "Not confirmed. Reconnect to obtain server catalog status."
                    : status.Stage switch
                    {
                        "applied" => "Server catalog applied. Client visibility may also depend on enabled groups.",
                        "pending" or "sending" or "awaiting_ack" => "Saved locally. Waiting for server confirmation.",
                        "rejected" => "Rejected by server. Saved local settings are retained.",
                        "disconnected" => "Disconnected. Saved local settings are not currently confirmed.",
                        "preparation_failed" => "Not confirmed. Tool catalog preparation failed.",
                        "send_failed" => "Not confirmed. Sending tool settings failed.",
                        "unconfirmed" => "Not confirmed. The server has not acknowledged these settings.",
                        _ => "Saved locally. Server application is not confirmed.",
                    };
                applyStatusLabel.tooltip =
                    status == null
                        ? string.Empty
                        : $"Desired revision: {status.DesiredRevision}; last confirmed: {status.ConfirmedRevision?.ToString() ?? "none"}. {status.Reason} {status.Message}";
            }
            retryToolsButton?.SetEnabled(http && status?.CanRetry == true);
        }

        private async Task RetryToolSettingsAsync()
        {
            var source = statusSource;
            if (!IsHttpMode() || source?.Status.CanRetry != true)
                return;
            try
            {
                await source.RetryToolSettingsAsync();
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Tool settings retry failed: {ex.GetType().Name}");
            }
            if (statusAttached)
                RenderToolSettingsStatus();
        }

        private void UpdateFoldoutHeaders()
        {
            foreach (var (foldout, title, tools) in foldoutEntries)
            {
                int enabledCount = tools.Count(t => MCPServiceLocator.ToolDiscovery.IsToolEnabled(t.Name));
                foldout.text = $"{title} ({enabledCount}/{tools.Count})";
            }
        }

        private void ApplySearch()
        {
            string query = (searchField?.value ?? string.Empty).Trim();
            bool searching = query.Length > 0;
            int visibleCount = 0;

            foreach (var (foldout, _, tools) in foldoutEntries)
            {
                int groupVisibleCount = 0;
                foreach (var tool in tools)
                {
                    bool matches = MatchesSearch(tool, query);
                    if (toolRowMap.TryGetValue(tool.Name, out var row))
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
                visibleCountLabel.text = $"{visibleCount} / {allTools.Count} shown";
            if (emptyStateLabel != null)
            {
                emptyStateLabel.text = allTools.Count == 0 ? "No tools discovered." : "No tools match your search.";
                emptyStateLabel.EnableInClassList("catalog-hidden", visibleCount > 0);
            }
        }

        private static bool MatchesSearch(ToolMetadata tool, string query)
        {
            if (tool == null)
                return false;
            query = (query ?? string.Empty).Trim();
            if (query.Length == 0)
                return true;
            string group = tool.Group ?? "core";
            string category = IsBuiltIn(tool) ? GetGroupDisplayName(group) : "Custom Tools";
            return (tool.Name ?? string.Empty).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                || (tool.Description ?? string.Empty).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                || group.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                || category.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private VisualElement CreateManageSceneActions()
        {
            var actions = new VisualElement();
            actions.AddToClassList("tool-item-actions");

            var gameViewButton = new Button(OnManageSceneScreenshotClicked) { text = "Game View" };
            gameViewButton.AddToClassList("tool-action-button");
            gameViewButton.style.marginTop = 4;
            gameViewButton.tooltip = "Capture a game camera screenshot. Default: Assets/Screenshots (configurable in Advanced).";

            var sceneViewButton = new Button(OnSceneViewScreenshotClicked) { text = "Scene View" };
            sceneViewButton.AddToClassList("tool-action-button");
            sceneViewButton.style.marginTop = 4;
            sceneViewButton.style.marginLeft = 4;
            sceneViewButton.tooltip = "Capture the active Scene View viewport. Default: Assets/Screenshots (configurable in Advanced).";

            var multiviewButton = new Button(OnManageSceneMultiviewClicked) { text = "Multiview" };
            multiviewButton.AddToClassList("tool-action-button");
            multiviewButton.style.marginTop = 4;
            multiviewButton.style.marginLeft = 4;
            multiviewButton.tooltip = "Capture a 6-angle contact sheet around the scene centre. Default: Assets/Screenshots (configurable in Advanced).";

            var captureLabel = new Label("Capture:");
            captureLabel.style.marginTop = 6;
            captureLabel.style.unityFontStyleAndWeight = UnityEngine.FontStyle.Normal;

            var row = new VisualElement();
            row.style.flexDirection = FlexDirection.Row;
            row.style.flexWrap = Wrap.Wrap;
            row.Add(gameViewButton);
            row.Add(sceneViewButton);
            row.Add(multiviewButton);

            actions.Add(captureLabel);
            actions.Add(row);
            return actions;
        }

        private VisualElement CreateBatchExecuteSettings()
        {
            var container = new VisualElement();
            container.AddToClassList("tool-item-actions");
            container.style.flexDirection = FlexDirection.Row;
            container.style.alignItems = Align.Center;
            container.style.marginTop = 4;

            var label = new Label("Max commands per batch:");
            label.style.marginRight = 8;
            label.style.unityFontStyleAndWeight = UnityEngine.FontStyle.Normal;
            container.Add(label);

            int currentValue = EditorPrefs.GetInt(EditorPrefKeys.BatchExecuteMaxCommands, BatchExecute.DefaultMaxCommandsPerBatch);

            var field = new IntegerField { value = Math.Clamp(currentValue, 1, BatchExecute.AbsoluteMaxCommandsPerBatch), style = { width = 60 } };
            field.tooltip =
                $"Number of commands allowed per batch_execute call (1–{BatchExecute.AbsoluteMaxCommandsPerBatch}). Default: {BatchExecute.DefaultMaxCommandsPerBatch}.";

            field.RegisterValueChangedCallback(evt =>
            {
                int clamped = Math.Clamp(evt.newValue, 1, BatchExecute.AbsoluteMaxCommandsPerBatch);
                if (clamped != evt.newValue)
                {
                    field.SetValueWithoutNotify(clamped);
                }
                EditorPrefs.SetInt(EditorPrefKeys.BatchExecuteMaxCommands, clamped);
            });

            container.Add(field);

            var hint = new Label($"(max {BatchExecute.AbsoluteMaxCommandsPerBatch})");
            hint.style.marginLeft = 4;
            hint.style.color = new UnityEngine.Color(0.5f, 0.5f, 0.5f);
            hint.style.fontSize = 10;
            container.Add(hint);

            return container;
        }

        private void OnManageSceneScreenshotClicked()
        {
            try
            {
                var response = ManageScene.ExecuteScreenshot();
                if (response is SuccessResponse success && !string.IsNullOrWhiteSpace(success.Message))
                {
                    McpLog.Info(success.Message);
                }
                else if (response is ErrorResponse error && !string.IsNullOrWhiteSpace(error.Error))
                {
                    McpLog.Error(error.Error);
                }
                else
                {
                    McpLog.Info("Screenshot capture requested.");
                }
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to capture screenshot: {ex.Message}");
            }
        }

        private void OnSceneViewScreenshotClicked()
        {
            try
            {
                var response = ManageScene.ExecuteSceneViewScreenshot();
                if (response is SuccessResponse success && !string.IsNullOrWhiteSpace(success.Message))
                {
                    McpLog.Info(success.Message);
                }
                else if (response is ErrorResponse error && !string.IsNullOrWhiteSpace(error.Error))
                {
                    McpLog.Error(error.Error);
                }
                else
                {
                    McpLog.Info("Scene View screenshot capture requested.");
                }
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to capture Scene View screenshot: {ex.Message}");
            }
        }

        internal static MCPForUnity.Runtime.Helpers.ScreenshotCaptureResult SaveMultiviewCapture(string base64, string folder, string fileName)
        {
            var output = MCPForUnity.Runtime.Helpers.ScreenshotUtility.PrepareCaptureResult(fileName, 1, true, folder, false);
            MCPForUnity.Runtime.Helpers.ScreenshotUtility.WriteCaptureBytes(output.FullPath, Convert.FromBase64String(base64));
            if (MCPForUnity.Runtime.Helpers.ScreenshotUtility.IsUnderAssets(output.ProjectRelativePath))
                AssetDatabase.ImportAsset(output.ProjectRelativePath, ImportAssetOptions.ForceSynchronousImport);
            return output;
        }

        private void OnManageSceneMultiviewClicked()
        {
            try
            {
                var response = ManageScene.ExecuteMultiviewScreenshot();
                if (response is SuccessResponse success)
                {
                    // The data object is an anonymous type with imageBase64 — serialize to extract it
                    var json = Newtonsoft.Json.Linq.JObject.FromObject(success.Data);
                    string base64 = json["imageBase64"]?.ToString();
                    if (!string.IsNullOrEmpty(base64))
                    {
                        var output = SaveMultiviewCapture(
                            base64,
                            MCPForUnity.Editor.Helpers.ScreenshotPreferences.Resolve(null),
                            $"Multiview_{System.DateTime.Now:yyyyMMdd_HHmmss}"
                        );
                        McpLog.Info($"Multiview contact sheet saved to {output.ProjectRelativePath}");
                    }
                    else
                    {
                        McpLog.Info(success.Message ?? "Multiview capture completed.");
                    }
                }
                else if (response is ErrorResponse error && !string.IsNullOrWhiteSpace(error.Error))
                {
                    McpLog.Error(error.Error);
                }
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to capture multiview: {ex.Message}");
            }
        }

        private static Label CreateTag(string text)
        {
            var tag = new Label(text);
            tag.AddToClassList("tool-tag");
            return tag;
        }

        private static bool IsManageSceneTool(ToolMetadata tool) => string.Equals(tool?.Name, "manage_scene", StringComparison.OrdinalIgnoreCase);

        private static bool IsManageCameraTool(ToolMetadata tool) => string.Equals(tool?.Name, "manage_camera", StringComparison.OrdinalIgnoreCase);

        private static bool IsBatchExecuteTool(ToolMetadata tool) => string.Equals(tool?.Name, "batch_execute", StringComparison.OrdinalIgnoreCase);

        private static bool IsBuiltIn(ToolMetadata tool) => tool?.IsBuiltIn ?? false;
    }
}
