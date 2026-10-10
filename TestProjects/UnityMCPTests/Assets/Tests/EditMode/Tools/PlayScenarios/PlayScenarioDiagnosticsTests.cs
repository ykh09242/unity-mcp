using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioDiagnosticsTests
    {
        private static readonly Assembly Product = typeof(PlayScenarioService).Assembly;

        [Test]
        public void NativeMetricSampleUsesScalarValuesAndObservesOwnedSceneObjects()
        {
            var method = Product
                .GetType("MCPForUnity.Editor.Services.PlayScenarios.UnityPlayScenarioDiagnostics")
                .GetMethod("CaptureMetrics", BindingFlags.Static | BindingFlags.NonPublic);
            var before = (PlayScenarioMetricsSnapshot)method.Invoke(null, new object[] { 1, 100L, 0 });
            var owned = new GameObject("ScenarioMetricsOwnedObject");
            try
            {
                var sample = (PlayScenarioMetricsSnapshot)method.Invoke(null, new object[] { 2, 200L, 1 });
                Assert.That(sample.Error, Is.Null);
                Assert.That(sample.Iteration, Is.EqualTo(2));
                Assert.That(sample.TimestampUnixMs, Is.EqualTo(200));
                Assert.That(sample.ManagedBytes, Is.GreaterThan(0));
                Assert.That(sample.AllocatedBytes, Is.GreaterThan(0));
                Assert.That(sample.RunnerHandleCount, Is.EqualTo(1));
                Assert.That(sample.ObjectCount, Is.GreaterThanOrEqualTo(before.ObjectCount + 1));
                foreach (FieldInfo field in typeof(PlayScenarioMetricsSnapshot).GetFields())
                    Assert.That(typeof(UnityEngine.Object).IsAssignableFrom(field.FieldType), Is.False);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owned);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnavailableOrDisabledCaptureLeavesNoFramePumpAndDisposeIsIdempotent(bool enabled)
        {
            if (EditorApplication.isPlaying)
                Assert.Ignore("This capability check requires Edit Mode.");
            Type type = Product.GetType("MCPForUnity.Editor.Services.PlayScenarios.PlayScenarioFailureCapture");
            var state = new PlayScenarioRun
            {
                JobId = Guid.NewGuid().ToString("N"),
                Scenario = new PlayScenarioDefinition(),
                FailureDiagnostics = new PlayScenarioFailureDiagnostics(),
            };
            state.Scenario.Diagnostics.ScreenshotOnFailure = enabled;
            var store = new PlayScenarioStore(Path.GetFullPath(Path.Combine(Application.dataPath, "..")));
            var capture = (IDisposable)
                Activator.CreateInstance(type, BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { state, store, 0L, false }, null);
            try
            {
                Assert.That(type.GetProperty("Pending", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(capture), Is.False);
                Assert.That(type.GetField("_pump", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(capture), Is.Null);
                Assert.That(state.FailureDiagnostics.ScreenshotPath, Is.Null);
                if (enabled)
                    StringAssert.Contains("interactive", state.FailureDiagnostics.ScreenshotError);
                else
                    Assert.That(state.FailureDiagnostics.ScreenshotError, Is.Null);
            }
            finally
            {
                capture.Dispose();
                capture.Dispose();
            }
        }
    }
}
