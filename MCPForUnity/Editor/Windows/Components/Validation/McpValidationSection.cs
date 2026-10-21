using System;
using MCPForUnity.Editor.Constants;
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
