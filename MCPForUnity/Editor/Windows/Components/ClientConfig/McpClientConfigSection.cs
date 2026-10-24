using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Setup;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.ClientConfig
{
    /// <summary>
    /// Controller for the Client Configuration section of the MCP for Unity editor window.
    /// Handles client selection, configuration, status display, and manual configuration details.
    /// </summary>
    public class McpClientConfigSection
    {
        // UI Elements
        private ClientConfigurationChangeService configurationChanges = ClientConfigurationChangeService.Shared;
        private ClientConfigurationPreview configurationPreview;
        private ClientConfigurationReceipt configurationReceipt;
        private Button previewConfigurationButton,
            applyConfigurationButton,
            restoreConfigurationButton,
            cancelConfigurationPreviewButton;
        private TextField configurationPreviewText;
        private Label configurationChangeStatusLabel;
        private DropdownField clientDropdown;
        private Button configureAllButton;
        private Button retryFailedButton;
        private Label retryStatusLabel;
        private ClientConfigurationAttempt retryAttempt;
        private bool configurationInProgress;
        private Action<string, string> showConfigurationResult = null;
        private VisualElement clientStatusIndicator;
        private Label clientStatusLabel;
        private Button configureButton;
        private Button installSkillsButton;
        private VisualElement claudeCliPathRow;
        private TextField claudeCliPath;
        private Button browseClaudeButton;
        private VisualElement clientProjectDirRow;
        private TextField clientProjectDirField;
        private Button browseProjectDirButton;
        private Button clearProjectDirButton;
        private Foldout clientDetailsFoldout;
        private Foldout manualConfigFoldout;
        private TextField configPathField;
        private Button copyPathButton;
        private Button openFileButton;
        private TextField configJsonField;
        private Button copyJsonButton;
        private Label installationStepsLabel;

        // Data
        private readonly List<IMcpClientConfigurator> configurators;
        private readonly Dictionary<IMcpClientConfigurator, DateTime> lastStatusChecks = new();
        private static readonly TimeSpan StatusRefreshInterval = TimeSpan.FromSeconds(45);
        private int selectedClientIndex = 0;
        private bool isSkillSyncInProgress;
        private CancellationTokenSource cliLifetime = new();
        private int cliRevision;
        private readonly Dictionary<IMcpClientConfigurator, CliStatusState> cliStatuses = new();
        private Func<Action, Task> runCliWork = action => Task.Run(action);
        private bool cliActionInProgress;

        private sealed class CliStatusContext : IEquatable<CliStatusContext>
        {
            internal string ProjectDir,
                Executable,
                PackageSource,
                HttpUrl,
                UvxPath,
                UserConfigPath;
            internal bool UseHttp,
                Remote,
                HasProjectOverride;
            internal RuntimePlatform Platform;
            internal ConfiguredTransport Transport;
            internal Dictionary<string, string> Headers;
            internal string HeaderIdentity;

            public bool Equals(CliStatusContext other) =>
                other != null
                && ProjectDir == other.ProjectDir
                && Executable == other.Executable
                && PackageSource == other.PackageSource
                && HttpUrl == other.HttpUrl
                && UvxPath == other.UvxPath
                && UserConfigPath == other.UserConfigPath
                && UseHttp == other.UseHttp
                && Remote == other.Remote
                && HasProjectOverride == other.HasProjectOverride
                && Platform == other.Platform
                && Transport == other.Transport
                && HeaderIdentity == other.HeaderIdentity;
        }

        private sealed class CliStatusState
        {
            internal CliStatusContext Context;
            internal ClaudeCliMcpConfigurator Client;
            internal int Revision;
            internal CancellationToken Token;
            internal bool Pending = true;
        }

        private static CliStatusContext CaptureCliContext()
        {
            bool http = EditorConfigurationCache.Instance.UseHttpTransport;
            bool remote = HttpEndpointUtility.IsRemoteScope();
            var headers = http && remote ? new Dictionary<string, string>(HttpEndpointUtility.GetAuthHeaders()) : null;
            string headerIdentity = null;
            if (headers != null)
            {
                // Cache identity never contains a credential. Keep captured headers private to
                // this operation for validation; do not include them in diagnostics or logs.
                var content = new StringBuilder();
                foreach (var header in headers.OrderBy(pair => pair.Key, StringComparer.Ordinal))
                    content.Append(header.Key.Length).Append(':').Append(header.Key).Append(header.Value.Length).Append(':').Append(header.Value);
                using var hash = SHA256.Create();
                headerIdentity = Convert.ToBase64String(hash.ComputeHash(Encoding.UTF8.GetBytes(content.ToString())));
            }
            return new CliStatusContext
            {
                ProjectDir = ClaudeCliMcpConfigurator.GetClientProjectDir(),
                Executable = MCPServiceLocator.Paths.GetClaudeCliPath(),
                UseHttp = http,
                Remote = remote,
                HasProjectOverride = ClaudeCliMcpConfigurator.HasClientProjectDirOverride,
                Platform = Application.platform,
                Transport = HttpEndpointUtility.GetCurrentServerTransport(),
                PackageSource = http ? null : GetExpectedPackageSourceForCurrentPackage(),
                UvxPath = http ? null : MCPServiceLocator.Paths.GetUvxPath(),
                HttpUrl = http ? HttpEndpointUtility.GetMcpRpcUrl() : null,
                UserConfigPath = ClaudeCliMcpConfigurator.GetUserConfigPath(),
                Headers = headers,
                HeaderIdentity = headerIdentity,
            };
        }

        private CliStatusState BeginCliStatus(IMcpClientConfigurator client, CliStatusContext context)
        {
            var state = new CliStatusState
            {
                Context = context,
                Client = ((ClaudeCliMcpConfigurator)client).CreateOperationClient(context.UserConfigPath),
                Revision = cliRevision,
                Token = cliLifetime.Token,
            };
            cliStatuses[client] = state;
            lastStatusChecks.Remove(client);
            return state;
        }

        private static bool TryCaptureCliContext(out CliStatusContext context)
        {
            try
            {
                context = CaptureCliContext();
                return true;
            }
            catch
            {
                context = null;
                return false;
            }
        }

        private bool IsCurrentCliState(IMcpClientConfigurator client, CliStatusState state) =>
            !state.Token.IsCancellationRequested
            && IsCurrentCliOperation(state.Revision, client)
            && cliStatuses.TryGetValue(client, out var current)
            && ReferenceEquals(current, state)
            && TryCaptureCliContext(out var context)
            && state.Context.Equals(context);

        private static void ProbeCliStatus(CliStatusState state)
        {
            state.Token.ThrowIfCancellationRequested();
            var context = state.Context;
            state.Client.CheckStatusWithProjectDir(
                context.ProjectDir,
                context.UseHttp,
                context.Executable,
                context.Platform,
                context.Remote,
                context.PackageSource,
                attemptAutoRewrite: false,
                hasProjectDirOverride: context.HasProjectOverride,
                expectedHttpUrl: context.HttpUrl,
                expectedHttpHeaders: context.Headers
            );
        }

        // Events
        /// <summary>
        /// Fired when the selected client's configured transport is detected/updated.
        /// The parameter contains the client name and its configured transport.
        /// </summary>
        public event Action<string, ConfiguredTransport> OnClientTransportDetected;

        /// <summary>
        /// Fired when a config mismatch is detected (e.g., version mismatch).
        /// The parameter contains the client name and the mismatch message (null if no mismatch).
        /// </summary>
        public event Action<string, string> OnClientConfigMismatch;

        public VisualElement Root { get; private set; }

        public McpClientConfigSection(VisualElement root)
        {
            Root = root;
            showConfigurationResult = (title, message) => EditorUtility.DisplayDialog(title, message, "OK");
            configurators = MCPServiceLocator.Client.GetAllClients().ToList();
            CacheUIElements();
            InitializeUI();
            RefreshRestoreAvailability();
            RegisterCallbacks();
            Root.RegisterCallback<DetachFromPanelEvent>(_ =>
            {
                cliRevision++;
                cliLifetime.Cancel();
                foreach (var client in cliStatuses.Keys)
                    lastStatusChecks.Remove(client);
                cliStatuses.Clear();
            });
            Root.RegisterCallback<AttachToPanelEvent>(_ =>
            {
                if (cliLifetime.IsCancellationRequested)
                {
                    cliLifetime.Dispose();
                    cliLifetime = new CancellationTokenSource();
                    UpdateClientStatus();
                }
            });
        }

        private void CacheUIElements()
        {
            clientDropdown = Root.Q<DropdownField>("client-dropdown");
            previewConfigurationButton = Root.Q<Button>("preview-configuration");
            applyConfigurationButton = Root.Q<Button>("apply-configuration");
            restoreConfigurationButton = Root.Q<Button>("restore-configuration");
            cancelConfigurationPreviewButton = Root.Q<Button>("cancel-configuration-preview");
            configurationPreviewText = Root.Q<TextField>("configuration-preview");
            configurationChangeStatusLabel = Root.Q<Label>("configuration-change-status");
            configureAllButton = Root.Q<Button>("configure-all-button");
            retryFailedButton = Root.Q<Button>("retry-failed-button");
            retryStatusLabel = Root.Q<Label>("retry-status");
            clientStatusIndicator = Root.Q<VisualElement>("client-status-indicator");
            clientStatusLabel = Root.Q<Label>("client-status");
            configureButton = Root.Q<Button>("configure-button");
            installSkillsButton = Root.Q<Button>("install-skills-button");
            claudeCliPathRow = Root.Q<VisualElement>("claude-cli-path-row");
            claudeCliPath = Root.Q<TextField>("claude-cli-path");
            browseClaudeButton = Root.Q<Button>("browse-claude-button");
            clientProjectDirRow = Root.Q<VisualElement>("client-project-dir-row");
            clientProjectDirField = Root.Q<TextField>("client-project-dir");
            browseProjectDirButton = Root.Q<Button>("browse-project-dir-button");
            clearProjectDirButton = Root.Q<Button>("clear-project-dir-button");
            clientDetailsFoldout = Root.Q<Foldout>("client-details-foldout");
            manualConfigFoldout = Root.Q<Foldout>("manual-config-foldout");
            configPathField = Root.Q<TextField>("config-path");
            copyPathButton = Root.Q<Button>("copy-path-button");
            openFileButton = Root.Q<Button>("open-file-button");
            configJsonField = Root.Q<TextField>("config-json");
            copyJsonButton = Root.Q<Button>("copy-json-button");
            installationStepsLabel = Root.Q<Label>("installation-steps");
        }

        private void InitializeUI()
        {
            SetRetryAttempt(null);
            applyConfigurationButton?.SetEnabled(false);
            restoreConfigurationButton?.SetEnabled(false);
            // Ensure manual config foldout starts collapsed
            if (manualConfigFoldout != null)
            {
                manualConfigFoldout.value = false;
            }

            // Default the per-client setup foldout to expanded. The Configure / Unregister
            // button for the currently-selected client lives inside it, and the 9.7.0
            // default-collapsed behavior made users believe the Unregister action had been
            // removed (community report: "the Unregister button was removed in 9.7.0,
            // something is stuck on stdio and I can't switch to local"). The state still
            // persists per-user, so anyone who explicitly collapses it keeps that preference.
            if (clientDetailsFoldout != null)
            {
                clientDetailsFoldout.value = EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen, true);
                clientDetailsFoldout.RegisterValueChangedCallback(evt =>
                {
                    if (evt.target == clientDetailsFoldout)
                        EditorPrefs.SetBool(EditorPrefKeys.ClientDetailsFoldoutOpen, evt.newValue);
                });
            }

            var clientNames = configurators.Select(c => c.DisplayName).ToList();
            clientDropdown.choices = clientNames;
            if (clientNames.Count > 0)
            {
                // Restore last selected client from EditorPrefs
                string lastClientId = EditorPrefs.GetString(EditorPrefKeys.LastSelectedClientId, string.Empty);
                int restoredIndex = FindConfiguratorIndex(lastClientId);
                if (restoredIndex < 0)
                    restoredIndex = 0;

                clientDropdown.index = restoredIndex;
                selectedClientIndex = restoredIndex;
            }

            claudeCliPathRow.style.display = DisplayStyle.None;
            clientProjectDirRow.style.display = DisplayStyle.None;

            // Initialize the configuration display for the first selected client
            UpdateClientStatus();
            UpdateManualConfiguration();
            UpdateClaudeCliPathVisibility();
            UpdateClientProjectDirVisibility();
            UpdateInstallSkillsVisibility();
        }

        private void RegisterCallbacks()
        {
            if (previewConfigurationButton != null)
                previewConfigurationButton.clicked += OnPreviewConfigurationClicked;
            if (applyConfigurationButton != null)
                applyConfigurationButton.clicked += OnApplyConfigurationPreviewClicked;
            if (restoreConfigurationButton != null)
                restoreConfigurationButton.clicked += OnRestoreConfigurationClicked;
            if (cancelConfigurationPreviewButton != null)
                cancelConfigurationPreviewButton.clicked += OnCancelConfigurationPreviewClicked;
            clientDropdown.RegisterValueChangedCallback(evt =>
            {
                int selectedIndex = GetIndexForDropdownValue(evt.newValue);
                if (selectedIndex < 0)
                {
                    selectedIndex = clientDropdown.index;
                }
                if (selectedIndex < 0 || selectedIndex >= configurators.Count)
                {
                    return;
                }

                OnCancelConfigurationPreviewClicked();
                selectedClientIndex = selectedIndex;
                cliRevision++;
                RefreshRestoreAvailability();
                // Persist the selected client so it's restored on next window open
                if (selectedClientIndex >= 0 && selectedClientIndex < configurators.Count)
                {
                    EditorPrefs.SetString(EditorPrefKeys.LastSelectedClientId, configurators[selectedClientIndex].Id);
                }
                UpdateClientStatus();
                UpdateManualConfiguration();
                UpdateClaudeCliPathVisibility();
                UpdateClientProjectDirVisibility();
                UpdateInstallSkillsVisibility();
            });

            configureAllButton.clicked += OnConfigureAllClientsClicked;
            if (retryFailedButton != null)
                retryFailedButton.clicked += OnRetryFailedClientsClicked;
            configureButton.clicked += OnConfigureClicked;
            installSkillsButton.clicked += OnInstallSkillsClicked;
            browseClaudeButton.clicked += OnBrowseClaudeClicked;
            browseProjectDirButton.clicked += OnBrowseProjectDirClicked;
            clearProjectDirButton.clicked += OnClearProjectDirClicked;
            copyPathButton.clicked += OnCopyPathClicked;
            openFileButton.clicked += OnOpenFileClicked;
            copyJsonButton.clicked += OnCopyJsonClicked;
        }

        public void UpdateClientStatus()
        {
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            var client = configurators[selectedClientIndex];
            RefreshClientStatus(client);
        }

        private string GetStatusDisplayString(McpStatus status)
        {
            return status switch
            {
                McpStatus.NotConfigured => "Not Configured",
                McpStatus.Configured => "Configured",
                McpStatus.Running => "Running",
                McpStatus.Connected => "Connected",
                McpStatus.IncorrectPath => "Incorrect Path",
                McpStatus.CommunicationError => "Communication Error",
                McpStatus.NoResponse => "No Response",
                McpStatus.UnsupportedOS => "Unsupported OS",
                McpStatus.MissingConfig => "Missing MCPForUnity Config",
                McpStatus.Error => "Error",
                McpStatus.VersionMismatch => "Version Mismatch",
                _ => "Unknown",
            };
        }

        public void UpdateManualConfiguration()
        {
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            var client = configurators[selectedClientIndex];

            try
            {
                string configPath = client.GetConfigPath();
                string configJson = client.GetManualSnippet();
                var steps = client.GetInstallationSteps();
                string instructions =
                    steps != null && steps.Count > 0
                        ? string.Join("\n", steps.Select((s, i) => $"{i + 1}. {s}"))
                        : "Configuration steps not available for this client.";

                configPathField.value = configPath;
                configJsonField.value = configJson;
                installationStepsLabel.text = instructions;
                copyPathButton.SetEnabled(true);
                openFileButton.SetEnabled(true);
                copyJsonButton.SetEnabled(true);
            }
            catch (Exception ex)
            {
                configPathField.value = string.Empty;
                configJsonField.value = string.Empty;
                installationStepsLabel.text = $"Configuration unavailable: {ex.Message}";
                copyPathButton.SetEnabled(false);
                openFileButton.SetEnabled(false);
                copyJsonButton.SetEnabled(false);
            }
        }

        private void UpdateClaudeCliPathVisibility()
        {
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            var client = configurators[selectedClientIndex];

            if (client is ClaudeCliMcpConfigurator)
            {
                string claudePath = MCPServiceLocator.Paths.GetClaudeCliPath();
                if (string.IsNullOrEmpty(claudePath))
                {
                    claudeCliPathRow.style.display = DisplayStyle.Flex;
                    claudeCliPath.value = "Not found - click Browse to select";
                }
                else
                {
                    claudeCliPathRow.style.display = DisplayStyle.Flex;
                    claudeCliPath.value = claudePath;
                }
            }
            else
            {
                claudeCliPathRow.style.display = DisplayStyle.None;
            }
        }

        private void UpdateClientProjectDirVisibility()
        {
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            var client = configurators[selectedClientIndex];

            if (client is ClaudeCliMcpConfigurator)
            {
                clientProjectDirRow.style.display = DisplayStyle.Flex;
                string projectDir = ClaudeCliMcpConfigurator.GetClientProjectDir();
                if (ClaudeCliMcpConfigurator.HasClientProjectDirOverride)
                {
                    clientProjectDirField.value = projectDir + "  (override)";
                }
                else
                {
                    clientProjectDirField.value = projectDir;
                }
            }
            else
            {
                clientProjectDirRow.style.display = DisplayStyle.None;
            }
        }

        private IMcpClientConfigurator PreviewClient =>
            selectedClientIndex >= 0 && selectedClientIndex < configurators.Count ? configurators[selectedClientIndex] : null;

        private void RefreshRestoreAvailability()
        {
            configurationReceipt = configurationChanges?.RestoreReceiptFor(PreviewClient);
            restoreConfigurationButton?.SetEnabled(configurationReceipt != null);
        }

        private void OnPreviewConfigurationClicked()
        {
            RefreshRestoreAvailability();
            configurationPreview = configurationChanges.Prepare(PreviewClient);
            if (configurationPreviewText != null)
                configurationPreviewText.value = configurationPreview.CanApply
                    ? configurationPreview.DisplayText
                    : ConfigurationStatusMessage(configurationPreview.Code, PreviewClient?.DisplayName);
            if (configurationChangeStatusLabel != null)
                configurationChangeStatusLabel.text = ConfigurationStatusMessage(configurationPreview.Code, PreviewClient?.DisplayName);
            applyConfigurationButton?.SetEnabled(configurationPreview.CanApply);
        }

        private void OnCancelConfigurationPreviewClicked()
        {
            configurationChanges?.CancelPreview();
            configurationPreview = null;
            applyConfigurationButton?.SetEnabled(false);
            if (configurationPreviewText != null)
                configurationPreviewText.value = "";
            if (configurationChangeStatusLabel != null)
                configurationChangeStatusLabel.text = "";
        }

        private void OnApplyConfigurationPreviewClicked()
        {
            var result = configurationChanges.Apply(configurationPreview, PreviewClient);
            OnCancelConfigurationPreviewClicked();
            if (result.Code == "applied")
            {
                configurationReceipt = result.Receipt;
                DiscardRetryAttempt();
            }
            restoreConfigurationButton?.SetEnabled(configurationReceipt != null);
            if (configurationChangeStatusLabel != null)
                configurationChangeStatusLabel.text = ConfigurationStatusMessage(result.Code, PreviewClient?.DisplayName);
        }

        private void OnRestoreConfigurationClicked()
        {
            var result = configurationChanges.Restore(configurationReceipt, PreviewClient);
            OnCancelConfigurationPreviewClicked();
            if (result.Code == "restored")
            {
                configurationReceipt = null;
                DiscardRetryAttempt();
            }
            restoreConfigurationButton?.SetEnabled(configurationReceipt != null);
            if (configurationChangeStatusLabel != null)
                configurationChangeStatusLabel.text = ConfigurationStatusMessage(result.Code, PreviewClient?.DisplayName);
        }

        internal static string ConfigurationStatusMessage(string code, string client)
        {
            string name = string.IsNullOrWhiteSpace(client) ? "Selected client" : client;
            string message = code switch
            {
                "prepared" => "Preview ready. Review the change, then Apply or Cancel.",
                "applied" => "Saved. Reconnect the client to use this configuration.",
                "restored" => "Restored the previous Unity MCP entry. Reconnect the client to use it.",
                "unchanged" => "Already matches. No file was changed.",
                "locked" => "Configuration is locked. Review and unlock editing before retrying.",
                "stale_handle" => "This preview is no longer current. Preview again.",
                "stale_target" => "The configuration destination changed. Preview again.",
                "stale_context" => "Connection settings changed. Preview again.",
                "stale_file" => "The configuration file changed. Preview again to preserve those edits.",
                "owned_entry_changed" => "The Unity MCP entry changed after saving. Review it before restoring.",
                "unsupported_client" => "Preview is unavailable for this client. Use its manual configuration instructions.",
                "capability_unavailable" => "The required client capability is unavailable. Check the installed client and preview again.",
                "unsupported_path" => "The configuration path is unsupported. Choose a regular local file.",
                "unsupported_layout" => "The file layout is unsupported. Review it using the manual configuration instructions.",
                "invalid_configuration" => "The configuration is invalid. Correct the file or connection settings and preview again.",
                "input_limit" => "The configuration is too large for safe automatic editing. Use manual configuration.",
                _ => "The change could not be completed. Check file permissions and preview again.",
            };
            return name + ": " + message;
        }

        private void OnConfigureAllClientsClicked()
        {
            OnCancelConfigurationPreviewClicked();
            if (configurationInProgress)
                return;
            SetConfigurationBusy(true);
            SetRetryAttempt(null);
            try
            {
                var service = MCPServiceLocator.Client;
                var summary = service.ConfigureAllDetectedClients();
                bool supportsRetry = service is IClientConfigurationRetryService;
                SetRetryAttempt(supportsRetry ? summary.Attempt : null);
                if (!supportsRetry && retryStatusLabel != null)
                    retryStatusLabel.text = "Selective retry unavailable for this client service.";

                string headline =
                    summary.SkippedCount > 0
                        ? $"{summary.SuccessCount + summary.FailureCount} detected client(s) processed. ({summary.SkippedCount} not installed, skipped.)"
                        : summary.GetSummaryMessage();
                string message = headline + "\n\n";
                foreach (var msg in summary.Messages)
                {
                    message += msg + "\n";
                }

                showConfigurationResult?.Invoke("Configure Detected Clients", message);

                // The bulk path mutated state for every detected client. Clear the per-client
                // status cache so any subsequent dropdown switch (or the currently-selected
                // client refresh below) reads fresh status from disk instead of the pre-bulk
                // value — otherwise every other client in the dropdown looks like it still
                // has Missing/IncorrectPath status until 45 s elapses on the throttle.
                lastStatusChecks.Clear();

                if (selectedClientIndex >= 0 && selectedClientIndex < configurators.Count)
                {
                    UpdateClientStatus();
                    UpdateManualConfiguration();
                }
            }
            catch (Exception ex)
            {
                SetRetryAttempt(null);
                showConfigurationResult?.Invoke("Configuration Failed", ex.Message);
            }
            finally
            {
                SetConfigurationBusy(false);
            }
        }

        private void SetConfigurationBusy(bool busy)
        {
            configurationInProgress = busy;
            configureAllButton?.SetEnabled(!busy);
            retryFailedButton?.SetEnabled(!busy && retryAttempt != null && retryAttempt.CanRetry);
        }

        private void SetRetryAttempt(ClientConfigurationAttempt attempt)
        {
            retryAttempt = attempt;
            retryFailedButton?.SetEnabled(!configurationInProgress && attempt != null && attempt.CanRetry);
            if (retryStatusLabel == null)
                return;
            retryStatusLabel.text =
                attempt == null ? "Uses current settings; successful clients are not changed."
                : attempt.CanRetry
                    ? $"{attempt.RemainingFailureCount} failed client(s) remain. Retry uses current settings; successful clients are not changed."
                : attempt.RemainingFailureCount == 0 ? "No failed clients remain."
                : "Selective retry unavailable; start a new detected-client configuration run.";
        }

        private void OnRetryFailedClientsClicked()
        {
            OnCancelConfigurationPreviewClicked();
            if (configurationInProgress)
                return;
            var service = MCPServiceLocator.Client;
            if (!(service is IClientConfigurationRetryService retryService))
            {
                SetRetryAttempt(null);
                if (retryStatusLabel != null)
                    retryStatusLabel.text = "Selective retry unavailable for this client service.";
                return;
            }
            var previous = retryAttempt;
            SetConfigurationBusy(true);
            try
            {
                var result = retryService.RetryFailedClients(previous);
                SetRetryAttempt(result.Attempt);
                if (retryStatusLabel != null)
                {
                    retryStatusLabel.text =
                        result.Disposition == "completed"
                            ? $"Retry: {result.Summary.SuccessCount} succeeded, {result.Summary.FailureCount} failed, {result.Summary.SkippedCount} unavailable; {result.Attempt.RemainingFailureCount} remain. Uses current settings."
                        : result.Disposition == "preparation_failed"
                            ? "Retry preparation failed; no clients were attempted. Retry again explicitly using current settings."
                        : "Selective retry unavailable; start a new detected-client configuration run.";
                }
                if (result.Disposition != "completed" || previous == null)
                    return;
                // Rendering a selective result must not enter the default auto-rewriting refresh path for successful peers.
                foreach (var target in previous.FailedTargets)
                    lastStatusChecks.Remove(target.Client);
                if (
                    selectedClientIndex >= 0
                    && selectedClientIndex < configurators.Count
                    && previous.FailedTargets.Any(target => ReferenceEquals(target.Client, configurators[selectedClientIndex]))
                )
                {
                    ApplyStatusToUi(configurators[selectedClientIndex]);
                    UpdateManualConfiguration();
                }
            }
            catch (Exception)
            {
                SetRetryAttempt(null);
                if (retryStatusLabel != null)
                    retryStatusLabel.text = "Selective retry unavailable; start a new detected-client configuration run.";
            }
            finally
            {
                SetConfigurationBusy(false);
            }
        }

        private void DiscardRetryAttempt()
        {
            if (MCPServiceLocator.Client is IClientConfigurationRetryService retryService)
                retryService.DiscardConfigurationAttempt();
            SetRetryAttempt(null);
        }

        private void OnConfigureClicked()
        {
            OnCancelConfigurationPreviewClicked();
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            DiscardRetryAttempt();
            var client = configurators[selectedClientIndex];

            // Handle CLI configurators asynchronously
            if (client is ClaudeCliMcpConfigurator)
            {
                ConfigureClaudeCliAsync(client);
                return;
            }

            try
            {
                // Only JSON configurators offer removal here; CLI configurators are handled above.
                if (client is JsonFileMcpConfigurator jsonClient && jsonClient.ShouldUnregister)
                {
                    client.Unregister();
                }
                else
                {
                    MCPServiceLocator.Client.ConfigureClient(client);
                }
                lastStatusChecks.Remove(client);
                RefreshClientStatus(client, forceImmediate: true);
                UpdateManualConfiguration();
            }
            catch (Exception ex)
            {
                clientStatusLabel.text = "Error";
                clientStatusLabel.style.color = Color.red;
                McpLog.Error($"Configuration failed: {ex.Message}");
                EditorUtility.DisplayDialog("Configuration Failed", ex.Message, "OK");
            }
        }

        private async void ConfigureClaudeCliAsync(IMcpClientConfigurator client)
        {
            if (cliActionInProgress || cliLifetime.IsCancellationRequested)
                return;

            cliActionInProgress = true;
            CliStatusState state = null;
            try
            {
                state = BeginCliStatus(client, CaptureCliContext());
                ApplyStatusToUi(client, showChecking: true);
                // The action always reads the exact target again, even when its UI status
                // is cached or another target's status check is still running.
                await runCliWork(() => ProbeCliStatus(state));
                if (!IsCurrentCliState(client, state))
                    return;

                var context = state.Context;
                bool unregister = state.Client.ShouldUnregisterForContext(context.HasProjectOverride, context.Transport);
                ApplyStatusToUi(client, showChecking: true, customMessage: unregister ? "Unregistering..." : "Configuring...");
                string uvxPath = null,
                    packageName = null,
                    fromArgs = null,
                    uvxDevFlags = null,
                    source = null;
                bool forceRefresh = false;
                if (!unregister && !context.UseHttp)
                {
                    (uvxPath, source, packageName) = AssetPathUtility.GetUvxCommandParts();
                    fromArgs = AssetPathUtility.GetBetaServerFromArgs(quoteFromPath: true);
                    forceRefresh = AssetPathUtility.ShouldForceUvxRefresh();
                }
                string apiKey = context.UseHttp && context.Remote ? EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty) : null;
                string pathPrepend = null;
                if (context.Platform == RuntimePlatform.OSXEditor)
                    pathPrepend = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
                else if (context.Platform == RuntimePlatform.LinuxEditor)
                    pathPrepend = "/usr/local/bin:/usr/bin:/bin";
                try
                {
                    string claudeDir = Path.GetDirectoryName(context.Executable);
                    if (!string.IsNullOrEmpty(claudeDir))
                        pathPrepend = string.IsNullOrEmpty(pathPrepend) ? claudeDir : $"{claudeDir}:{pathPrepend}";
                }
                catch { }

                if (!unregister && !context.UseHttp)
                    uvxDevFlags = await AssetPathUtility.GetUvxDevFlagsAsync(uvxPath, source, forceRefresh, state.Token);
                state.Token.ThrowIfCancellationRequested();
                if (!IsCurrentCliState(client, state) || (source != null && (source != context.PackageSource || uvxPath != context.UvxPath)))
                    return;

                await runCliWork(() =>
                {
                    state.Token.ThrowIfCancellationRequested();
                    state.Client.ConfigureWithCapturedValues(
                        context.ProjectDir,
                        context.Executable,
                        pathPrepend,
                        context.UseHttp,
                        context.HttpUrl,
                        uvxPath,
                        fromArgs,
                        packageName,
                        uvxDevFlags,
                        apiKey,
                        context.Transport,
                        unregister
                    );
                });
                if (!IsCurrentCliState(client, state))
                    return;
                state.Pending = false;
                lastStatusChecks[client] = DateTime.UtcNow;
                ApplyStatusToUi(client);
                UpdateManualConfiguration();
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                if (state != null && IsCurrentCliState(client, state))
                {
                    state.Client.Client.SetStatus(McpStatus.Error, ex.Message);
                    state.Pending = false;
                    lastStatusChecks[client] = DateTime.UtcNow;
                    ApplyStatusToUi(client);
                }
            }
            finally
            {
                if (state != null)
                    state.Pending = false;
                cliActionInProgress = false;
                if (!cliLifetime.IsCancellationRequested && Root.panel != null)
                    ApplyStatusToUi(client);
            }
        }

        private bool IsCurrentCliOperation(int revision, IMcpClientConfigurator client) =>
            revision == cliRevision
            && Root.panel != null
            && selectedClientIndex >= 0
            && selectedClientIndex < configurators.Count
            && ReferenceEquals(configurators[selectedClientIndex], client);

        private void UpdateInstallSkillsVisibility()
        {
            if (installSkillsButton == null)
                return;

            bool visible = selectedClientIndex >= 0 && selectedClientIndex < configurators.Count && configurators[selectedClientIndex].SupportsSkills;

            installSkillsButton.style.display = visible ? DisplayStyle.Flex : DisplayStyle.None;
        }

        private void OnInstallSkillsClicked()
        {
            if (isSkillSyncInProgress)
                return;

            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            var client = configurators[selectedClientIndex];
            if (!client.SupportsSkills)
                return;

            string installPath = client.GetSkillInstallPath();
            if (string.IsNullOrEmpty(installPath))
                return;

            SkillSyncSource source;
            try
            {
                source = SkillSyncSource.CaptureInstalled();
            }
            catch (Exception error)
            {
                EditorUtility.DisplayDialog("Install Skills Failed", error.Message, "OK");
                return;
            }

            isSkillSyncInProgress = true;
            installSkillsButton.SetEnabled(false);
            installSkillsButton.text = "Syncing...";

            SkillSyncService.SyncSourceAsync(
                source,
                installPath,
                null,
                result =>
                {
                    isSkillSyncInProgress = false;
                    installSkillsButton.SetEnabled(true);
                    installSkillsButton.text = "Install Skills";

                    string summary = SkillSyncService.DescribeResult(result);
                    if (result.Success)
                    {
                        bool needsAttention = result.Conflicts.Length > 0 || !string.IsNullOrEmpty(result.RecoveryPath);
                        if (needsAttention)
                            McpLog.Warn($"SkillSync: {summary} ({installPath})");
                        else
                            McpLog.Info($"SkillSync: {summary} ({installPath})");
                        EditorUtility.DisplayDialog(
                            needsAttention ? "Skills synced with retained changes" : "Install Skills",
                            $"{summary}\n\nInstalled at: {installPath}",
                            "OK"
                        );
                    }
                    else
                    {
                        McpLog.Error($"SkillSync failed: {summary}");
                        EditorUtility.DisplayDialog(result.PartialApplication ? "Skill sync requires recovery" : "Install Skills Failed", summary, "OK");
                    }
                }
            );
        }

        private void OnBrowseClaudeClicked()
        {
            OnCancelConfigurationPreviewClicked();
            string suggested = RuntimeInformation.IsOSPlatform(OSPlatform.OSX)
                ? "/opt/homebrew/bin"
                : Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            string picked = EditorUtility.OpenFilePanel("Select Claude CLI", suggested, "");
            if (!string.IsNullOrEmpty(picked))
            {
                try
                {
                    MCPServiceLocator.Paths.SetClaudeCliPathOverride(picked);
                    UpdateClaudeCliPathVisibility();
                    UpdateClientStatus();
                    McpLog.Info($"Claude CLI path override set to: {picked}");
                }
                catch (Exception ex)
                {
                    EditorUtility.DisplayDialog("Invalid Path", ex.Message, "OK");
                }
            }
        }

        private void OnBrowseProjectDirClicked()
        {
            OnCancelConfigurationPreviewClicked();
            string currentDir = ClaudeCliMcpConfigurator.GetClientProjectDir();
            string picked = EditorUtility.OpenFolderPanel("Select Client Project Directory", currentDir, "");
            if (!string.IsNullOrEmpty(picked))
            {
                if (!Directory.Exists(picked))
                {
                    EditorUtility.DisplayDialog("Invalid Path", "The selected directory does not exist.", "OK");
                    return;
                }
                EditorPrefs.SetString(EditorPrefKeys.ClientProjectDirOverride, picked);
                UpdateClientProjectDirVisibility();
                UpdateClientStatus();
                McpLog.Info($"Client project directory override set to: {picked}");
            }
        }

        private void OnClearProjectDirClicked()
        {
            OnCancelConfigurationPreviewClicked();
            EditorPrefs.DeleteKey(EditorPrefKeys.ClientProjectDirOverride);
            UpdateClientProjectDirVisibility();
            UpdateClientStatus();
            McpLog.Info("Client project directory override cleared, using Unity project directory.");
        }

        private void OnCopyPathClicked()
        {
            EditorGUIUtility.systemCopyBuffer = configPathField.value;
            McpLog.Info("Config path copied to clipboard");
        }

        private void OnOpenFileClicked()
        {
            OnCancelConfigurationPreviewClicked();
            string path = configPathField.value;
            try
            {
                if (!File.Exists(path))
                {
                    EditorUtility.DisplayDialog("Open File", "The configuration file path does not exist.", "OK");
                    return;
                }

                using var openedFile = Process.Start(new ProcessStartInfo { FileName = path, UseShellExecute = true });
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to open file: {ex.Message}");
            }
        }

        private void OnCopyJsonClicked()
        {
            EditorGUIUtility.systemCopyBuffer = configJsonField.value;
            McpLog.Info("Configuration copied to clipboard");
        }

        public void RefreshSelectedClient(bool forceImmediate = false)
        {
            if (selectedClientIndex >= 0 && selectedClientIndex < configurators.Count)
            {
                var client = configurators[selectedClientIndex];
                bool refresh = forceImmediate || ShouldRefreshClient(client);
                RefreshClientStatus(client, refresh);
                if (refresh && configurationPreview == null)
                {
                    UpdateManualConfiguration();
                    UpdateClaudeCliPathVisibility();
                }
            }
        }

        private void RefreshClientStatus(IMcpClientConfigurator client, bool forceImmediate = false)
        {
            if (client is ClaudeCliMcpConfigurator)
            {
                RefreshClaudeCliStatus(client, forceImmediate);
                return;
            }

            if (forceImmediate || ShouldRefreshClient(client))
            {
                MCPServiceLocator.Client.CheckClientStatus(client, attemptAutoRewrite: false);
                lastStatusChecks[client] = DateTime.UtcNow;
                (lastStatusFiles ??= new Dictionary<IMcpClientConfigurator, string>())[client] = ClientFileStamp(client);
            }

            ApplyStatusToUi(client);
        }

        private async void RefreshClaudeCliStatus(IMcpClientConfigurator client, bool forceImmediate)
        {
            if (cliLifetime.IsCancellationRequested)
                return;
            if (!TryCaptureCliContext(out var context))
            {
                ApplyStatusToUi(client);
                return;
            }
            if (
                cliStatuses.TryGetValue(client, out var previous)
                && previous.Revision == cliRevision
                && !previous.Token.IsCancellationRequested
                && previous.Context.Equals(context)
            )
            {
                if (previous.Pending || (!forceImmediate && !ShouldRefreshClient(client)))
                {
                    ApplyStatusToUi(client);
                    return;
                }
            }

            var state = BeginCliStatus(client, context);
            ApplyStatusToUi(client, showChecking: true);
            try
            {
                await runCliWork(() => ProbeCliStatus(state));
                if (!IsCurrentCliState(client, state))
                    return;
                state.Pending = false;
                lastStatusChecks[client] = DateTime.UtcNow;
                ApplyStatusToUi(client);
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                if (IsCurrentCliState(client, state))
                {
                    state.Client.Client.SetStatus(McpStatus.Error, ex.Message);
                    state.Pending = false;
                    lastStatusChecks[client] = DateTime.UtcNow;
                    ApplyStatusToUi(client);
                }
            }
            finally
            {
                // Retire real work even when its result cannot be published. Leaving an
                // obsolete record pending would suppress a later probe of the same target.
                state.Pending = false;
            }
        }

        private Dictionary<IMcpClientConfigurator, string> lastStatusFiles = new();

        private static string ClientFileStamp(IMcpClientConfigurator client)
        {
            if (client is ClaudeCliMcpConfigurator)
                return null;
            try
            {
                string path = client.GetConfigPath();
                var file = new FileInfo(path);
                return path + "|" + (file.Exists ? file.Length + ":" + file.LastWriteTimeUtc.Ticks : "missing");
            }
            catch
            {
                return null;
            }
        }

        private bool ShouldRefreshClient(IMcpClientConfigurator client)
        {
            if (
                client is ClaudeCliMcpConfigurator
                && (
                    !cliStatuses.TryGetValue(client, out var state)
                    || state.Revision != cliRevision
                    || state.Token.IsCancellationRequested
                    || !TryCaptureCliContext(out var context)
                    || !state.Context.Equals(context)
                )
            )
                return true;
            if (!lastStatusChecks.TryGetValue(client, out var last))
            {
                return true;
            }

            return (DateTime.UtcNow - last) > StatusRefreshInterval
                || (
                    client is not ClaudeCliMcpConfigurator
                    && (lastStatusFiles == null || !lastStatusFiles.TryGetValue(client, out string stamp) || stamp != ClientFileStamp(client))
                );
        }

        private void ApplyStatusToUi(IMcpClientConfigurator client, bool showChecking = false, string customMessage = null)
        {
            if (selectedClientIndex < 0 || selectedClientIndex >= configurators.Count)
                return;

            if (!ReferenceEquals(configurators[selectedClientIndex], client))
                return;

            if (client is ClaudeCliMcpConfigurator)
            {
                if (
                    cliStatuses.TryGetValue(client, out var state)
                    && state.Revision == cliRevision
                    && !state.Token.IsCancellationRequested
                    && TryCaptureCliContext(out var context)
                    && state.Context.Equals(context)
                )
                {
                    client = state.Client;
                    showChecking |= state.Pending;
                }
                else
                {
                    showChecking = true;
                    if (!TryCaptureCliContext(out _))
                        customMessage = "Configuration unavailable";
                }
                configureButton.SetEnabled(!showChecking && !cliActionInProgress);
            }
            else
                configureButton.SetEnabled(true);

            clientStatusIndicator.RemoveFromClassList("configured");
            clientStatusIndicator.RemoveFromClassList("not-configured");
            clientStatusIndicator.RemoveFromClassList("warning");

            if (showChecking)
            {
                clientStatusLabel.text = customMessage ?? "Checking...";
                clientStatusLabel.style.color = StyleKeyword.Null;
                clientStatusIndicator.AddToClassList("warning");
                configureButton.text = "Configure";
                return;
            }

            // Check for transport mismatch (3-way: Stdio, Http, HttpRemote).
            // Skip when a project dir override is active — the registered transport
            // in the overridden project may legitimately differ from the local server.
            bool hasTransportMismatch = false;
            if (client.ConfiguredTransport != ConfiguredTransport.Unknown && !ClaudeCliMcpConfigurator.HasClientProjectDirOverride)
            {
                ConfiguredTransport serverTransport = HttpEndpointUtility.GetCurrentServerTransport();
                hasTransportMismatch = client.ConfiguredTransport != serverTransport;
            }

            // If configured but with transport mismatch, show warning state
            if (hasTransportMismatch && (client.Status == McpStatus.Configured || client.Status == McpStatus.Running || client.Status == McpStatus.Connected))
            {
                clientStatusLabel.text = "Transport Mismatch";
                clientStatusIndicator.AddToClassList("warning");
            }
            else
            {
                clientStatusLabel.text = GetStatusDisplayString(client.Status);

                switch (client.Status)
                {
                    case McpStatus.Configured:
                    case McpStatus.Running:
                    case McpStatus.Connected:
                        clientStatusIndicator.AddToClassList("configured");
                        break;
                    case McpStatus.IncorrectPath:
                    case McpStatus.CommunicationError:
                    case McpStatus.NoResponse:
                    case McpStatus.Error:
                    case McpStatus.VersionMismatch:
                        clientStatusIndicator.AddToClassList("warning");
                        break;
                    default:
                        clientStatusIndicator.AddToClassList("not-configured");
                        break;
                }
            }

            clientStatusLabel.style.color = StyleKeyword.Null;
            clientStatusLabel.tooltip = client is McpClientConfiguratorBase configuredClient ? configuredClient.Client.configStatus : string.Empty;
            configureButton.text = client.GetConfigureActionLabel();

            // Notify listeners about the client's configured transport
            OnClientTransportDetected?.Invoke(client.DisplayName, client.ConfiguredTransport);

            // Notify listeners about version mismatch if applicable
            if (client.Status == McpStatus.VersionMismatch && client is McpClientConfiguratorBase baseConfigurator)
            {
                // Get the mismatch reason from the configStatus field
                string mismatchReason = baseConfigurator.Client.configStatus;
                OnClientConfigMismatch?.Invoke(client.DisplayName, mismatchReason);
            }
            else
            {
                // Clear any previous mismatch warning
                OnClientConfigMismatch?.Invoke(client.DisplayName, null);
            }
        }

        /// <summary>
        /// Gets the expected package source for validation based on installed package version.
        /// Uses the same logic as registration to ensure validation matches what was registered.
        /// MUST be called from the main thread due to EditorPrefs access.
        /// </summary>
        private static string GetExpectedPackageSourceForCurrentPackage()
        {
            return AssetPathUtility.GetMcpServerPackageSource();
        }

        private int FindConfiguratorIndex(string persistedClientValue)
        {
            if (string.IsNullOrWhiteSpace(persistedClientValue))
                return -1;

            // Primary match: stored stable ID.
            for (int i = 0; i < configurators.Count; i++)
            {
                if (string.Equals(configurators[i].Id, persistedClientValue, StringComparison.OrdinalIgnoreCase))
                    return i;
            }

            // Compatibility match for older persisted values (e.g., display names with spaces).
            string normalized = NormalizeClientToken(persistedClientValue);
            if (string.IsNullOrEmpty(normalized))
                return -1;

            for (int i = 0; i < configurators.Count; i++)
            {
                if (NormalizeClientToken(configurators[i].Id) == normalized || NormalizeClientToken(configurators[i].DisplayName) == normalized)
                {
                    return i;
                }
            }

            return -1;
        }

        private int GetIndexForDropdownValue(string dropdownValue)
        {
            if (string.IsNullOrWhiteSpace(dropdownValue))
                return -1;

            int directIndex = clientDropdown.choices?.IndexOf(dropdownValue) ?? -1;
            if (directIndex >= 0 && directIndex < configurators.Count)
                return directIndex;

            string normalized = NormalizeClientToken(dropdownValue);
            if (string.IsNullOrEmpty(normalized))
                return -1;

            for (int i = 0; i < configurators.Count; i++)
            {
                if (NormalizeClientToken(configurators[i].DisplayName) == normalized)
                    return i;
            }

            return -1;
        }

        private static string NormalizeClientToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return string.Empty;

            return new string(value.Where(char.IsLetterOrDigit).ToArray()).ToLowerInvariant();
        }
    }
}
