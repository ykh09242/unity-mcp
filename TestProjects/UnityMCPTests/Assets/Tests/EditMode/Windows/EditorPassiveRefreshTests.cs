using System;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.Advanced;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class EditorPassiveRefreshTests
    {
        [Test]
        public void HiddenAdvancedPanelDoesNotProbeAndVisibleFocusReusesInputsWithoutChangeEvents()
        {
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            var pathSlot = typeof(MCPServiceLocator).GetField("_pathService", flags);
            var bridgeSlot = typeof(MCPServiceLocator).GetField("_bridgeService", flags);
            var deploymentSlot = typeof(MCPServiceLocator).GetField("_packageDeploymentService", flags);
            object savedPaths = pathSlot.GetValue(null),
                savedBridge = bridgeSlot.GetValue(null),
                savedDeployment = deploymentSlot.GetValue(null);
            MCPForUnityEditorWindow window = null;
            try
            {
                var paths = new CountingPaths();
                pathSlot.SetValue(null, paths);
                bridgeSlot.SetValue(null, new IdleBridge());
                deploymentSlot.SetValue(null, new EmptyDeployment());
                var advanced = (McpAdvancedSection)FormatterServices.GetUninitializedObject(typeof(McpAdvancedSection));
                int changes = 0;
                foreach (var field in typeof(McpAdvancedSection).GetFields(BindingFlags.Instance | BindingFlags.NonPublic))
                {
                    VisualElement value =
                        field.FieldType == typeof(TextField) ? new TextField()
                        : field.FieldType == typeof(Toggle) ? new Toggle()
                        : field.FieldType == typeof(Label) ? new Label()
                        : field.FieldType == typeof(Button) ? new Button()
                        : field.FieldType == typeof(VisualElement) ? new VisualElement()
                        : null;
                    if (value == null)
                        continue;
                    value.RegisterCallback<ChangeEvent<string>>(_ => changes++);
                    value.RegisterCallback<ChangeEvent<bool>>(_ => changes++);
                    field.SetValue(advanced, value);
                }
                window = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
                window.rootVisualElement.Add(new VisualElement());
                Set(window, "advancedSection", advanced);
                var panel = typeof(MCPForUnityEditorWindow).GetField("activePanel", BindingFlags.Instance | BindingFlags.NonPublic);
                panel.SetValue(window, Enum.Parse(panel.FieldType, "Clients"));
                Focus(window);
                Focus(window);
                Assert.AreEqual(0, paths.Resolutions);
                Assert.AreEqual(0, paths.Probes);
                panel.SetValue(window, Enum.Parse(panel.FieldType, "Advanced"));
                for (int i = 0; i < 5; i++)
                    Focus(window);
                Assert.AreEqual(1, paths.Probes);
                Assert.AreEqual(0, changes, "Passive field display must not fire preference-writing callbacks");
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                pathSlot.SetValue(null, savedPaths);
                bridgeSlot.SetValue(null, savedBridge);
                deploymentSlot.SetValue(null, savedDeployment);
            }
        }

        private static void Focus(MCPForUnityEditorWindow window)
        {
            Set(window, "lastRefreshTime", -1000d);
            typeof(MCPForUnityEditorWindow).GetMethod("OnFocus", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window, null);
        }

        private static void Set(object target, string name, object value) =>
            target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value);

        private sealed class CountingPaths : IPathResolverService
        {
            public int Resolutions,
                Probes;

            public string GetUvxPath()
            {
                Resolutions++;
                return "memory-uvx";
            }

            public Task<string> ValidateUvxExecutableAsync(string path)
            {
                Probes++;
                return Task.FromResult("0.9.18");
            }

            public bool HasUvxPathOverride => false;
            public bool HasUvxPathFallback => false;
            public bool HasClaudeCliPathOverride => false;

            public bool TryValidateUvxExecutable(string path, out string version) => throw new InvalidOperationException();

            public string GetClaudeCliPath() => throw new InvalidOperationException();

            public bool IsPythonDetected() => false;

            public bool IsClaudeCliDetected() => false;

            public void SetUvxPathOverride(string path) => throw new InvalidOperationException();

            public void SetClaudeCliPathOverride(string path) => throw new InvalidOperationException();

            public void ClearUvxPathOverride() => throw new InvalidOperationException();

            public void ClearClaudeCliPathOverride() => throw new InvalidOperationException();
        }

        private sealed class IdleBridge : IBridgeControlService
        {
            public bool IsRunning => false;
            public int CurrentPort => 0;
            public bool IsAutoConnectMode => false;
            public TransportMode? ActiveMode => null;

            public Task<bool> StartAsync() => throw new InvalidOperationException();

            public Task StopAsync() => throw new InvalidOperationException();

            public BridgeVerificationResult Verify(int port) => throw new InvalidOperationException();

            public Task<BridgeVerificationResult> VerifyAsync() => throw new InvalidOperationException();
        }

        private sealed class EmptyDeployment : IPackageDeploymentService
        {
            public string GetStoredSourcePath() => "";

            public void SetStoredSourcePath(string path) => throw new InvalidOperationException();

            public void ClearStoredSourcePath() => throw new InvalidOperationException();

            public string GetTargetPath() => "";

            public string GetTargetDisplayPath() => "";

            public string GetLastBackupPath() => "";

            public bool HasBackup() => false;

            public PackageDeploymentResult DeployFromStoredSource() => throw new InvalidOperationException();

            public PackageDeploymentResult RestoreLastBackup() => throw new InvalidOperationException();
        }
    }
}
