using System;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.ScriptQuality;
using MCPForUnity.Editor.Setup;
using MCPForUnity.Editor.Tools;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.Validation
{
    /// <summary>
    /// Controller for the Script Validation section.
    /// Handles script validation level settings.
    /// </summary>
    public class McpValidationSection
    {
        // UI Elements
        private EnumField validationLevelField;
        private Label validationDescription;
        private Label analysisStatus;
        private System.Threading.CancellationTokenSource setupCancellation;

        // Data
        private ValidationLevel currentValidationLevel = ValidationLevel.Standard;

        // Validation levels
        public enum ValidationLevel
        {
            Basic,
            Standard,
            Comprehensive,
            Strict,
        }

        public VisualElement Root { get; private set; }

        public McpValidationSection(VisualElement root)
        {
            Root = root;
            CacheUIElements();
            InitializeUI();
            RegisterCallbacks();
            Root.RegisterCallback<AttachToPanelEvent>(_ => UpdateValidationDescription());
        }

        private void CacheUIElements()
        {
            validationLevelField = Root.Q<EnumField>("validation-level");
            validationDescription = Root.Q<Label>("validation-description");
        }

        private void InitializeUI()
        {
            validationLevelField.Init(ValidationLevel.Standard);
            int savedLevel = EditorPrefs.GetInt(EditorPrefKeys.ValidationLevel, 1);
            currentValidationLevel = (ValidationLevel)Mathf.Clamp(savedLevel, 0, 3);
            validationLevelField.value = currentValidationLevel;
            UpdateValidationDescription();
            analysisStatus = new Label();
            analysisStatus.style.whiteSpace = WhiteSpace.Normal;
            Root.Add(analysisStatus);
            Root.Add(
                new Button(async () =>
                {
                    if (ScriptAnalysisHostInstaller.Installing)
                        return;
                    setupCancellation = new System.Threading.CancellationTokenSource();
                    analysisStatus.text = "Setting up the optional analysis host...";
                    try
                    {
                        await ScriptAnalysisHostInstaller.InstallAsync(setupCancellation.Token);
                        analysisStatus.text = "Analysis host installed. Enable analysis for this Editor session to use it.";
                    }
                    catch (Exception error)
                    {
                        analysisStatus.text = "Analysis setup: " + error.Message;
                    }
                    finally
                    {
                        setupCancellation.Dispose();
                        setupCancellation = null;
                    }
                })
                {
                    text = "Set up / update analysis host (.NET SDK 10+ required)",
                }
            );
            Root.Add(new Button(() => setupCancellation?.Cancel()) { text = "Cancel analysis host setup" });
            Root.Add(
                new Button(() =>
                {
                    var result = MCPServiceLocator.ToolConsent.Grant(ScriptAnalysisHost.ConsentName, ToolConsentScope.EditorSession);
                    analysisStatus.text = result.Succeeded ? "Analysis enabled for this Editor session." : "Analysis consent: " + result.ErrorCode;
                })
                {
                    text = "Enable analysis for this Editor session",
                }
            );
            Root.Add(
                new Button(() =>
                {
                    var result = MCPServiceLocator.ToolConsent.Revoke(ScriptAnalysisHost.ConsentName);
                    analysisStatus.text = result.Succeeded ? "Analysis disabled." : "Analysis consent: " + result.ErrorCode;
                })
                {
                    text = "Disable analysis",
                }
            );
            Root.Add(new Button(() => analysisStatus.text = ScriptAnalysisHost.Capabilities().ToString()) { text = "Check analysis host status" });
            analysisStatus.text =
                "Optional changed-script diagnostics use a separate .NET process. Setup restores pinned dependencies; analysis never installs or changes project code.";
        }

        private void RegisterCallbacks()
        {
            var consoleButton = Root.Q<Button>("validation-console");
            if (consoleButton != null)
                consoleButton.clicked += () => EditorApplication.ExecuteMenuItem("Window/General/Console");
            validationLevelField.RegisterValueChangedCallback(evt =>
            {
                currentValidationLevel = (ValidationLevel)evt.newValue;
                EditorPrefs.SetInt(EditorPrefKeys.ValidationLevel, (int)currentValidationLevel);
                UpdateValidationDescription();
            });
        }

        private void UpdateValidationDescription()
        {
            validationDescription.text = Describe(currentValidationLevel, ManageScript.RoslynValidationAvailable, EditorApplication.isCompiling);
        }

        internal static string Describe(ValidationLevel level, bool roslynAvailable, bool compiling)
        {
            string engine = roslynAvailable ? "Roslyn enabled" : "lexical heuristics (Roslyn not enabled)";
            string checks = level switch
            {
                ValidationLevel.Basic => "Structural checks: balanced delimiters and duplicate method signatures. No compiler syntax or semantic checks.",
                ValidationLevel.Standard => roslynAvailable
                    ? "Roslyn syntax and Unity lexical rules. Semantic compilation, references and cross-file partial types are not checked at this level."
                    : "Structural and Unity lexical rules. Compiler syntax, semantics, references and cross-file partial types are not checked.",
                ValidationLevel.Comprehensive => roslynAvailable
                    ? "Roslyn syntax, Unity rules and additional quality heuristics. Semantic compilation, references and cross-file partial types are not checked at this level."
                    : "Structural, Unity and quality heuristics. Compiler syntax, semantics, references and cross-file partial types are not checked.",
                ValidationLevel.Strict => roslynAvailable
                    ? "Roslyn syntax and semantic compilation plus heuristic rules; warnings fail validation. Reference and partial-type coverage depends on the script's owning assembly. Check the per-file validation report for actual coverage."
                    : "Structural, Unity and quality heuristics; warnings fail validation. Compiler syntax, semantics, references and cross-file partial types are not checked.",
                _ => "Unknown validation level; inspect the per-file validation report.",
            };
            string compilation = compiling ? "active" : "idle; last result not checked here";
            return $"Requested: {level}\nAvailable engine: {engine}\n{checks}\n\nUnity project compilation is separate and is not run by validation. Compilation snapshot when settings last updated: {compilation}. Open Console for existing diagnostics.";
        }
    }
}
