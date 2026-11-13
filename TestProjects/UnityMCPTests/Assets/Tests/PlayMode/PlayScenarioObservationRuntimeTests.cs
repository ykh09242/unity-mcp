using System;
using System.Collections;
using System.Linq;
using System.Security.Cryptography;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayMode
{
    [Category("playmode_evidence")]
    public class PlayScenarioObservationRuntimeTests
    {
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        private static int OwnedObjects(string name) => Resources.FindObjectsOfTypeAll<GameObject>().Count(item => item != null && item.name == name);

        private sealed class FinalReadProvider : IPlayScenarioStateProvider
        {
            internal const int FinalRead = 8;
            internal const int AllocationBytes = 32 * 1024 * 1024;
            internal bool Allocate;
            internal int Reads;

            public bool TryRead(out PlayScenarioStateValue value)
            {
                Reads++;
                value = PlayScenarioStateValue.FromBoolean(true);
                if (Reads != FinalRead)
                    return false;
                if (Allocate)
                    GC.KeepAlive(new byte[AllocationBytes]);
                return true;
            }
        }

        [UnityTest]
        public IEnumerator FinalWaitStateReadWithoutLargeAllocationPasses() => RunFinalRead(false);

        [UnityTest]
        public IEnumerator FinalWaitStateReadAllocationFailsGcBudget() => RunFinalRead(true);

        private static IEnumerator RunFinalRead(bool allocate)
        {
            const int budget = FinalReadProvider.AllocationBytes / 2;
            const string id = "test.final-gc-read";
            var provider = new FinalReadProvider { Allocate = allocate };
            var definition = new PlayScenarioDefinition { Name = "final-frame", CompletionStableMs = 0 };
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "Final read",
                    Action = "wait_state",
                    StateId = id,
                    StateEquals = true,
                    Performance = new PlayScenarioPerformanceOptions { MaxGcAllocatedBytesPerFrame = budget },
                }
            );
            const long start = 1000;
            var run = PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, start);
            var engine = new PlayScenarioEngine(run, new PlayScenarioPlayerHost(Array.Empty<string>()));
            using (PlayScenarioStateRegistry.Register(id, provider))
            {
                try
                {
                    engine.EnteredPlayMode();
                    for (int frame = 0; frame < 32 && engine.Running; frame++)
                    {
                        engine.Tick(start + frame * definition.PollIntervalMs, true);
                        yield return null;
                    }
                    Assert.That(provider.Reads, Is.EqualTo(FinalReadProvider.FinalRead));
                    var performance = run.Steps[0].Performance;
                    Assert.That(performance, Is.Not.Null, run.Error);
                    if (performance.GcSampleCount == 0 && performance.Error?.Contains("GC") == true)
                        Assert.Inconclusive(performance.Error);
                    Assert.That(performance.GcSampleCount, Is.GreaterThan(1));
                    if (allocate)
                    {
                        Assert.That(performance.GcAllocatedBytesPerFrameMax, Is.GreaterThanOrEqualTo(FinalReadProvider.AllocationBytes));
                        Assert.That(run.Status, Is.EqualTo("failed"));
                        Assert.That(run.Failure.Code, Is.EqualTo("performance_budget_failed"));
                    }
                    else
                    {
                        Assert.That(run.Status, Is.EqualTo("succeeded"), run.Error);
                        Assert.That(performance.GcAllocatedBytesPerFrameMax, Is.LessThanOrEqualTo(budget));
                    }
                }
                finally
                {
                    engine.Release();
                }
            }
        }

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
                Assert.That(observer.Complete(Now), Is.False);
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
                if (!observer.Complete(Now))
                {
                    yield return null;
                    Assert.That(observer.Complete(Now), Is.True);
                }
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
        public IEnumerator SingleEvaluationFrameIsRetainedOnceWhenCompletionIsPolledLate()
        {
            var observer = new PlayScenarioStepObserver();
            var result = new PlayScenarioStepResult();
            try
            {
                observer.Begin(
                    new PlayScenarioStep
                    {
                        Performance = new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 120000, SampleLimit = 1 },
                    },
                    result,
                    Now
                );
                Assert.That(observer.Complete(Now), Is.False);
                for (int frame = 0; frame < 5; frame++)
                    yield return null;
                Assert.That(observer.Complete(Now), Is.True);
                Assert.That(result.Performance.ObservedFrameCount, Is.EqualTo(1));
                Assert.That(result.Performance.Passed, Is.True, result.Performance.Error);
            }
            finally
            {
                observer.Dispose();
            }
        }

        [UnityTest]
        public IEnumerator PausedFrameDeliveryFailsClosedWithoutCountingDrainAsStepDuration()
        {
            var observer = new PlayScenarioStepObserver();
            var result = new PlayScenarioStepResult();
            try
            {
                observer.Begin(new PlayScenarioStep { Performance = new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 120000 } }, result, 1000);
                for (int frame = 0; frame < 5; frame++)
                    yield return null;
                Assert.That(observer.Complete(61000), Is.False);
                // Editor ticks and elapsed wall time can advance while the Player loop is paused.
                Assert.That(observer.Complete(62999), Is.False);
                Assert.That(observer.Complete(63000), Is.True);
                Assert.That(result.Performance.FrameSampleCount, Is.GreaterThan(0));
                Assert.That(result.Performance.Passed, Is.False);
                Assert.That(result.Performance.Error, Does.Contain("final evaluation frame"));
                Assert.That(result.Performance.DurationMs, Is.EqualTo(60000));
            }
            finally
            {
                observer.Dispose();
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
            Assert.That(observer.Complete(Now), Is.False);
            Assert.That(OwnedObjects("__MCP_ScenarioFrameSampler__"), Is.EqualTo(samplers + 1));
            observer.Abort("Cancelled by caller.", Now);
            observer.Dispose();
            yield return null;
            Assert.That(OwnedObjects("__MCP_ScenarioFrameSampler__"), Is.EqualTo(samplers));
            Assert.That(OwnedObjects("__MCP_RecordingFramePump__"), Is.EqualTo(pumps));
            Assert.That(result.Screenshot.Status, Is.EqualTo("unavailable"));
            Assert.That(result.Performance.Passed, Is.False);
            Assert.That(result.Performance.Error, Does.Contain("final evaluation frame"));
        }
    }
}
