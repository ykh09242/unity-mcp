using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.Serialization;
using MCPForUnity.Editor.Windows.Components.Advanced;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class AdvancedServerSourceDraftTests
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

        private sealed class Host : EditorWindow { }

        private static object Invoke(object section, string method) => section.GetType().GetMethod(method, Private).Invoke(section, null);

        private static void Set(object section, string field, object value) => section.GetType().GetField(field, Private).SetValue(section, value);

        [TestCase(false)]
        [TestCase(true)]
        public void ServerSourceCommitAcceptsEncodedFileUrisAndLiteralPercentPaths(bool fileUri)
        {
            string directory = Path.GetFullPath(
                Path.Combine(Application.dataPath, "../Library/McpTestEvidence/source space%20literal-" + Guid.NewGuid().ToString("N"))
            );
            string server = Path.Combine(directory, "Server");
            Directory.CreateDirectory(server);
            File.WriteAllText(Path.Combine(server, "pyproject.toml"), "[project]\nname='synthetic-uri'\n");
            var host = ScriptableObject.CreateInstance<Host>();
            try
            {
                host.ShowUtility();
                var root = AssetDatabase
                    .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/Advanced/McpAdvancedSection.uxml")
                    .CloneTree();
                host.rootVisualElement.Add(root);
                var section = (McpAdvancedSection)FormatterServices.GetUninitializedObject(typeof(McpAdvancedSection));
                Set(section, "<Root>k__BackingField", root);
                Invoke(section, "CacheUIElements");
                string saved = "previous";
                int writes = 0;
                Set(section, "readServerSource", (Func<string>)(() => saved));
                Set(
                    section,
                    "writeServerSource",
                    (Action<string>)(
                        value =>
                        {
                            saved = value;
                            writes++;
                        }
                    )
                );
                Invoke(section, "RegisterServerSourceCallbacks");
                var field = root.Q<TextField>("git-url-override");
                Assert.IsNotNull(field.panel, "Value-change callbacks require an attached UI panel.");
                field.value = fileUri ? new Uri(directory).AbsoluteUri : directory;
                Assert.IsTrue((bool)section.GetType().GetField("serverSourceDraftDirty", Private).GetValue(section));
                if (fileUri)
                {
                    StringAssert.Contains("%20", field.value);
                    StringAssert.Contains("%2520", field.value, "The literal percent sequence must remain distinct from URI-encoded spaces.");
                }
                Assert.IsTrue((bool)Invoke(section, "CommitServerSource"));
                Assert.AreEqual(1, writes);
                Assert.AreEqual(server, saved);
                Assert.AreEqual(server, field.value);
                Assert.IsTrue(File.Exists(Path.Combine(saved, "pyproject.toml")));
            }
            finally
            {
                host.Close();
                Directory.Delete(directory, true);
            }
        }

        [UnityTest]
        public IEnumerator ServerSourceDraftCommitsAtApplyEnterAndBlurAndKeepsInvalidInput()
        {
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/source-draft-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(Path.Combine(directory, "Server"));
            File.WriteAllText(Path.Combine(directory, "Server/pyproject.toml"), "[project]\nname='synthetic-draft'\n");
            var host = ScriptableObject.CreateInstance<Host>();
            try
            {
                host.ShowUtility();
                var root = AssetDatabase
                    .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/Advanced/McpAdvancedSection.uxml")
                    .CloneTree();
                host.rootVisualElement.Add(root);
                var section = (McpAdvancedSection)FormatterServices.GetUninitializedObject(typeof(McpAdvancedSection));
                Set(section, "<Root>k__BackingField", root);
                Invoke(section, "CacheUIElements");
                string saved = "git+https://example.invalid/previous";
                var writes = new List<string>();
                Set(section, "readServerSource", (Func<string>)(() => saved));
                Set(
                    section,
                    "writeServerSource",
                    (Action<string>)(
                        value =>
                        {
                            saved = value;
                            writes.Add(value);
                        }
                    )
                );
                Invoke(section, "RegisterServerSourceCallbacks");
                Invoke(section, "RefreshServerSource");
                var field = root.Q<TextField>("git-url-override");
                root.Q<Foldout>("settings-paths").value = true;
                field.Focus();
                yield return null;
                foreach (string draft in new[] { directory, directory + "/S", directory + "/", directory })
                {
                    field.value = draft;
                    Invoke(section, "RefreshServerSource");
                    Assert.AreEqual(draft, field.value, "Typing, backspace and refresh must preserve the exact draft.");
                    Assert.Zero(writes.Count);
                }
                var apply = root.Q<Button>("apply-server-source-button");
                Assert.IsNotNull(apply);
                typeof(Clickable).GetMethod("Invoke", Private).Invoke(apply.clickable, new object[] { null });
                Assert.AreEqual(1, writes.Count);
                Assert.AreEqual(Path.Combine(directory, "Server"), saved);
                Assert.AreEqual(saved, field.value);

                field.value = "git+https://example.invalid/entered";
                field.Focus();
                yield return null;
                var focused = host.rootVisualElement.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focused == field || focused != null && field.Contains(focused), "Enter must be dispatched to the real focused source input.");
                Assert.IsTrue((bool)section.GetType().GetField("serverSourceDraftDirty", Private).GetValue(section));
                bool enterObserved = false;
                field.RegisterCallback<KeyDownEvent>(evt => enterObserved |= evt.keyCode == KeyCode.Return, TrickleDown.TrickleDown);
                using (var enter = KeyDownEvent.GetPooled('\n', KeyCode.Return, EventModifiers.None))
                {
                    enter.target = focused;
                    host.rootVisualElement.SendEvent(enter);
                }
                Assert.IsTrue(enterObserved, "The native source-input event route must receive Enter.");
                Assert.AreEqual("git+https://example.invalid/entered", saved);
                Assert.AreEqual(2, writes.Count);
                field.value = "git+https://example.invalid/blurred";
                apply.Focus();
                yield return null;
                Assert.AreEqual("git+https://example.invalid/blurred", saved);
                Assert.AreEqual(3, writes.Count);

                string invalid = Path.Combine(directory, "missing") + "  ";
                field.value = invalid;
                Assert.IsFalse((bool)Invoke(section, "CommitServerSource"));
                Invoke(section, "RefreshServerSource");
                Assert.AreEqual(invalid, field.value);
                Assert.AreEqual(3, writes.Count);
                Assert.IsFalse(root.Q("server-source-status").ClassListContains("hidden"));
                StringAssert.Contains("pyproject.toml", root.Q<Label>("server-source-status").text);
            }
            finally
            {
                host.Close();
                Directory.Delete(directory, true);
            }
        }
    }
}
