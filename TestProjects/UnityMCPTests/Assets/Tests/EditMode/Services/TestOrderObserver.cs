using System;
using System.Collections;
using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    internal sealed class TestOrderObserver : ICallbacks
    {
        private static string project => Path.GetDirectoryName(Application.dataPath);
        private static string session;
        private static TestRunnerApi runner;
        private bool originalEnabled;
        private EnterPlayModeOptions originalOptions;
        private readonly JArray events = new JArray();
        private readonly JArray transitions = new JArray();
        private static readonly string domain = Guid.NewGuid().ToString("N");

        [InitializeOnLoadMethod]
        private static void Register()
        {
            var args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-mcpOrderSession");
            if (!Application.isBatchMode || index < 0 || index + 1 >= args.Length || !Guid.TryParseExact(args[index + 1], "N", out _))
                return;
            string marker = Path.Combine(project, ".mcp-order-session");
            if (!File.Exists(marker) || File.ReadAllText(marker) != args[index + 1])
                return;
            session = args[index + 1];
            runner = ScriptableObject.CreateInstance<TestRunnerApi>();
            runner.RegisterCallbacks(new TestOrderObserver(), 100);
        }

        internal static bool Active => session != null;
        internal static event Action Finished;

        private static JObject Settings() =>
            new JObject
            {
                ["enabled"] = EditorSettings.enterPlayModeOptionsEnabled,
                ["options"] = (int)EditorSettings.enterPlayModeOptions,
                ["domainReloadDisabled"] =
                    EditorSettings.enterPlayModeOptionsEnabled && (EditorSettings.enterPlayModeOptions & EnterPlayModeOptions.DisableDomainReload) != 0,
            };

        private JObject report;

        public void RunStarted(ITestAdaptor test)
        {
            originalEnabled = EditorSettings.enterPlayModeOptionsEnabled;
            originalOptions = EditorSettings.enterPlayModeOptions;
            report = new JObject
            {
                ["session"] = session,
                ["unityVersion"] = Application.unityVersion,
                ["before"] = Settings(),
                ["events"] = events,
                ["transitions"] = transitions,
            };
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
            EditorSettings.enterPlayModeOptionsEnabled = true;
            report["during"] = Settings();
            EditorApplication.playModeStateChanged += PlayModeChanged;
        }

        private void PlayModeChanged(PlayModeStateChange state) =>
            transitions.Add(
                new JObject
                {
                    ["state"] = state.ToString(),
                    ["domain"] = domain,
                    ["settings"] = Settings(),
                }
            );

        public void TestStarted(ITestAdaptor test)
        {
            if (!test.IsSuite)
                events.Add(
                    new JObject
                    {
                        ["phase"] = "started",
                        ["test"] = test.FullName,
                        ["settings"] = Settings(),
                    }
                );
        }

        public void TestFinished(ITestResultAdaptor result)
        {
            if (result.Test != null && !result.Test.IsSuite)
                events.Add(
                    new JObject
                    {
                        ["phase"] = "finished",
                        ["test"] = result.Test.FullName,
                        ["result"] = result.TestStatus.ToString(),
                        ["message"] = result.Message,
                    }
                );
        }

        public void RunFinished(ITestResultAdaptor result)
        {
            if (report == null)
                return;
            EditorApplication.playModeStateChanged -= PlayModeChanged;
            EditorSettings.enterPlayModeOptions = originalOptions;
            EditorSettings.enterPlayModeOptionsEnabled = originalEnabled;
            report["after"] = Settings();
            report["runResult"] = result.TestStatus.ToString();
            Finished?.Invoke();
            Finished = null;
            File.WriteAllText(Path.Combine(project, "Library", "McpTestOrder.json"), report.ToString());
        }
    }

    public class DomainReloadOrderControl
    {
        private static Action onSignal;

        private static void Listener() { }

        [SetUp]
        public void RequireOwnedOrderTrial()
        {
            if (!TestOrderObserver.Active)
                Assert.Ignore("This deliberate negative control runs only in a disposable order trial.");
        }

        [UnityTest]
        public IEnumerator Subscribe()
        {
            yield return new EnterPlayMode(false);
            Assert.IsTrue(Application.isPlaying);
            onSignal += Listener;
            TestOrderObserver.Finished += ReleaseOwnedSubscription;
            yield return new ExitPlayMode();
            Assert.IsFalse(Application.isPlaying);
        }

        [UnityTest]
        public IEnumerator CheckSubscribers()
        {
            yield return new EnterPlayMode(false);
            Assert.IsTrue(Application.isPlaying);
            int observed = onSignal?.GetInvocationList().Length ?? 0;
            yield return new ExitPlayMode();
            Assert.IsFalse(Application.isPlaying);
            TestContext.WriteLine("Retained subscribers observed in Play Mode: " + observed);
            Assert.AreEqual(0, observed, "A previous Play Mode session retained a static subscriber.");
        }

        private static void ReleaseOwnedSubscription()
        {
            onSignal = null;
        }
    }
}
