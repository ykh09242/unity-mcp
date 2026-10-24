using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Dependencies;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.Branding;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    /// <summary>
    /// Setup window for checking and guiding dependency installation
    /// </summary>
    public class MCPSetupWindow : EditorWindow
    {
        // UI Elements
        private VisualElement pythonIndicator;
        private Label pythonVersion;
        private Label pythonDetails;
        private VisualElement uvIndicator;
        private Label uvVersion;
        private Label uvDetails;
        private VisualElement gitIndicator;
        private Label gitVersion;
        private Label gitDetails;
        private Label statusMessage;
        private Label dependencyCheckError;
        private VisualElement installationSection;
        private Label installationInstructions;
        private Button openPythonLinkButton;
        private Button openUvLinkButton;
        private Button installUvButton;
        private Button refreshButton;
        private Button doneButton;

        // Tracks an in-flight uv install so completion is handled on the main thread.
        private Task<UvInstaller.UvInstallResult> _uvInstallTask;

        // Step 2 (Configure Clients) UI elements
        private VisualElement stepDeps;
        private VisualElement stepClients;
        private VisualElement clientsList;
        private Button skipClientsButton;
        private Button configureSelectedButton;
        private readonly List<(IMcpClientConfigurator client, Toggle toggle)> clientToggles = new();

        private Label clientsResult;
        private readonly HashSet<string> configuredClients = new();
        private readonly Dictionary<string, bool> clientSelections = new();
        private readonly Dictionary<string, string> clientResults = new();
        private readonly Dictionary<string, Label> clientResultLabels = new();
        private Func<IReadOnlyList<IMcpClientConfigurator>> getClients = () => McpClientRegistry.All;
        private Action<IMcpClientConfigurator> configureClient = client => MCPServiceLocator.Client.ConfigureClient(client);
        private Action finishSetup = null;

        private DependencyCheckResult _dependencyResult;
        private string _dependencyCheckError;
        private Task<DependencyCheckResult> _dependencyTask;
        private static Func<bool, Task<DependencyCheckResult>> dependencyCheck = DependencyManager.CheckAllDependenciesAsync;

        public static void ShowWindow(DependencyCheckResult dependencyResult = null)
        {
            var window = GetWindow<MCPSetupWindow>("MCP Setup");
            window.minSize = new Vector2(480, 320);
            if (dependencyResult != null)
                window.SetDependencyResult(dependencyResult);
            else
                window.BeginDependencyCheck(false);
            window.Show();
        }

        private void SetDependencyResult(DependencyCheckResult dependencyResult)
        {
            _dependencyTask = null;
            EditorApplication.update -= PollDependencies;
            _dependencyCheckError = DependencyFailure(dependencyResult);
            _dependencyResult = _dependencyCheckError == null ? dependencyResult : null;
            if (statusMessage != null && installationSection != null && doneButton != null)
                UpdateUI();
        }

        public void CreateGUI()
        {
            string basePath = AssetPathUtility.GetMcpPackageRootPath();

            // Load UXML
            var visualTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>($"{basePath}/Editor/Windows/MCPSetupWindow.uxml");

            if (visualTree == null)
            {
                McpLog.Error($"Failed to load UXML at: {basePath}/Editor/Windows/MCPSetupWindow.uxml");
                return;
            }

            rootVisualElement.Clear();
            rootVisualElement.AddToClassList("mcp-editor");
            rootVisualElement.EnableInClassList("unity-theme-light", !EditorGUIUtility.isProSkin);
            rootVisualElement.EnableInClassList("unity-theme-dark", EditorGUIUtility.isProSkin);
            visualTree.CloneTree(rootVisualElement);

            // Embed the Ocean brand mark beside the title
            var setupHeader = rootVisualElement.Q<VisualElement>("setup-header");
            if (setupHeader != null && setupHeader.Q<OceanMark>() == null)
            {
                var logo = new OceanMark { name = "setup-logo" };
                logo.AddToClassList("setup-logo");
                setupHeader.Insert(0, logo);
            }

            // Cache UI elements
            pythonIndicator = rootVisualElement.Q<VisualElement>("python-indicator");
            pythonVersion = rootVisualElement.Q<Label>("python-version");
            pythonDetails = rootVisualElement.Q<Label>("python-details");
            uvIndicator = rootVisualElement.Q<VisualElement>("uv-indicator");
            uvVersion = rootVisualElement.Q<Label>("uv-version");
            uvDetails = rootVisualElement.Q<Label>("uv-details");
            gitIndicator = rootVisualElement.Q<VisualElement>("git-indicator");
            gitVersion = rootVisualElement.Q<Label>("git-version");
            gitDetails = rootVisualElement.Q<Label>("git-details");
            statusMessage = rootVisualElement.Q<Label>("status-message");
            dependencyCheckError = rootVisualElement.Q<Label>("dependency-check-error");
            dependencyCheckError.enableRichText = false;
            installationSection = rootVisualElement.Q<VisualElement>("installation-section");
            installationInstructions = rootVisualElement.Q<Label>("installation-instructions");
            openPythonLinkButton = rootVisualElement.Q<Button>("open-python-link-button");
            openUvLinkButton = rootVisualElement.Q<Button>("open-uv-link-button");
            installUvButton = rootVisualElement.Q<Button>("install-uv-button");
            refreshButton = rootVisualElement.Q<Button>("refresh-button");
            doneButton = rootVisualElement.Q<Button>("done-button");
            stepDeps = rootVisualElement.Q<VisualElement>("step-deps");
            stepClients = rootVisualElement.Q<VisualElement>("step-clients");
            clientsList = rootVisualElement.Q<VisualElement>("clients-list");
            clientsResult = rootVisualElement.Q<Label>("clients-result");
            skipClientsButton = rootVisualElement.Q<Button>("skip-clients-button");
            configureSelectedButton = rootVisualElement.Q<Button>("configure-selected-button");

            // Register callbacks
            refreshButton.clicked += OnRefreshClicked;
            doneButton.clicked += OnDoneClicked;
            openPythonLinkButton.clicked += OnOpenPythonInstallClicked;
            openUvLinkButton.clicked += OnOpenUvInstallClicked;
            if (installUvButton != null)
                installUvButton.clicked += OnInstallUvClicked;
            skipClientsButton.clicked += OnSkipClientsClicked;
            configureSelectedButton.clicked += OnConfigureSelectedClicked;
            rootVisualElement.Q<Button>("back-button").clicked += () =>
            {
                stepClients.AddToClassList("hidden");
                stepDeps.RemoveFromClassList("hidden");
            };

            // Initial update
            UpdateUI();
        }

        private void OnEnable()
        {
            // Delay initial capture so ShowWindow can provide an existing result first.
            EditorApplication.delayCall += BeginInitialDependencyCheck;
            // Resume polling if a uv install was still in flight when the window was last disabled,
            // so its completion is still processed (button reset, dependencies re-checked).
            if (_uvInstallTask != null)
            {
                EditorApplication.update -= PollUvInstall;
                EditorApplication.update += PollUvInstall;
            }
        }

        private void BeginInitialDependencyCheck()
        {
            if (this != null && _dependencyResult == null && _dependencyTask == null && _dependencyCheckError == null)
                BeginDependencyCheck(false);
        }

        private void OnRefreshClicked() => BeginDependencyCheck(true);

        private void BeginDependencyCheck(bool force)
        {
            if (_dependencyTask != null)
                return;
            _dependencyResult = null;
            _dependencyCheckError = null;
            EditorApplication.update -= PollDependencies;
            try
            {
                _dependencyTask = dependencyCheck(force) ?? throw new InvalidOperationException("The dependency probe did not start.");
                EditorApplication.update += PollDependencies;
            }
            catch (Exception ex)
            {
                _dependencyCheckError = SecretRedactor.Scrub(ex.GetBaseException().Message);
            }
            UpdateUI();
        }

        private void PollDependencies()
        {
            if (_dependencyTask == null || !_dependencyTask.IsCompleted)
                return;
            var task = _dependencyTask;
            _dependencyTask = null;
            EditorApplication.update -= PollDependencies;
            if (task.Status == TaskStatus.RanToCompletion)
            {
                SetDependencyResult(task.Result);
                return;
            }
            else if (task.IsFaulted)
                _dependencyCheckError = SecretRedactor.Scrub(task.Exception.GetBaseException().Message);
            else
                _dependencyCheckError = "The dependency check was cancelled.";
            UpdateUI();
        }

        private static string DependencyFailure(DependencyCheckResult result)
        {
            const string failurePrefix = "Dependency check failed:";
            if (result?.Summary?.StartsWith(failurePrefix, StringComparison.OrdinalIgnoreCase) == true)
            {
                string cause = result.Summary.Substring(failurePrefix.Length).Trim();
                return string.IsNullOrEmpty(cause) ? "The dependency probe did not complete." : SecretRedactor.Scrub(cause);
            }
            if (
                result?.Dependencies == null
                || !result.Dependencies.Exists(d => d?.Name == "Python")
                || !result.Dependencies.Exists(d => d?.Name == "uv Package Manager")
            )
                return "The check returned incomplete dependency results.";
            if (!result.IsSystemReady && !result.Dependencies.Exists(d => d != null && d.IsRequired && !d.IsAvailable))
                return "The check did not confirm system readiness.";
            return null;
        }

        private void OnDoneClicked()
        {
            if (_dependencyResult != null && _dependencyResult.IsSystemReady)
            {
                ShowClientsStep();
            }
            else
            {
                Setup.SetupWindowService.MarkSetupDismissed();
                Close();
            }
        }

        private void ShowClientsStep()
        {
            stepDeps.AddToClassList("hidden");
            stepClients.RemoveFromClassList("hidden");
            PopulateClientsList();
        }

        private void PopulateClientsList()
        {
            clientsList.Clear();
            clientToggles.Clear();
            clientResultLabels.Clear();
            configureSelectedButton.SetEnabled(true);
            foreach (var c in getClients())
            {
                if (!c.IsInstalled)
                    continue;
                bool configured = configuredClients.Contains(c.Id);
                var toggle = new Toggle(c.DisplayName)
                {
                    value = !configured && (!clientSelections.TryGetValue(c.Id, out bool selected) || selected),
                    tooltip = c.GetConfigPath(),
                };
                toggle.SetEnabled(!configured);
                toggle.RegisterValueChangedCallback(evt => clientSelections[c.Id] = evt.newValue);
                clientToggles.Add((c, toggle));
                clientsList.Add(toggle);
                var result = new Label(clientResults.TryGetValue(c.Id, out string message) ? message : "") { enableRichText = false };
                result.AddToClassList("help-text");
                clientsList.Add(result);
                clientResultLabels[c.Id] = result;
            }
            if (clientToggles.Count == 0)
            {
                var empty = new Label("No supported MCP clients detected.");
                empty.AddToClassList("help-text");
                clientsList.Add(empty);
                configureSelectedButton.SetEnabled(false);
            }
        }

        private void OnSkipClientsClicked() => CompleteSetup();

        private void CompleteSetup()
        {
            if (finishSetup != null)
                finishSetup();
            else
            {
                Setup.SetupWindowService.MarkSetupCompleted();
                Close();
            }
        }

        private void OnConfigureSelectedClicked()
        {
            int success = 0,
                failure = 0;
            foreach (var (client, toggle) in clientToggles)
            {
                if (!toggle.value || configuredClients.Contains(client.Id))
                    continue;
                string result;
                try
                {
                    configureClient(client);
                    success++;
                    configuredClients.Add(client.Id);
                    clientSelections[client.Id] = false;
                    toggle.SetValueWithoutNotify(false);
                    toggle.SetEnabled(false);
                    result = "Configured. This client will not be repeated during retry.";
                }
                catch (Exception)
                {
                    failure++;
                    clientSelections[client.Id] = true;
                    result = "Configuration failed. Check the client installation and file permissions, then retry.";
                }
                clientResults[client.Id] = result;
                if (clientResultLabels.TryGetValue(client.Id, out var label))
                    label.text = result;
            }
            clientsResult.text = success + " configured, " + failure + " failed in this attempt. ";
            if (failure > 0)
            {
                clientsResult.text += "Failed selections are kept. Retry them or choose Skip to finish without them.";
                configureSelectedButton.text = "Retry selected failures";
                return;
            }
            if (success == 0)
            {
                clientsResult.text = "Select a client to configure, or choose Skip to finish.";
                return;
            }
            CompleteSetup();
        }

        private void OnOpenPythonInstallClicked()
        {
            var (pythonUrl, _) = DependencyManager.GetInstallationUrls();
            Application.OpenURL(pythonUrl);
        }

        private void OnOpenUvInstallClicked()
        {
            var (_, uvUrl) = DependencyManager.GetInstallationUrls();
            Application.OpenURL(uvUrl);
        }

        private void OnInstallUvClicked()
        {
            if (_uvInstallTask != null)
                return; // already running

            bool proceed = EditorUtility.DisplayDialog(
                "Install UV",
                "This will download and run the official uv installer:\n\n" + UvInstaller.DescribeCommand() + "\n\nContinue?",
                "Install",
                "Cancel"
            );
            if (!proceed)
                return;

            installUvButton.SetEnabled(false);
            installUvButton.text = "Installing UV…";
            statusMessage.text = "Installing uv… this can take a moment.";
            statusMessage.RemoveFromClassList("status-success");
            statusMessage.AddToClassList("status-warning");

            _uvInstallTask = Task.Run(() => UvInstaller.Run());
            EditorApplication.update += PollUvInstall;
        }

        private void PollUvInstall()
        {
            // The window/UI may have been torn down while the task ran — stop polling and drop it
            // (guards against dereferencing UI fields after teardown).
            if (installUvButton == null || installUvButton.panel == null)
            {
                EditorApplication.update -= PollUvInstall;
                return;
            }
            if (_uvInstallTask == null || !_uvInstallTask.IsCompleted)
                return;

            EditorApplication.update -= PollUvInstall;
            var task = _uvInstallTask;
            _uvInstallTask = null;

            installUvButton.SetEnabled(true);
            installUvButton.text = "Install uv";

            // UvInstaller.Run catches its own exceptions, so the task always completes with a result.
            UvInstaller.UvInstallResult result = task.Result;

            if (result.Success)
            {
                BeginDependencyCheck(true);
            }
            else
            {
                // Reset the status label off the "Installing…" state before reporting the failure.
                UpdateUI();
                EditorUtility.DisplayDialog(
                    "Install UV Failed",
                    "The installer did not complete successfully. You can install uv manually via \"Open UV Install Page\".\n\n" + result.Output,
                    "OK"
                );
            }
        }

        private void OnDisable()
        {
            EditorApplication.update -= PollUvInstall;
            EditorApplication.update -= PollDependencies;
            EditorApplication.delayCall -= BeginInitialDependencyCheck;
            _dependencyTask = null;
        }

        private void UpdateUI()
        {
            if (statusMessage == null || installationSection == null || doneButton == null)
                return;
            bool checking = _dependencyTask != null;
            refreshButton?.SetEnabled(!checking);
            doneButton.SetEnabled(!checking);
            statusMessage.RemoveFromClassList("status-success");
            statusMessage.RemoveFromClassList("status-warning");
            statusMessage.RemoveFromClassList("status-error");
            installationSection.RemoveFromClassList("visible");
            if (installationInstructions != null)
                installationInstructions.text = "";
            openPythonLinkButton?.AddToClassList("hidden");
            openUvLinkButton?.AddToClassList("hidden");
            installUvButton?.AddToClassList("hidden");
            dependencyCheckError?.AddToClassList("hidden");
            if (dependencyCheckError != null)
                dependencyCheckError.text = "";
            ResetDependencyStatus(pythonIndicator, pythonVersion, pythonDetails);
            ResetDependencyStatus(uvIndicator, uvVersion, uvDetails);
            ResetDependencyStatus(gitIndicator, gitVersion, gitDetails);
            if (refreshButton != null)
                refreshButton.text = "Refresh";
            doneButton.text = "Close";
            if (checking)
            {
                statusMessage.text = "Checking dependencies...";
                return;
            }
            if (_dependencyCheckError != null)
            {
                statusMessage.text = "Dependency check failed";
                statusMessage.AddToClassList("status-error");
                if (dependencyCheckError != null)
                {
                    dependencyCheckError.text = _dependencyCheckError + " Retry to check requirements again.";
                    dependencyCheckError.RemoveFromClassList("hidden");
                }
                if (refreshButton != null)
                    refreshButton.text = "Retry";
                return;
            }
            if (_dependencyResult == null)
            {
                statusMessage.text = "Requirements have not been checked. Refresh to check them.";
                return;
            }

            // Update Python status
            var pythonDep = _dependencyResult.Dependencies.Find(d => d?.Name == "Python");
            if (pythonDep != null)
            {
                UpdateDependencyStatus(pythonIndicator, pythonVersion, pythonDetails, pythonDep);
            }

            // Update uv status
            var uvDep = _dependencyResult.Dependencies.Find(d => d?.Name == "uv Package Manager");
            if (uvDep != null)
            {
                UpdateDependencyStatus(uvIndicator, uvVersion, uvDetails, uvDep);
            }

            // Update git status (optional dependency: never blocks readiness)
            var gitDep = _dependencyResult.Dependencies.Find(d => d?.Name == "Git");
            if (gitDep != null)
            {
                UpdateDependencyStatus(gitIndicator, gitVersion, gitDetails, gitDep);
            }

            // Update overall status
            bool requiredMissing = _dependencyResult.Dependencies.Exists(d => d != null && d.IsRequired && !d.IsAvailable);
            if (_dependencyResult.IsSystemReady && !requiredMissing)
            {
                statusMessage.text = "System requirements met";
                statusMessage.RemoveFromClassList("status-warning");
                statusMessage.AddToClassList("status-success");
                installationSection.RemoveFromClassList("visible");
                doneButton.text = "Next";
            }
            else
            {
                statusMessage.text = "Required dependencies missing";
                statusMessage.RemoveFromClassList("status-success");
                statusMessage.AddToClassList("status-warning");
                installationSection.AddToClassList("visible");
                doneButton.text = "Close";
                bool pythonMissing = pythonDep != null && !pythonDep.IsAvailable;
                bool uvMissing = uvDep != null && !uvDep.IsAvailable;
                openPythonLinkButton?.EnableInClassList("hidden", !pythonMissing);
                openUvLinkButton?.EnableInClassList("hidden", !uvMissing);
                installUvButton?.EnableInClassList("hidden", !uvMissing || !UvInstaller.IsSupported || _uvInstallTask != null);
                var instructions = new List<string>();
                if (pythonMissing)
                    instructions.Add("Install Python 3.11+ using Python Downloads.");
                if (uvMissing)
                    instructions.Add(UvInstaller.IsSupported ? "Install uv using Install uv or uv Downloads." : "Install uv using uv Downloads.");
                instructions.Add("Refresh after installation to check requirements again.");
                if (installationInstructions != null)
                    installationInstructions.text = string.Join("\n", instructions);
            }
        }

        private static void ResetDependencyStatus(VisualElement indicator, Label versionLabel, Label detailsLabel)
        {
            indicator?.RemoveFromClassList("valid");
            indicator?.RemoveFromClassList("invalid");
            if (versionLabel != null)
                versionLabel.text = "Not checked";
            if (detailsLabel != null)
            {
                detailsLabel.text = "";
                detailsLabel.RemoveFromClassList("status-error");
            }
        }

        internal static void UpdateDependencyStatus(VisualElement indicator, Label versionLabel, Label detailsLabel, DependencyStatus dep)
        {
            if (indicator == null || versionLabel == null || detailsLabel == null)
                return;
            if (dep.IsAvailable)
            {
                indicator.RemoveFromClassList("invalid");
                indicator.AddToClassList("valid");
                versionLabel.text = $"v{dep.Version}";
                detailsLabel.text = dep.Details ?? "Available";
                detailsLabel.RemoveFromClassList("status-error");
            }
            else if (dep.IsRequired)
            {
                indicator.RemoveFromClassList("valid");
                indicator.AddToClassList("invalid");
                versionLabel.text = "Not Found";
                detailsLabel.text = dep.ErrorMessage ?? "Not available";
                detailsLabel.AddToClassList("status-error");
            }
            else
            {
                // A missing optional dependency is information, not a blocker. Drop both state
                // classes so the dot keeps the neutral grey of .status-indicator-small instead of
                // the red .invalid reserved for required ones, and say what it is for.
                indicator.RemoveFromClassList("valid");
                indicator.RemoveFromClassList("invalid");
                versionLabel.text = "Not Found";
                detailsLabel.text = dep.Details ?? dep.ErrorMessage ?? "Not available";
                detailsLabel.RemoveFromClassList("status-error");
            }
        }
    }
}
