using System;
using System.Collections;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class MainWindowAsyncTests
    {
        private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags StaticFlags = BindingFlags.Static | BindingFlags.NonPublic;

        [Test]
        public void DependencyCoroutine_RequestExceptionRestoresButtonAndReleasesGate()
        {
            var button = new Button { text = "Install" };
            IEnumerator routine = null;
            RunAction(button, "Installing...", "Install", done =>
                routine = (IEnumerator)typeof(MCPForUnityEditorWindow).GetMethod("PollUpmRequest", StaticFlags)
                    .Invoke(null, new object[] { null, "install", done }));
            try
            {
                LogAssert.Expect(LogType.Error, new Regex("Package install failed:"));
                Assert.IsFalse(routine.MoveNext());
                Assert.IsTrue(button.enabledSelf);
                Assert.AreEqual("Install", button.text);
                bool retry = false;
                RunAction(new Button(), "Installing...", "Install", done => { retry = true; done(); });
                Assert.IsTrue(retry);
            }
            finally
            {
                (routine as IDisposable)?.Dispose();
            }
        }

        [Test]
        public void UpdateCheck_RefreshDuringFetchKeepsPendingIntent()
        {
            var window = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            try
            {
                var type = typeof(MCPForUnityEditorWindow);
                type.GetField("updateCheckInFlight", InstanceFlags).SetValue(window, true);
                type.GetMethod("QueueUpdateCheck", InstanceFlags).Invoke(window, null);
                type.GetMethod("QueueUpdateCheck", InstanceFlags).Invoke(window, null);
                Assert.IsTrue((bool)type.GetField("updateCheckPending", InstanceFlags).GetValue(window));
                Assert.IsFalse((bool)type.GetField("updateCheckQueued", InstanceFlags).GetValue(window));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [TestCase("main", true)]
        [TestCase("beta", false)]
        public void UpdateCheck_ChangedChannelOrInstallationRejectsFetchedIdentity(string branch, bool git)
        {
            var locatorField = typeof(MCPServiceLocator).GetField("_packageUpdateService", StaticFlags);
            var previous = locatorField.GetValue(null);
            var service = new FakeUpdateService();
            try
            {
                MCPServiceLocator.Register<IPackageUpdateService>(service);
                string version = AssetPathUtility.GetPackageVersion();
                var guard = typeof(MCPForUnityEditorWindow).GetMethod("IsUpdateCheckCurrent", StaticFlags);
                Assert.IsTrue((bool)guard.Invoke(null, new object[] { service, version, true, "beta" }));
                service.Branch = branch;
                service.Git = git;
                Assert.IsFalse((bool)guard.Invoke(null, new object[] { service, version, true, "beta" }));
            }
            finally
            {
                locatorField.SetValue(null, previous);
            }
        }

        [Test]
        public void DependencyAction_FilesystemFailureRestoresButtonAndAllowsRetry()
        {
            var button = new Button { text = "Uninstall All" };
            LogAssert.Expect(LogType.Error, new Regex("Dependency action failed: simulated locked DLL"));
            RunAction(button, "Removing...", "Uninstall All", _ => throw new System.IO.IOException("simulated locked DLL"));

            Assert.IsTrue(button.enabledSelf);
            Assert.AreEqual("Uninstall All", button.text);
            bool retried = false;
            RunAction(button, "Removing...", "Uninstall All", done => { retried = true; done(); });
            Assert.IsTrue(retried);
        }

        [Test]
        public void DependencyAction_PendingOperationSerializesRecreatedWindows()
        {
            var first = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            MCPForUnityEditorWindow reopened = null;
            Action finish = null;
            try
            {
                var firstSection = new VisualElement();
                typeof(MCPForUnityEditorWindow).GetField("dependencySection", InstanceFlags).SetValue(first, firstSection);
                var button = new Button { text = "Install" };
                RunAction(button, "Installing...", "Install", done => finish = done);
                Assert.IsFalse(firstSection.enabledSelf);
                Assert.IsFalse(button.enabledSelf);

                UnityEngine.Object.DestroyImmediate(first);
                first = null;
                reopened = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
                typeof(MCPForUnityEditorWindow).GetMethod("BuildDependenciesSection", InstanceFlags)
                    .Invoke(reopened, new object[] { new VisualElement() });
                var reopenedSection = (VisualElement)typeof(MCPForUnityEditorWindow).GetField("dependencySection", InstanceFlags).GetValue(reopened);
                Assert.IsFalse(reopenedSection.enabledSelf);

                bool overlapping = false;
                RunAction(new Button(), "Removing...", "Uninstall", done => { overlapping = true; done(); });
                Assert.IsFalse(overlapping);
                finish();
                Assert.IsTrue(reopenedSection.enabledSelf);
                Assert.IsTrue(button.enabledSelf);
                Assert.AreEqual("Install", button.text);
            }
            finally
            {
                finish?.Invoke();
                if (first != null) UnityEngine.Object.DestroyImmediate(first);
                if (reopened != null) UnityEngine.Object.DestroyImmediate(reopened);
            }
        }

        [Test]
        public void DependencyAction_DuplicateCompletionCannotReleaseAnotherAction()
        {
            Action firstDone = null;
            Action secondDone = null;
            try
            {
                RunAction(new Button(), "Installing...", "Install", done => firstDone = done);
                firstDone();
                RunAction(new Button(), "Installing...", "Install", done => secondDone = done);
                firstDone();
                bool overlapped = false;
                RunAction(new Button(), "Removing...", "Uninstall", done => { overlapped = true; done(); });
                Assert.IsFalse(overlapped);
            }
            finally
            {
                firstDone?.Invoke();
                secondDone?.Invoke();
            }
        }

        [Test]
        public void SetupWindow_NewDependencyResultRefreshesExistingControls()
        {
            var window = ScriptableObject.CreateInstance<MCPSetupWindow>();
            try
            {
                var status = new Label { text = "Required dependencies missing" };
                var installation = new VisualElement();
                installation.AddToClassList("visible");
                var done = new Button { text = "Close" };
                typeof(MCPSetupWindow).GetField("statusMessage", InstanceFlags).SetValue(window, status);
                typeof(MCPSetupWindow).GetField("installationSection", InstanceFlags).SetValue(window, installation);
                typeof(MCPSetupWindow).GetField("doneButton", InstanceFlags).SetValue(window, done);

                typeof(MCPSetupWindow).GetMethod("SetDependencyResult", InstanceFlags).Invoke(window,
                    new object[] { new DependencyCheckResult { IsSystemReady = true } });

                Assert.AreEqual("System requirements met", status.text);
                Assert.AreEqual("Next", done.text);
                Assert.IsFalse(installation.ClassListContains("visible"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        private static void RunAction(Button button, string busy, string idle, Action<Action> action)
        {
            typeof(MCPForUnityEditorWindow).GetMethod("RunDependencyAction", StaticFlags)
                .Invoke(null, new object[] { button, busy, idle, action });
        }

        private sealed class FakeUpdateService : IPackageUpdateService
        {
            public string Branch = "beta";
            public bool Git = true;
            public UpdateCheckResult CheckForUpdate(string version) => null;
            public UpdateCheckResult TryGetCachedResult(string version) => null;
            public UpdateCheckResult FetchAndCompare(string version) => null;
            public UpdateCheckResult FetchAndCompare(string version, bool git, string branch) => null;
            public void CacheFetchResult(string current, string fetched) { }
            public bool IsNewerVersion(string left, string right) => false;
            public bool IsGitInstallation() => Git;
            public string GetGitUpdateBranch(string version) => Branch;
            public void ClearCache() { }
        }
    }
}
