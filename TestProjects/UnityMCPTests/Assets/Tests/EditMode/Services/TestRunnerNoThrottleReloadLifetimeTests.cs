using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    public class TestRunnerNoThrottleReloadLifetimeTests
    {
        [Test]
        public void ReleaseApi_IsIdempotentAndPreservesActiveRunState()
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Static;
            var apiField = typeof(TestRunnerNoThrottle).GetField("_api", flags);
            var callbacksField = typeof(TestRunnerNoThrottle).GetField("_callbacks", flags);
            var release = typeof(TestRunnerNoThrottle).GetMethod("ReleaseApi", flags);
            var originalApi = apiField.GetValue(null);
            var originalCallbacks = callbacksField.GetValue(null);
            var api = ScriptableObject.CreateInstance<TestRunnerApi>();
            var callbacks = new NoOpCallbacks();
            bool activeBefore = SessionState.GetBool("TestRunnerNoThrottle_TestRunActive", false);
            bool capturedBefore = SessionState.GetBool("TestRunnerNoThrottle_SettingsCaptured", false);
            try
            {
                api.RegisterCallbacks(callbacks);
                // Only the temporary API is destroyed; the real test-run owner's callback remains registered.
                apiField.SetValue(null, api);
                callbacksField.SetValue(null, callbacks);
                Assert.DoesNotThrow(() => release.Invoke(null, null));
                Assert.DoesNotThrow(() => release.Invoke(null, null));
                Assert.That(api == null, Is.True);
                Assert.That(apiField.GetValue(null), Is.Null);
                Assert.That(callbacksField.GetValue(null), Is.Null);
                Assert.That(SessionState.GetBool("TestRunnerNoThrottle_TestRunActive", false), Is.EqualTo(activeBefore));
                Assert.That(SessionState.GetBool("TestRunnerNoThrottle_SettingsCaptured", false), Is.EqualTo(capturedBefore));
            }
            finally
            {
                apiField.SetValue(null, originalApi);
                callbacksField.SetValue(null, originalCallbacks);
                if (api != null)
                {
                    api.UnregisterCallbacks(callbacks);
                    ScriptableObject.DestroyImmediate(api);
                }
            }
        }

        private sealed class NoOpCallbacks : IErrorCallbacks
        {
            public void RunStarted(ITestAdaptor testsToRun) { }

            public void RunFinished(ITestResultAdaptor result) { }

            public void TestStarted(ITestAdaptor test) { }

            public void TestFinished(ITestResultAdaptor result) { }

            public void OnError(string message) { }
        }
    }
}
