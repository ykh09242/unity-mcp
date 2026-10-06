using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.Advanced;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using MCPForUnity.Editor.Windows.Components.Branding;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using MCPForUnity.Editor.Windows.Components.Connection;
using MCPForUnity.Editor.Windows.Components.Resources;
using MCPForUnity.Editor.Windows.Components.Tools;
using MCPForUnity.Editor.Setup;
using MCPForUnity.Editor.Windows.Components.Validation;
using UnityEditor;
using Unity.EditorCoroutines.Editor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    public class MCPForUnityEditorWindow : EditorWindow
    {
        // Section controllers
        private McpConnectionSection connectionSection;
        private McpClientConfigSection clientConfigSection;
        private McpAdvancedSection advancedSection;
        private McpToolsSection toolsSection;
        private McpResourcesSection resourcesSection;
        private McpAssetGenSection assetGenSection;

        // UI Elements
        private Label versionLabel;
        private VisualElement updateNotification;
        private Label updateNotificationText;

        private ToolbarToggle clientsTabToggle;
        private ToolbarToggle depsTabToggle;
        private ToolbarToggle advancedTabToggle;
        private ToolbarToggle toolsTabToggle;
        private ToolbarToggle resourcesTabToggle;
        private ToolbarToggle assetGenTabToggle;
        private VisualElement clientsPanel;
        private VisualElement depsPanel;
        private VisualElement advancedPanel;
        private VisualElement toolsPanel;
        private VisualElement resourcesPanel;
        private VisualElement assetGenPanel;

        private static readonly HashSet<MCPForUnityEditorWindow> OpenWindows = new();
        private static bool dependencyActionInFlight;
        private VisualElement dependencySection;
        private bool guiCreated = false;
        private bool toolsLoaded = false;
        private bool resourcesLoaded = false;
        private double lastRefreshTime = 0;
        private const double RefreshDebounceSeconds = 0.5;
        private bool updateCheckQueued = false;
        private bool updateCheckInFlight = false;
        private bool updateCheckPending = false;
        private ActivePanel activePanel;

        private enum ActivePanel
        {
            Clients,
            Deps,
            Advanced,
            Tools,
            Resources,
            AssetGen
        }

        internal static void CloseAllWindows()
        {
            var windows = OpenWindows.Where(window => window != null).ToArray();
            foreach (var window in windows)
            {
                window.Close();
            }
        }

        public static void ShowWindow()
        {
            var existingWindows = UnityEngine.Resources.FindObjectsOfTypeAll<MCPForUnityEditorWindow>();
            MCPForUnityEditorWindow window = null;

            if (existingWindows.Length > 0)
            {
                window = existingWindows[0];

                // If multiple instances exist, keep one and close the extras to avoid stale hidden tabs.
                for (int i = 1; i < existingWindows.Length; i++)
                {
                    try
                    {
                        existingWindows[i].Close();
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"Error closing duplicate MCP window: {ex.Message}");
                    }
                }
            }
            else
            {
                window = GetWindow<MCPForUnityEditorWindow>(ProductInfo.ProductName);
            }

            window.titleContent = new GUIContent(ProductInfo.ProductName);
            window.minSize = new Vector2(500, 340);

            if (window.position.width < 100 || window.position.height < 100)
            {
                window.position = new Rect(120, 120, 900, 700);
            }

            window.Show();
            window.ShowTab();
            window.Focus();
        }

        // Helper to check and manage open windows from other classes
        public static bool HasAnyOpenWindow()
        {
            return OpenWindows.Count > 0;
        }

        public static void CloseAllOpenWindows()
        {
            if (OpenWindows.Count == 0)
                return;

            // Copy to array to avoid modifying the collection while iterating
            var arr = new MCPForUnityEditorWindow[OpenWindows.Count];
            OpenWindows.CopyTo(arr);
            foreach (var window in arr)
            {
                try
                {
                    window?.Close();
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Error closing MCP window: {ex.Message}");
                }
            }
        }

        public void CreateGUI()
        {
            // Guard against repeated CreateGUI calls (e.g., domain reloads)
            if (guiCreated)
                return;

            string basePath = AssetPathUtility.GetMcpPackageRootPath();

            // Load main window UXML
            var visualTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/MCPForUnityEditorWindow.uxml"
            );

            if (visualTree == null)
            {
                McpLog.Error(
                    $"Failed to load UXML at: {basePath}/Editor/Windows/MCPForUnityEditorWindow.uxml"
                );
                return;
            }

            rootVisualElement.Clear();
            rootVisualElement.AddToClassList("mcp-editor");
            rootVisualElement.EnableInClassList("unity-theme-light", !EditorGUIUtility.isProSkin);
            rootVisualElement.EnableInClassList("unity-theme-dark", EditorGUIUtility.isProSkin);
            visualTree.CloneTree(rootVisualElement);

            // Embed the Ocean brand mark at the left of the header bar
            var headerLeft = rootVisualElement.Q<VisualElement>("header-left");
            if (headerLeft != null && headerLeft.Q<OceanMark>() == null)
            {
                var logo = new OceanMark { name = "header-logo" };
                logo.AddToClassList("header-logo");
                headerLeft.Insert(0, logo);
            }

            // Cache UI elements
            versionLabel = rootVisualElement.Q<Label>("version-label");
            updateNotification = rootVisualElement.Q<VisualElement>("update-notification");
            updateNotificationText = rootVisualElement.Q<Label>("update-notification-text");

            clientsPanel = rootVisualElement.Q<VisualElement>("clients-panel");
            depsPanel = rootVisualElement.Q<VisualElement>("deps-panel");
            advancedPanel = rootVisualElement.Q<VisualElement>("advanced-panel");
            toolsPanel = rootVisualElement.Q<VisualElement>("tools-panel");
            resourcesPanel = rootVisualElement.Q<VisualElement>("resources-panel");
            assetGenPanel = rootVisualElement.Q<VisualElement>("assetgen-panel");
            var clientsContainer = rootVisualElement.Q<VisualElement>("clients-container");
            var depsContainer = rootVisualElement.Q<VisualElement>("deps-container");
            var advancedContainer = rootVisualElement.Q<VisualElement>("advanced-container");
            var toolsContainer = rootVisualElement.Q<VisualElement>("tools-container");
            var resourcesContainer = rootVisualElement.Q<VisualElement>("resources-container");
            var assetGenContainer = rootVisualElement.Q<VisualElement>("assetgen-container");

            if (clientsPanel == null || depsPanel == null || advancedPanel == null || toolsPanel == null || resourcesPanel == null || assetGenPanel == null)
            {
                McpLog.Error("Failed to find tab panels in UXML");
                return;
            }

            if (clientsContainer == null)
            {
                McpLog.Error("Failed to find clients-container in UXML");
                return;
            }

            if (depsContainer == null)
            {
                McpLog.Error("Failed to find deps-container in UXML");
                return;
            }

            if (advancedContainer == null)
            {
                McpLog.Error("Failed to find advanced-container in UXML");
                return;
            }

            if (toolsContainer == null)
            {
                McpLog.Error("Failed to find tools-container in UXML");
                return;
            }

            if (resourcesContainer == null)
            {
                McpLog.Error("Failed to find resources-container in UXML");
                return;
            }

            if (assetGenContainer == null)
            {
                McpLog.Error("Failed to find assetgen-container in UXML");
                return;
            }

            // Initialize version label
            UpdateVersionLabel();

            SetupTabs();

            // Load and initialize Connection section
            var connectionTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/Connection/McpConnectionSection.uxml"
            );
            if (connectionTree != null)
            {
                var connectionRoot = connectionTree.Instantiate();
                clientsContainer.Add(connectionRoot);
                connectionSection = new McpConnectionSection(connectionRoot);
                connectionSection.OnManualConfigUpdateRequested += () =>
                    clientConfigSection?.UpdateManualConfiguration();
                connectionSection.OnTransportChanged += () =>
                    clientConfigSection?.RefreshSelectedClient(forceImmediate: true);
            }

            // Load and initialize Client Configuration section
            var clientConfigTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/ClientConfig/McpClientConfigSection.uxml"
            );
            if (clientConfigTree != null)
            {
                var clientConfigRoot = clientConfigTree.Instantiate();
                clientsContainer.Add(clientConfigRoot);
                clientConfigSection = new McpClientConfigSection(clientConfigRoot);

                // Wire up transport mismatch detection: when client status is checked,
                // update the connection section's warning banner if there's a mismatch
                clientConfigSection.OnClientTransportDetected += (clientName, transport) =>
                    connectionSection?.UpdateTransportMismatchWarning(clientName, transport);

                // Wire up version mismatch detection: when client status is checked,
                // update the connection section's warning banner if there's a version mismatch
                clientConfigSection.OnClientConfigMismatch += (clientName, mismatchMessage) =>
                    connectionSection?.UpdateVersionMismatchWarning(clientName, mismatchMessage);
            }

            // Build Dependencies section (replaces old Roslyn + Validation in Deps tab)
            BuildDependenciesSection(depsContainer);

            // Load and initialize Advanced section
            var advancedTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/Advanced/McpAdvancedSection.uxml"
            );
            if (advancedTree != null)
            {
                var advancedRoot = advancedTree.Instantiate();
                advancedContainer.Add(advancedRoot);
                advancedSection = new McpAdvancedSection(advancedRoot);

                // Wire up events from Advanced section
                advancedSection.OnGitUrlChanged += () =>
                    clientConfigSection?.UpdateManualConfiguration();
                advancedSection.OnHttpServerCommandUpdateRequested += () =>
                {
                    connectionSection?.UpdateHttpServerCommandDisplay();
                    connectionSection?.UpdateConnectionStatus();
                };
                advancedSection.OnTestConnectionRequested += async () =>
                {
                    if (connectionSection != null)
                        await connectionSection.VerifyBridgeConnectionAsync();
                };
                advancedSection.OnPackageDeployed += () =>
                {
                    UpdateVersionLabel();
                    QueueUpdateCheck();
                };
                // Wire up health status updates from Connection to Advanced
                connectionSection?.SetHealthStatusUpdateCallback((isHealthy, statusText) =>
                    advancedSection?.UpdateHealthStatus(isHealthy, statusText));
            }

            // Load Validation section into Advanced tab
            var validationTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/Validation/McpValidationSection.uxml"
            );
            if (validationTree != null)
            {
                var validationRoot = validationTree.Instantiate();
                advancedContainer.Add(validationRoot);
                new McpValidationSection(validationRoot);
            }

            // Load and initialize Tools section
            var toolsTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/Tools/McpToolsSection.uxml"
            );
            if (toolsTree != null)
            {
                var toolsRoot = toolsTree.Instantiate();
                toolsContainer.Add(toolsRoot);
                toolsSection = new McpToolsSection(toolsRoot);

                if (toolsTabToggle != null && toolsTabToggle.value)
                {
                    EnsureToolsLoaded();
                }
            }
            else
            {
                McpLog.Warn("Failed to load tools section UXML. Tool configuration will be unavailable.");
            }

            // Load and initialize Resources section
            var resourcesTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/Resources/McpResourcesSection.uxml"
            );
            if (resourcesTree != null)
            {
                var resourcesRoot = resourcesTree.Instantiate();
                resourcesContainer.Add(resourcesRoot);
                resourcesSection = new McpResourcesSection(resourcesRoot);

                if (resourcesTabToggle != null && resourcesTabToggle.value)
                {
                    EnsureResourcesLoaded();
                }
            }
            else
            {
                McpLog.Warn("Failed to load resources section UXML. Resource configuration will be unavailable.");
            }

            // Load and initialize Asset Generation section
            var assetGenTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                $"{basePath}/Editor/Windows/Components/AssetGen/McpAssetGenSection.uxml"
            );
            if (assetGenTree != null)
            {
                var assetGenRoot = assetGenTree.Instantiate();
                assetGenContainer.Add(assetGenRoot);
                assetGenSection = new McpAssetGenSection(assetGenRoot);
            }
            else
            {
                McpLog.Warn("Failed to load asset generation section UXML. Asset generation configuration will be unavailable.");
            }

            // Apply .section-last class to last section in each stack
            // (Unity UI Toolkit doesn't support :last-child pseudo-class)
            ApplySectionLastClasses();

            guiCreated = true;

            // Initial updates
            RefreshAllData();
            QueueUpdateCheck();
        }

        private void UpdateVersionLabel()
        {
            if (versionLabel == null)
            {
                return;
            }

            string version = AssetPathUtility.GetPackageVersion();
            versionLabel.text = $"v{version}";
            try
            {
                versionLabel.tooltip = $"{ProductInfo.ProductName} v{version}\nServer source: {AssetPathUtility.GetMcpServerPackageSource()}";
            }
            catch (InvalidOperationException ex)
            {
                versionLabel.tooltip = $"{ProductInfo.ProductName} v{version}\n{ex.Message}";
            }
        }

        private void QueueUpdateCheck()
        {
            if (updateCheckInFlight)
            {
                updateCheckPending = true;
                return;
            }
            if (updateCheckQueued)
            {
                return;
            }

            updateCheckQueued = true;
            EditorApplication.delayCall += CheckForPackageUpdates;
        }

        private async void CheckForPackageUpdates()
        {
            updateCheckQueued = false;

            if (updateNotification == null || updateNotificationText == null)
            {
                return;
            }

            string currentVersion = AssetPathUtility.GetPackageVersion();
            if (string.IsNullOrEmpty(currentVersion) || currentVersion == "unknown")
            {
                updateNotification.RemoveFromClassList("visible");
                return;
            }

            // Main thread: resolve service + read EditorPrefs cache (both require main thread)
            var updateService = MCPServiceLocator.Updates;
            var cachedResult = updateService.TryGetCachedResult(currentVersion);
            if (cachedResult != null)
            {
                ApplyUpdateCheckResult(cachedResult, currentVersion);
                return;
            }

            // Main thread: pre-compute installation info (uses main-thread-only Unity APIs)
            bool isGitInstallation = updateService.IsGitInstallation();
            string gitBranch = isGitInstallation ? updateService.GetGitUpdateBranch(currentVersion) : "main";

            // Background thread: network I/O only (no EditorPrefs or Unity API access)
            updateCheckInFlight = true;
            try
            {
                var result = await Task.Run(() =>
                {
                    return updateService.FetchAndCompare(currentVersion, isGitInstallation, gitBranch);
                });

                bool current = IsUpdateCheckCurrent(updateService, currentVersion, isGitInstallation, gitBranch);
                if (current && result != null && result.CheckSucceeded && !string.IsNullOrEmpty(result.LatestVersion))
                {
                    updateService.CacheFetchResult(currentVersion, result.LatestVersion);
                }
                if (this == null || !guiCreated || updateNotification == null || updateNotificationText == null)
                    return;

                if (current)
                    ApplyUpdateCheckResult(result, currentVersion);
                else
                    updateCheckPending = true;
            }
            catch (Exception ex)
            {
                McpLog.Info($"Package update check skipped: {ex.Message}");
                if (this != null && guiCreated && updateNotification != null && updateNotificationText != null)
                    ApplyUpdateCheckResult(null, currentVersion);
            }
            finally
            {
                updateCheckInFlight = false;
                if (updateCheckPending)
                {
                    updateCheckPending = false;
                    if (this != null && guiCreated)
                        QueueUpdateCheck();
                }
            }
        }

        private static bool IsUpdateCheckCurrent(IPackageUpdateService service, string version, bool isGitInstallation, string branch)
        {
            if (!ReferenceEquals(service, MCPServiceLocator.Updates) ||
                !string.Equals(version, AssetPathUtility.GetPackageVersion(), StringComparison.Ordinal) ||
                isGitInstallation != service.IsGitInstallation())
                return false;

            return !isGitInstallation || string.Equals(branch, service.GetGitUpdateBranch(version), StringComparison.OrdinalIgnoreCase);
        }

        private void ApplyUpdateCheckResult(UpdateCheckResult result, string currentVersion)
        {
            if (result != null && result.CheckSucceeded && result.UpdateAvailable && !string.IsNullOrEmpty(result.LatestVersion))
            {
                updateNotificationText.text = $"Update available: v{result.LatestVersion}  (current: v{currentVersion})";
                updateNotificationText.tooltip = $"Latest version: v{result.LatestVersion}\nCurrent version: v{currentVersion}";
                updateNotification.AddToClassList("visible");
            }
            else
            {
                updateNotification.RemoveFromClassList("visible");
            }
        }

        private void EnsureToolsLoaded()
        {
            if (toolsLoaded)
            {
                return;
            }

            if (toolsSection == null)
            {
                return;
            }

            toolsLoaded = true;
            toolsSection.Refresh();
        }

        private void EnsureResourcesLoaded()
        {
            if (resourcesLoaded)
            {
                return;
            }

            if (resourcesSection == null)
            {
                return;
            }

            resourcesLoaded = true;
            resourcesSection.Refresh();
        }

        /// <summary>
        /// Applies the .section-last class to the last .section element in each .section-stack container.
        /// This is a workaround for Unity UI Toolkit not supporting the :last-child pseudo-class.
        /// </summary>
        private void ApplySectionLastClasses()
        {
            var sectionStacks = rootVisualElement.Query<VisualElement>(className: "section-stack").ToList();
            foreach (var stack in sectionStacks)
            {
                var sections = stack.Children().Where(c => c.ClassListContains("section")).ToList();
                if (sections.Count > 0)
                {
                    // Remove class from all sections first (in case of refresh)
                    foreach (var section in sections)
                    {
                        section.RemoveFromClassList("section-last");
                    }
                    // Add class to the last section
                    sections[sections.Count - 1].AddToClassList("section-last");
                }
            }
        }

        // Throttle OnEditorUpdate to avoid per-frame overhead (GitHub issue #577).
        // Connection status polling every frame caused expensive network checks 60+ times/sec.
        private double _lastEditorUpdateTime;
        private const double EditorUpdateIntervalSeconds = 2.0;

        private void OnEnable()
        {
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.update += OnEditorUpdate;
            OpenWindows.Add(this);
        }

        private void OnDisable()
        {
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.delayCall -= CheckForPackageUpdates;
            updateCheckQueued = false;
            updateCheckPending = false;
            OpenWindows.Remove(this);
            guiCreated = false;
            toolsLoaded = false;
            resourcesLoaded = false;
        }

        private void OnFocus()
        {
            // Only refresh data if UI is built
            if (rootVisualElement == null || rootVisualElement.childCount == 0)
                return;

            RefreshAllData();
        }

        private void OnEditorUpdate()
        {
            // Throttle to 2-second intervals instead of every frame.
            // This prevents the expensive IsLocalHttpServerReachable() socket checks from running
            // 60+ times per second, which caused main thread blocking and GC pressure.
            double now = EditorApplication.timeSinceStartup;
            if (now - _lastEditorUpdateTime < EditorUpdateIntervalSeconds)
            {
                return;
            }
            _lastEditorUpdateTime = now;

            if (rootVisualElement == null || rootVisualElement.childCount == 0)
                return;

            connectionSection?.UpdateConnectionStatus();
        }

        private void RefreshAllData()
        {
            // Debounce rapid successive calls (e.g., from OnFocus being called multiple times)
            double currentTime = EditorApplication.timeSinceStartup;
            if (currentTime - lastRefreshTime < RefreshDebounceSeconds)
            {
                return;
            }
            lastRefreshTime = currentTime;

            connectionSection?.UpdateConnectionStatus();

            if (MCPServiceLocator.Bridge.IsRunning)
            {
                _ = connectionSection?.VerifyBridgeConnectionAsync();
            }

            advancedSection?.UpdatePathOverrides();
            clientConfigSection?.RefreshSelectedClient();
        }

        private void SetupTabs()
        {
            clientsTabToggle = rootVisualElement.Q<ToolbarToggle>("clients-tab");
            depsTabToggle = rootVisualElement.Q<ToolbarToggle>("deps-tab");
            advancedTabToggle = rootVisualElement.Q<ToolbarToggle>("advanced-tab");
            toolsTabToggle = rootVisualElement.Q<ToolbarToggle>("tools-tab");
            resourcesTabToggle = rootVisualElement.Q<ToolbarToggle>("resources-tab");
            assetGenTabToggle = rootVisualElement.Q<ToolbarToggle>("assetgen-tab");

            RegisterTab(clientsTabToggle, ActivePanel.Clients);
            RegisterTab(depsTabToggle, ActivePanel.Deps);
            RegisterTab(advancedTabToggle, ActivePanel.Advanced);
            RegisterTab(toolsTabToggle, ActivePanel.Tools);
            RegisterTab(resourcesTabToggle, ActivePanel.Resources);
            RegisterTab(assetGenTabToggle, ActivePanel.AssetGen);

            var savedPanel = EditorPrefs.GetString(EditorPrefKeys.EditorWindowActivePanel, ActivePanel.Clients.ToString());
            // Migrate old "Validation" saved value to "Deps"
            if (savedPanel == "Validation") savedPanel = "Deps";
            if (!Enum.TryParse(savedPanel, out ActivePanel initialPanel)
                || !Enum.IsDefined(typeof(ActivePanel), initialPanel))
            {
                initialPanel = ActivePanel.Clients;
            }

            SwitchPanel(initialPanel);
        }

        private void RegisterTab(ToolbarToggle toggle, ActivePanel panel)
        {
            toggle?.RegisterValueChangedCallback(evt =>
            {
                if (evt.newValue)
                    SwitchPanel(panel);
                else if (activePanel == panel)
                    toggle.SetValueWithoutNotify(true);
            });
        }

        private void SwitchPanel(ActivePanel panel)
        {
            activePanel = panel;
            clientsPanel?.EnableInClassList("hidden", panel != ActivePanel.Clients);
            depsPanel?.EnableInClassList("hidden", panel != ActivePanel.Deps);
            advancedPanel?.EnableInClassList("hidden", panel != ActivePanel.Advanced);
            toolsPanel?.EnableInClassList("hidden", panel != ActivePanel.Tools);
            resourcesPanel?.EnableInClassList("hidden", panel != ActivePanel.Resources);
            assetGenPanel?.EnableInClassList("hidden", panel != ActivePanel.AssetGen);

            // Show selected panel
            switch (panel)
            {
                case ActivePanel.Clients:
                    // Refresh client status when switching to Connect tab (e.g., after package/version changes).
                    clientConfigSection?.RefreshSelectedClient(forceImmediate: true);
                    break;
                case ActivePanel.Deps:
                    break;
                case ActivePanel.Advanced:
                    break;
                case ActivePanel.Tools:
                    EnsureToolsLoaded();
                    break;
                case ActivePanel.Resources:
                    EnsureResourcesLoaded();
                    break;
                case ActivePanel.AssetGen:
                    assetGenSection?.Refresh();
                    break;
            }

            // Update toggle states
            clientsTabToggle?.SetValueWithoutNotify(panel == ActivePanel.Clients);
            depsTabToggle?.SetValueWithoutNotify(panel == ActivePanel.Deps);
            advancedTabToggle?.SetValueWithoutNotify(panel == ActivePanel.Advanced);
            toolsTabToggle?.SetValueWithoutNotify(panel == ActivePanel.Tools);
            resourcesTabToggle?.SetValueWithoutNotify(panel == ActivePanel.Resources);
            assetGenTabToggle?.SetValueWithoutNotify(panel == ActivePanel.AssetGen);

            EditorPrefs.SetString(EditorPrefKeys.EditorWindowActivePanel, panel.ToString());
        }

        internal static void RequestHealthVerification()
        {
            foreach (var window in OpenWindows)
            {
                window?.ScheduleHealthCheck();
            }
        }

        private void ScheduleHealthCheck()
        {
            EditorApplication.delayCall += async () =>
            {
                // Ensure window and components are still valid before execution
                if (this == null || connectionSection == null)
                {
                    return;
                }

                try
                {
                    await connectionSection.VerifyBridgeConnectionAsync();
                }
                catch (Exception ex)
                {
                    // Log but don't crash if verification fails during cleanup
                    McpLog.Warn($"Health check verification failed: {ex.Message}");
                }
            };
        }

        private void BuildDependenciesSection(VisualElement container)
        {
            var section = new VisualElement();
            dependencySection = section;
            section.SetEnabled(!dependencyActionInFlight);
            section.AddToClassList("section");

            var title = new Label("Optional Dependencies");
            title.AddToClassList("section-title");
            section.Add(title);

            var content = new VisualElement();
            content.AddToClassList("section-content");

            // Install All / Uninstall All buttons
            var bulkRow = new VisualElement();
            bulkRow.AddToClassList("tool-actions");

            var upmPackages = new[] { "com.unity.probuilder", "com.unity.cinemachine", "com.unity.visualeffectgraph", "com.unity.cloud.gltfast" };

            Button installAllButton = null;
            installAllButton = new Button(() =>
            {
                if (!EditorUtility.DisplayDialog("Install All Dependencies",
                    "This will install Roslyn DLLs, ProBuilder, Cinemachine, VFX Graph, and glTFast. Continue?",
                    "Install All", "Cancel")) return;
                RunDependencyAction(installAllButton, "Installing...", "Install All", async done =>
                {
                    try
                    {
                        if (!RoslynInstaller.IsInstalled()) await RoslynInstaller.InstallAsync(interactive: false);
                        BatchUpmAdd(upmPackages, done);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[MCP] Install failed: {ex.Message}");
                        done();
                    }
                });
            });
            installAllButton.text = "Install All";
            installAllButton.AddToClassList("action-button");
            bulkRow.Add(installAllButton);

            Button uninstallAllButton = null;
            uninstallAllButton = new Button(() =>
            {
                if (!EditorUtility.DisplayDialog("Uninstall All Dependencies",
                    "This will remove Roslyn DLLs, ProBuilder, Cinemachine, VFX Graph, and glTFast. Continue?",
                    "Uninstall All", "Cancel")) return;
                RunDependencyAction(uninstallAllButton, "Removing...", "Uninstall All", done =>
                {
                    UninstallRoslyn();
                    BatchUpmRemove(upmPackages, done);
                });
            });
            uninstallAllButton.text = "Uninstall All";
            uninstallAllButton.AddToClassList("action-button");
            bulkRow.Add(uninstallAllButton);

            content.Add(bulkRow);

            // Roslyn — for execute_code modern C# support
            // Check if Roslyn types are actually loaded (covers NuGet, Plugins folder, etc.)
            bool roslynLoaded = Type.GetType("Microsoft.CodeAnalysis.CSharp.CSharpCompilation, Microsoft.CodeAnalysis.CSharp") != null;
            bool roslynInstalledLocally = RoslynInstaller.IsInstalled();
            AddDependencyRow(content,
                "Roslyn (C# 12+ Compiler)",
                "Enables modern C# syntax in execute_code tool (scripting_ext group).",
                roslynLoaded,
                roslynInstalledLocally
                    ? "Installed via Plugins/Roslyn \u2014 execute_code uses Roslyn"
                    : "Available (loaded from NuGet/external) \u2014 execute_code uses Roslyn",
                "Not installed \u2014 execute_code falls back to C# 6 (CodeDom)",
                async done => { try { await RoslynInstaller.InstallAsync(interactive: true); } finally { done(); } },
                roslynInstalledLocally
                    ? (Action<Action>)(done => { UninstallRoslyn(); done?.Invoke(); })
                    : null);

            // ProBuilder
            bool hasProBuilder = Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder") != null;
            AddDependencyRow(content,
                "ProBuilder",
                "Required for the manage_probuilder tool (probuilder group).",
                hasProBuilder,
                "Installed",
                "Not installed",
                done => InstallUpmPackage("com.unity.probuilder", done),
                done => RemoveUpmPackage("com.unity.probuilder", done));

            // Cinemachine
            bool hasCinemachine = Type.GetType("Unity.Cinemachine.CinemachineCamera, Unity.Cinemachine") != null
                || Type.GetType("Cinemachine.CinemachineVirtualCamera, Cinemachine") != null;
            AddDependencyRow(content,
                "Cinemachine",
                "Enhances manage_camera with virtual camera support (core group).",
                hasCinemachine,
                "Installed",
                "Not installed \u2014 camera tool works without it",
                done => InstallUpmPackage("com.unity.cinemachine", done),
                done => RemoveUpmPackage("com.unity.cinemachine", done));

            // VFX Graph — uses preprocessor symbol, so check via UPM package list
            bool hasVfxGraph = IsUpmPackageInstalled("com.unity.visualeffectgraph");
            AddDependencyRow(content,
                "VFX Graph",
                "Enables VisualEffect support in manage_vfx tool (vfx group).",
                hasVfxGraph,
                "Installed",
                "Not installed \u2014 VFX tool falls back to ParticleSystem/LineRenderer",
                done => InstallUpmPackage("com.unity.visualeffectgraph", done),
                done => RemoveUpmPackage("com.unity.visualeffectgraph", done));

            // glTFast — uses an assembly type, but also check via UPM package list
            bool hasGltfast = IsUpmPackageInstalled("com.unity.cloud.gltfast") || Type.GetType("GLTFast.GltfImport, glTFast") != null;
            AddDependencyRow(content,
                "glTFast (glTF/GLB import)",
                "Enables .glb/.gltf model import for the AI Asset Generation tools (asset_gen group).",
                hasGltfast,
                "Installed — GLB generation/import works",
                "Not installed — GLB import is unavailable; FBX still works, or install to enable GLB",
                done => InstallUpmPackage("com.unity.cloud.gltfast", done),
                done => RemoveUpmPackage("com.unity.cloud.gltfast", done));

            section.Add(content);
            container.Add(section);
        }

        private static void AddDependencyRow(VisualElement parent, string name, string description,
            bool isInstalled, string installedText, string missingText,
            Action<Action> installAction, Action<Action> uninstallAction)
        {
            var row = new VisualElement();
            row.AddToClassList("package-row");

            var header = new VisualElement();
            header.AddToClassList("setting-row");

            var nameLabel = new Label(name);
            nameLabel.AddToClassList("package-name");
            nameLabel.tooltip = description;
            header.Add(nameLabel);

            var statusIcon = new Label(isInstalled ? "\u2713" : "\u2717");
            statusIcon.AddToClassList(isInstalled ? "status-success" : "status-warning");
            header.Add(statusIcon);

            row.Add(header);

            var statusText = new Label(isInstalled ? installedText : missingText);
            statusText.AddToClassList("help-text");
            statusText.AddToClassList(isInstalled ? "status-success" : "status-warning");
            row.Add(statusText);

            var buttonRow = new VisualElement();
            buttonRow.AddToClassList("tool-actions");

            if (!isInstalled && installAction != null)
            {
                Button btn = null;
                btn = new Button(() =>
                {
                    RunDependencyAction(btn, "Installing...", "Install", installAction);
                });
                btn.text = "Install";
                btn.AddToClassList("action-button");
                buttonRow.Add(btn);
            }

            if (isInstalled && uninstallAction != null)
            {
                Button btn = null;
                btn = new Button(() =>
                {
                    if (!EditorUtility.DisplayDialog("Remove " + name,
                        $"Are you sure you want to remove {name}?", "Remove", "Cancel")) return;
                    RunDependencyAction(btn, "Removing...", "Uninstall", uninstallAction);
                });
                btn.text = "Uninstall";
                btn.AddToClassList("action-button");
                buttonRow.Add(btn);
            }

            if (buttonRow.childCount > 0)
                row.Add(buttonRow);

            parent.Add(row);
        }

        private static void RunDependencyAction(Button button, string busyText, string idleText, Action<Action> action)
        {
            if (dependencyActionInFlight) return;
            dependencyActionInFlight = true;
            button.SetEnabled(false);
            button.text = busyText;
            foreach (var window in OpenWindows)
                window.dependencySection?.SetEnabled(false);

            bool completed = false;
            Action restore = () =>
            {
                if (completed) return;
                completed = true;
                dependencyActionInFlight = false;
                button.SetEnabled(true);
                button.text = idleText;
                foreach (var window in OpenWindows)
                    window.dependencySection?.SetEnabled(true);
            };
            try { action(restore); }
            catch (Exception ex)
            {
                Debug.LogError($"[MCP] Dependency action failed: {ex.Message}");
                restore();
            }
        }

        private static void InstallUpmPackage(string packageId, Action onComplete = null)
        {
            BatchUpmAdd(new[] { packageId }, onComplete);
        }

        private static void RemoveUpmPackage(string packageId, Action onComplete = null)
        {
            BatchUpmRemove(new[] { packageId }, onComplete);
        }

        private static void BatchUpmAdd(string[] packageIds, Action onComplete = null)
        {
            var request = UnityEditor.PackageManager.Client.AddAndRemove(packageIds, null);
            EditorUtility.DisplayProgressBar("Installing Packages", $"Installing {packageIds.Length} package(s)...", 0.5f);
            EditorCoroutineUtility.StartCoroutineOwnerless(PollUpmRequest(request, "install", onComplete));
        }

        private static void BatchUpmRemove(string[] packageIds, Action onComplete = null)
        {
            var request = UnityEditor.PackageManager.Client.AddAndRemove(null, packageIds);
            EditorUtility.DisplayProgressBar("Removing Packages", $"Removing {packageIds.Length} package(s)...", 0.5f);
            EditorCoroutineUtility.StartCoroutineOwnerless(PollUpmRequest(request, "remove", onComplete));
        }

        private static IEnumerator PollUpmRequest(UnityEditor.PackageManager.Requests.AddAndRemoveRequest request, string verb, Action onComplete)
        {
            try
            {
                while (true)
                {
                    bool completed = false;
                    bool failed = false;
                    try { completed = request.IsCompleted; }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[MCP] Package {verb} failed: {ex.Message}");
                        failed = true;
                    }
                    if (failed) yield break;
                    if (completed) break;
                    yield return null;
                }

                try
                {
                    if (request.Status == UnityEditor.PackageManager.StatusCode.Success)
                        Debug.Log($"[MCP] Package {verb} succeeded.");
                    else
                        Debug.LogError($"[MCP] Package {verb} failed: {request.Error?.message}");
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[MCP] Package {verb} failed: {ex.Message}");
                }
            }
            finally
            {
                EditorUtility.ClearProgressBar();
                onComplete?.Invoke();
            }
        }

        private static void UninstallRoslyn()
        {
            string folder = System.IO.Path.Combine(Application.dataPath, "Plugins/Roslyn");
            if (System.IO.Directory.Exists(folder))
            {
                System.IO.Directory.Delete(folder, true);
                string metaPath = folder + ".meta";
                if (System.IO.File.Exists(metaPath))
                    System.IO.File.Delete(metaPath);
                AssetDatabase.Refresh();
                Debug.Log("[MCP] Roslyn DLLs removed from Assets/Plugins/Roslyn/");
            }
        }

        private static bool IsUpmPackageInstalled(string packageId)
        {
            // Check manifest.json directly — faster than async UPM API
            string manifestPath = System.IO.Path.Combine(Application.dataPath, "../Packages/manifest.json");
            if (!System.IO.File.Exists(manifestPath)) return false;
            string manifest = System.IO.File.ReadAllText(manifestPath);
            return manifest.Contains($"\"{packageId}\"");
        }
    }
}
