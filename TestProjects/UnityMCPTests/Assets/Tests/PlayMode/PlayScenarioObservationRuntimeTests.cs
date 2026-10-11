using System;
using System.Collections;
using System.Linq;
using System.Security.Cryptography;
using MCPForUnity.Editor.Services.PlayScenarios;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayMode
{
    public class PlayScenarioObservationRuntimeTests
    {
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        private static int OwnedObjects(string name) => Resources.FindObjectsOfTypeAll<GameObject>().Count(item => item != null && item.name == name);

        [UnityTest]
        public IEnumerator NativeFramesAreCollectedAndSamplerIsReleased()
        {
            int initial = OwnedObjects("__MCP_ScenarioFrameSampler__");
            var result = new PlayScenarioStepResult();
            var observer = new PlayScenarioStepObserver();
            try
            {
                observer.Begin(new PlayScenarioStep { Performance = new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 120000 } }, result, Now);
                for (int frame = 0; frame < 6; frame++)
                    yield return null;
                Assert.That(observer.Complete(Now), Is.True);
                Assert.That(result.Performance.Passed, Is.True, result.Performance.Error);
                Assert.That(result.Performance.FrameSampleCount, Is.GreaterThanOrEqualTo(3));
                Assert.That(result.Performance.FrameTimeP95Ms, Is.GreaterThan(0));
                Assert.That(result.Performance.GcSampleCount, Is.EqualTo(0));
                Assert.That(OwnedObjects("__MCP_ScenarioFrameSampler__"), Is.EqualTo(initial));
            }
            finally
            {
                observer.Dispose();
            }
        }

        [UnityTest]
        public IEnumerator NativeGcCounterIsMeasuredOrExplicitlyUnavailable()
        {
            int initial = OwnedObjects("__MCP_ScenarioFrameSampler__");
            var result = new PlayScenarioStepResult();
            var observer = new PlayScenarioStepObserver();
            try
            {
                observer.Begin(
                    new PlayScenarioStep { Performance = new PlayScenarioPerformanceOptions { MaxGcAllocatedBytesPerFrame = int.MaxValue } },
                    result,
                    Now
                );
                for (int frame = 0; frame < 6; frame++)
                    yield return null;
                observer.Complete(Now);
                if (result.Performance.Passed)
                {
                    Assert.That(result.Performance.GcSampleCount, Is.GreaterThan(0));
                    Assert.That(result.Performance.GcAllocatedBytesPerFrameMax, Is.GreaterThanOrEqualTo(0));
                }
                else
                    Assert.That(result.Performance.Error, Does.Contain("GC"));
                Assert.That(OwnedObjects("__MCP_ScenarioFrameSampler__"), Is.EqualTo(initial));
            }
            finally
            {
                observer.Dispose();
            }
        }

        [UnityTest]
        public IEnumerator RenderedCheckpointCapturesExactDimensionsOrDeclaresHeadlessUnavailable()
        {
            int initial = OwnedObjects("__MCP_RecordingFramePump__");
            var result = new PlayScenarioStepResult();
            byte[] captured = null;
            var observer = new PlayScenarioStepObserver(
                (step, png) =>
                {
                    captured = png;
                    return "native-checkpoint.png";
                }
            );
            var cameraObject = new GameObject("ScenarioCheckpointTestCamera");
            cameraObject.AddComponent<Camera>().backgroundColor = Color.cyan;
            try
            {
                observer.Begin(
                    new PlayScenarioStep
                    {
                        Screenshot = new PlayScenarioScreenshotOptions
                        {
                            Name = "native",
                            Width = Screen.width,
                            Height = Screen.height,
                        },
                    },
                    result,
                    Now
                );
                observer.Complete(Now);
                long deadline = Now + 3000;
                while (result.Screenshot.Status == "pending" && Now < deadline)
                {
                    yield return null;
                    observer.Complete(Now);
                }
                if (Application.isBatchMode || SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                {
                    Assert.That(result.Screenshot.Status, Is.EqualTo("unavailable"));
                    Assert.That(captured, Is.Null);
                    Assert.That(result.Screenshot.Path, Is.Null);
                }
                else
                {
                    Assert.That(result.Screenshot.Status, Is.EqualTo("captured"), result.Screenshot.Error);
                    Assert.That(result.Screenshot.Width, Is.EqualTo(result.Screenshot.RequestedWidth));
                    Assert.That(result.Screenshot.Height, Is.EqualTo(result.Screenshot.RequestedHeight));
                    Assert.That(captured.Length, Is.GreaterThan(0));
                    using (SHA256 hash = SHA256.Create())
                        Assert.That(
                            result.Screenshot.Sha256,
                            Is.EqualTo(BitConverter.ToString(hash.ComputeHash(captured)).Replace("-", "").ToLowerInvariant())
                        );
                }
                Assert.That(OwnedObjects("__MCP_RecordingFramePump__"), Is.EqualTo(initial));
            }
            finally
            {
                observer.Dispose();
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [UnityTest]
        public IEnumerator CancelledNativeObservationReleasesSamplerAndPendingCapture()
        {
            int samplers = OwnedObjects("__MCP_ScenarioFrameSampler__");
            int pumps = OwnedObjects("__MCP_RecordingFramePump__");
            var observer = new PlayScenarioStepObserver((step, png) => "cancelled.png");
            var result = new PlayScenarioStepResult();
            observer.Begin(
                new PlayScenarioStep
                {
                    Performance = new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 120000 },
                    Screenshot = new PlayScenarioScreenshotOptions
                    {
                        Name = "cancelled",
                        Width = Screen.width,
                        Height = Screen.height,
                    },
                },
                result,
                Now
            );
            yield return null;
            observer.Complete(Now);
            observer.Abort("Cancelled by caller.", Now);
            observer.Dispose();
            yield return null;
            Assert.That(OwnedObjects("__MCP_ScenarioFrameSampler__"), Is.EqualTo(samplers));
            Assert.That(OwnedObjects("__MCP_RecordingFramePump__"), Is.EqualTo(pumps));
            Assert.That(result.Screenshot.Status, Is.EqualTo("unavailable"));
        }
    }
}
