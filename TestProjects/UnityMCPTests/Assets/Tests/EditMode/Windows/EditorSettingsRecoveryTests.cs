using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.Advanced;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class EditorSettingsRecoveryTests
    {
        private const string Base = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";
        private const BindingFlags Instance = BindingFlags.NonPublic | BindingFlags.Instance;
        private const BindingFlags Static = BindingFlags.NonPublic | BindingFlags.Static;

        private sealed class EvidenceWindow : EditorWindow { }

        private static VisualElement PrefRow(EditorPrefsWindow window, EditorPrefItem item)
        {
            typeof(EditorPrefsWindow)
                .GetField("itemTemplate", Instance)
                .SetValue(window, AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "EditorPrefs/EditorPrefItem.uxml"));
            return (VisualElement)typeof(EditorPrefsWindow).GetMethod("CreateItemUI", Instance).Invoke(window, new object[] { item });
        }

        [TestCase(EditorPrefKeys.ApiKey)]
        [TestCase(EditorPrefKeys.LastLocalHttpServerInstanceToken)]
        public void SecretPreferenceNeverCreatesAValueField(string key)
        {
            var window = ScriptableObject.CreateInstance<EditorPrefsWindow>();
            try
            {
                var row = PrefRow(
                    window,
                    new EditorPrefItem
                    {
                        Key = key,
                        Value = "synthetic-secret",
                        Type = EditorPrefType.String,
                        IsKnown = true,
                    }
                );
                Assert.IsNull(row.Q<TextField>("value-field"));
                Assert.IsFalse(row.Query<Label>().ToList().Any(x => x.text.Contains("synthetic-secret")));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [Test]
        public void UnsetKnownPreferenceCanBeInitializedWithLockedType()
        {
            var window = ScriptableObject.CreateInstance<EditorPrefsWindow>();
            try
            {
                var row = PrefRow(
                    window,
                    new EditorPrefItem
                    {
                        Key = EditorPrefKeys.DebugLogs,
                        Value = "Unset",
                        Type = EditorPrefType.Bool,
                        IsKnown = true,
                        IsUnset = true,
                    }
                );
                Assert.IsTrue(row.Q<TextField>("value-field").enabledSelf);
                Assert.AreEqual("false", row.Q<TextField>("value-field").value.ToLowerInvariant());
                Assert.IsFalse(row.Q<DropdownField>("type-dropdown").enabledSelf);
                Assert.AreEqual("Initialize", row.Q<Button>("save-button").text);
                Assert.IsNotNull(row.Q<Button>("reset-button"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [Test]
        public void RefreshKeepsUnsavedPreferenceDraftAndResetRestoresUnset()
        {
            var window = ScriptableObject.CreateInstance<EditorPrefsWindow>();
            string key = "MCPForUnity.RecoveryTest." + Guid.NewGuid().ToString("N");
            try
            {
                var type = typeof(EditorPrefsWindow);
                var known = (HashSet<string>)type.GetField("knownMcpKeys", Instance).GetValue(window);
                known.Clear();
                known.Add(key);
                ((Dictionary<string, EditorPrefType>)type.GetField("knownPrefTypes", Instance).GetValue(window)).Add(key, EditorPrefType.Bool);
                type.GetField("prefsContainer", Instance).SetValue(window, new VisualElement());
                type.GetField("resultCount", Instance).SetValue(window, new Label());
                type.GetField("emptyState", Instance).SetValue(window, new Label());
                type.GetField("itemTemplate", Instance)
                    .SetValue(window, AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "EditorPrefs/EditorPrefItem.uxml"));
                EditorPrefs.SetBool(key, false);
                var refresh = type.GetMethod("RefreshPrefs", Instance);
                refresh.Invoke(window, null);
                var rows = (Dictionary<string, VisualElement>)type.GetField("prefRows", Instance).GetValue(window);
                rows[key].Q<TextField>("value-field").value = "true";
                refresh.Invoke(window, null);
                Assert.AreEqual("true", rows[key].Q<TextField>("value-field").value);
                Assert.IsFalse(EditorPrefs.GetBool(key));
                var item = ((List<EditorPrefItem>)type.GetField("currentPrefs", Instance).GetValue(window)).Single(x => x.Key == key);
                type.GetMethod("SavePref", Instance).Invoke(window, new object[] { item, "true", EditorPrefType.Bool });
                Assert.IsTrue(EditorPrefs.GetBool(key));
                type.GetMethod("ResetPref", Instance).Invoke(window, new object[] { item });
                Assert.IsFalse(EditorPrefs.HasKey(key));
                Assert.AreEqual("Initialize", rows[key].Q<Button>("save-button").text);
            }
            finally
            {
                EditorPrefs.DeleteKey(key);
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [UnityTest, Explicit("Controller-owned rendered settings check.")]
        public IEnumerator NarrowPreferenceActionsRemainReadable()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            var preferences = ScriptableObject.CreateInstance<EditorPrefsWindow>();
            try
            {
                window.ShowUtility();
                window.position = new Rect(30, 30, 600, 400);
                window.rootVisualElement.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "EditorPrefs/EditorPrefsWindow.uxml").CloneTree(window.rootVisualElement);
                var row = PrefRow(
                    preferences,
                    new EditorPrefItem
                    {
                        Key = EditorPrefKeys.DebugLogs,
                        Value = "Unset",
                        Type = EditorPrefType.Bool,
                        IsKnown = true,
                        IsUnset = true,
                    }
                );
                window.rootVisualElement.Q("prefs-container").Add(row);
                for (int i = 0; i < 12; i++)
                {
                    window.Repaint();
                    EditorApplication.QueuePlayerLoopUpdate();
                    yield return null;
                }
                var initialize = row.Q<Button>("save-button");
                Assert.GreaterOrEqual(initialize.worldBound.width, 70);
                Assert.IsTrue(window.rootVisualElement.worldBound.Contains(initialize.worldBound.center));
                string directory = System.IO.Path.GetFullPath(
                    System.IO.Path.Combine(Application.dataPath, "../Library/McpTestEvidence/settings-recovery-" + Guid.NewGuid().ToString("N"))
                );
                System.IO.Directory.CreateDirectory(directory);
                string path = System.IO.Path.Combine(directory, "preferences-600.png");
                typeof(OperationActivityTests).GetMethod("Capture", Static).Invoke(null, new object[] { window, path });
                TestContext.Out.WriteLine("SETTINGS_CAPTURE " + path);
            }
            finally
            {
                window.Close();
                UnityEngine.Object.DestroyImmediate(preferences);
            }
        }

        private sealed class Deployment : IPackageDeploymentService
        {
            public int Writes;

            public string GetStoredSourcePath() => "";

            public void SetStoredSourcePath(string path)
            {
                Writes++;
                throw new ArgumentException("Select an existing package folder.");
            }

            public void ClearStoredSourcePath() { }

            public string GetTargetPath() => "";

            public string GetTargetDisplayPath() => "test target";

            public string GetLastBackupPath() => "";

            public bool HasBackup() => false;

            public PackageDeploymentResult DeployFromStoredSource() => throw new InvalidOperationException();

            public PackageDeploymentResult RestoreLastBackup() => throw new InvalidOperationException();
        }

        [Test]
        public void SourceTypingKeepsDraftUntilExplicitApplyAndFailureRetainsIt()
        {
            var slot = typeof(MCPServiceLocator).GetField("_packageDeploymentService", Static);
            object previous = slot.GetValue(null);
            var fake = new Deployment();
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                slot.SetValue(null, fake);
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/Advanced/McpAdvancedSection.uxml").CloneTree();
                window.rootVisualElement.Add(root);
                var section = new McpAdvancedSection(root);
                // The missing explicit action is the baseline failure, before any modal callback can run.
                Assert.IsNotNull(root.Q<Button>("apply-deploy-source-button"));
                root.Q<TextField>("deploy-source-path").value = "incomplete/package";
                Assert.AreEqual(0, fake.Writes);
                typeof(McpAdvancedSection).GetMethod("ApplyDeploymentSource", Instance).Invoke(section, null);
                Assert.AreEqual(1, fake.Writes);
                Assert.AreEqual("incomplete/package", root.Q<TextField>("deploy-source-path").value);
                Assert.That(root.Q<Label>("deploy-status-label").text, Does.Contain("existing package"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
                slot.SetValue(null, previous);
            }
        }

        private sealed class KeyStore : ISecureKeyStore
        {
            public bool Present = true;
            public bool Refuse = true;
            public int Deletes;

            public bool Has(string id) => Present;

            public bool TryGet(string id, out string key)
            {
                key = null;
                throw new InvalidOperationException("No key read allowed");
            }

            public void Set(string id, string key) => Assert.Fail("No key writes allowed");

            public void Delete(string id)
            {
                Deletes++;
                if (Refuse)
                    throw new InvalidOperationException("synthetic-secret must not reach logs");
                Present = false;
            }
        }

        [UnityTest]
        public IEnumerator FailedKeyDeletionPreservesPresenceAndSuccessfulRetryClearsIt()
        {
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            var store = new KeyStore();
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network allowed") };
            try
            {
                SecureKeyStore.OverrideForTests(store);
                AssetGenModelCatalog.ResetForTests(true);
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                new McpAssetGenSection(root);
                var row = root.Query<VisualElement>(className: "provider-row").First();
                var remove = row.Query<Button>().ToList().First(b => b.text == "Clear" || b.text == "Remove saved key");
                remove.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    remove.SendEvent(submit);
                yield return null;
                Assert.AreEqual(1, store.Deletes);
                Assert.That(row.Q<Label>(className: "provider-status").text, Does.Contain("failed"));
                Assert.That(row.Q<Label>(className: "provider-status").text, Does.Not.Contain("synthetic-secret"));
                Assert.IsTrue(store.Present);
                store.Refuse = false;
                remove.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    remove.SendEvent(submit);
                yield return null;
                Assert.AreEqual("not set", row.Q<Label>(className: "provider-status").text);
                Assert.IsFalse(store.Present);
                Assert.IsEmpty(transport.RecordedRequests);
            }
            finally
            {
                window.Close();
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }
    }
}
