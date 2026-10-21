using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Windows.Components.Validation;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    public class ValidationCapabilityTests
    {
        [TestCase(false, 0), TestCase(false, 1), TestCase(false, 2), TestCase(false, 3)]
        [TestCase(true, 0), TestCase(true, 1), TestCase(true, 2), TestCase(true, 3)]
        public void EveryLevelExplainsAvailableEngineAndCoverage(bool roslyn, int requested)
        {
            var level = (McpValidationSection.ValidationLevel)requested;
            string text = McpValidationSection.Describe(level, roslyn, false);
            StringAssert.Contains("Requested: " + level, text);
            StringAssert.Contains(roslyn ? "Roslyn enabled" : "Roslyn not enabled", text);
            StringAssert.Contains("Unity project compilation is separate", text);
            StringAssert.Contains("idle; last result not checked here", text);
            if (requested == 0)
                StringAssert.Contains("No compiler syntax or semantic checks", text);
            else if (!roslyn)
                StringAssert.Contains("cross-file partial types are not checked", text);
            else if (requested < 3)
                StringAssert.Contains("partial types are not checked at this level", text);
            else
                StringAssert.Contains("depends on the script's owning assembly", text);
            StringAssert.Contains("snapshot when settings last updated: active", McpValidationSection.Describe(level, roslyn, true));
        }

        private sealed class ValidationHost : EditorWindow { }

        [TestCase(0), TestCase(1), TestCase(2), TestCase(3)]
        public void ActualSettingsTemplateShowsAvailableEngineWithoutCompilationClaims(int level)
        {
            if (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
                Assert.Ignore("This attached Editor panel test requires a graphics device.");
            bool had = EditorPrefs.HasKey(EditorPrefKeys.ValidationLevel);
            int previous = EditorPrefs.GetInt(EditorPrefKeys.ValidationLevel);
            ValidationHost window = null;
            try
            {
                EditorPrefs.SetInt(EditorPrefKeys.ValidationLevel, level);
                var asset = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                    AssetPathUtility.GetMcpPackageRootPath() + "/Editor/Windows/Components/Validation/McpValidationSection.uxml"
                );
                Assert.IsNotNull(asset);
                var root = asset.Instantiate();
                _ = new McpValidationSection(root);
                window = ScriptableObject.CreateInstance<ValidationHost>();
                window.Show();
                window.rootVisualElement.Add(root);
                string text = root.Q<Label>("validation-description").text;
                StringAssert.Contains("Requested:", text);
                StringAssert.Contains("Available engine:", text);
                StringAssert.Contains(ManageScript.RoslynValidationAvailable ? "Roslyn enabled" : "Roslyn not enabled", text);
                StringAssert.Contains("Unity project compilation is separate", text);
                StringAssert.DoesNotContain("catches all", text);
                Assert.IsNotNull(root.Q<Button>("validation-console"));
                Assert.IsTrue(root.Q<Button>("validation-console").focusable);
                root.Q<EnumField>("validation-level").value = McpValidationSection.ValidationLevel.Basic;
                StringAssert.Contains("Structural", root.Q<Label>("validation-description").text);
                Assert.AreEqual(0, EditorPrefs.GetInt(EditorPrefKeys.ValidationLevel));
            }
            finally
            {
                if (window != null)
                    window.Close();
                if (had)
                    EditorPrefs.SetInt(EditorPrefKeys.ValidationLevel, previous);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.ValidationLevel);
            }
        }
    }
}
