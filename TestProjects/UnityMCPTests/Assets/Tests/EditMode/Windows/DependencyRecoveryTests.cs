using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class DependencyRecoveryTests
    {
        private const string Id = "com.unity.cinemachine";
        private const BindingFlags Instance = BindingFlags.NonPublic | BindingFlags.Instance;

        private sealed class Host : EditorWindow { }

        private MCPForUnityEditorWindow controller;
        private Host host;
        private readonly Dictionary<string, string> registered = new();
        private readonly Dictionary<string, string> declared = new();
        private Action<string[], string> completeSearch;
        private int searches;
        private VisualElement Root => host.rootVisualElement;
        private TextField Selection => Root.Q<TextField>(Id + "-version");
        private Button Apply => Root.Q<Button>(Id + "-apply");
        private Label Message => Root.Q<Label>(Id + "-message");

        [SetUp]
        public void SetUp()
        {
            registered.Clear();
            declared.Clear();
            searches = 0;
            controller = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            host = ScriptableObject.CreateInstance<Host>();
            host.ShowUtility();
            Set("readDependencyRegistered", (Func<Dictionary<string, string>>)(() => new Dictionary<string, string>(registered)));
            Set("readDependencyDeclared", (Func<Dictionary<string, string>>)(() => new Dictionary<string, string>(declared)));
            Set(
                "searchDependencyVersions",
                (Action<string, Action<string[], string>>)(
                    (id, done) =>
                    {
                        Assert.AreEqual(Id, id);
                        searches++;
                        completeSearch = done;
                    }
                )
            );
        }

        [TearDown]
        public void TearDown()
        {
            completeSearch?.Invoke(Array.Empty<string>(), "cancelled");
            completeSearch = null;
            if (host != null)
                host.Close();
            if (controller != null)
                UnityEngine.Object.DestroyImmediate(controller);
        }

        [Test]
        public void UnresolvedDeclarationIsNotLabelledInstalled()
        {
            declared[Id] = "2.9.7";
            Build();
            string status = Root.Q<Label>(Id + "-status").text;
            StringAssert.Contains("Declared", status);
            StringAssert.DoesNotContain("Installed", status);
            StringAssert.Contains("not registered", status);
            Assert.IsFalse(Apply.enabledSelf);
        }

        [Test]
        public void InvalidAndUnchangedVersionsCannotBeApplied()
        {
            registered[Id] = declared[Id] = "2.9.7";
            Build();
            Assert.IsFalse(Apply.enabledSelf);
            StringAssert.Contains("already", Message.text);
            Selection.value = Id + "@latest";
            Assert.IsFalse(Apply.enabledSelf);
            StringAssert.Contains("exact", Message.text);
            Selection.value = Id + "@3.0.0";
            Assert.IsTrue(Apply.enabledSelf);
        }

        [Test]
        public void ExplicitRefreshShowsExternalChangesAndKeepsEditedVersion()
        {
            registered[Id] = declared[Id] = "2.9.7";
            Build();
            Selection.value = Id + "@2.9.8";
            registered[Id] = declared[Id] = "3.0.0";
            Click(Root.Q<Button>("dependencies-refresh"));
            Assert.AreEqual(Id + "@2.9.8", Selection.value);
            StringAssert.Contains("3.0.0", Root.Q<Label>(Id + "-status").text);
            Assert.AreEqual(0, searches);
        }

        [Test]
        public void CompatibleLookupIsExplicitAndNeverSelectsOrInstallsAutomatically()
        {
            Build();
            Assert.AreEqual(0, searches);
            Click(Root.Q<Button>(Id + "-find-versions"));
            Assert.AreEqual(1, searches);
            var done = completeSearch;
            completeSearch = null;
            done(new[] { "2.9.7", "latest", "3.0.0", "2.9.7" }, null);
            Assert.AreEqual(Id + "@", Selection.value);
            Assert.IsFalse(Apply.enabledSelf);
            var choices = Root.Q<DropdownField>(Id + "-versions");
            CollectionAssert.DoesNotContain(choices.choices, "latest");
            choices.value = "3.0.0";
            Assert.AreEqual(Id + "@3.0.0", Selection.value);
            Assert.IsTrue(Apply.enabledSelf);
            Assert.IsEmpty(registered);
            Assert.IsEmpty(declared);
        }

        [Test]
        public void LookupFailureKeepsManualDraftAndAllowsRetry()
        {
            Build();
            Selection.value = Id + "@3.0.0";
            var find = Root.Q<Button>(Id + "-find-versions");
            Click(find);
            var done = completeSearch;
            completeSearch = null;
            done(null, "synthetic-private-detail");
            Assert.AreEqual(Id + "@3.0.0", Selection.value);
            Assert.IsTrue(find.enabledSelf);
            StringAssert.DoesNotContain("synthetic-private-detail", Root.Q<Label>(Id + "-lookup-status").text);
            Click(find);
            Assert.AreEqual(2, searches);
        }

        [Test]
        public void ExternalSourceChangeBlocksAStaleInstallBeforeAnyUpmRequest()
        {
            Build();
            Selection.value = Id + "@3.0.0";
            declared[Id] = "file:../synthetic-package";
            Click(Apply);
            StringAssert.Contains("changed", Message.text);
            Assert.IsFalse(Apply.enabledSelf);
            Assert.AreEqual(Id + "@3.0.0", Selection.value);
        }

        [Test]
        public void LookupRequestFailureCompletesOnceAndReleasesTheActionGate()
        {
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            int completions = 0;
            var routine = (System.Collections.IEnumerator)
                typeof(MCPForUnityEditorWindow)
                    .GetMethod("PollCompatibleVersions", flags)
                    .Invoke(
                        null,
                        new object[]
                        {
                            null,
                            Id,
                            (Action<string[], string>)(
                                (versions, error) =>
                                {
                                    completions++;
                                    Assert.IsNotNull(error);
                                }
                            ),
                        }
                    );
            Assert.IsFalse(routine.MoveNext());
            Assert.AreEqual(1, completions);
        }

        private void Set(string fieldName, object value)
        {
            var field = typeof(MCPForUnityEditorWindow).GetField(fieldName, Instance);
            Assert.IsNotNull(field, fieldName);
            field.SetValue(controller, value);
        }

        private void Build() => typeof(MCPForUnityEditorWindow).GetMethod("BuildDependenciesSection", Instance).Invoke(controller, new object[] { Root });

        private static void Click(Button button)
        {
            Assert.IsNotNull(button);
            using var evt = NavigationSubmitEvent.GetPooled();
            evt.target = button;
            button.SendEvent(evt);
        }
    }
}
