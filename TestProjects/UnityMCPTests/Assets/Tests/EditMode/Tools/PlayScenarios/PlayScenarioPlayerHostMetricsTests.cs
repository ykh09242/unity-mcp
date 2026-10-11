using System;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests
{
    public sealed class PlayScenarioPlayerHostMetricsTests
    {
        private static readonly MethodInfo Reset = typeof(PlayScenarioResourceTracker).GetMethod(
            "ResetOnPlayEntry",
            BindingFlags.Static | BindingFlags.NonPublic
        );

        [SetUp]
        public void SetUp() => Reset.Invoke(null, null);

        [TearDown]
        public void TearDown() => Reset.Invoke(null, null);

        [Test]
        public void BoundaryMetricsCountInactiveChildrenAndReadCurrentRunnerSubscriptions()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            int subscriptions = 1;
            var host = new PlayScenarioPlayerHost(Array.Empty<string>(), () => subscriptions);
            try
            {
                Assert.That(scene.IsValid() && scene.isLoaded, Is.True, "The fixture scene must be loaded for boundary object sampling.");
                PlayScenarioMetricsSnapshot before = host.CaptureMetrics(1, 1000);
                var root = new GameObject("MetricsRoot");
                SceneManager.MoveGameObjectToScene(root, scene);
                var inactive = new GameObject("Inactive");
                inactive.transform.SetParent(root.transform);
                inactive.SetActive(false);
                new GameObject("InactiveChild").transform.SetParent(inactive.transform);
                subscriptions = 2;
                PlayScenarioMetricsSnapshot after = host.CaptureMetrics(2, 2000);
                Assert.That(before.ObjectCount, Is.Not.Null, before.Error);
                Assert.That(after.ObjectCount, Is.Not.Null, after.Error);
                Assert.That(after.ObjectCount, Is.EqualTo(before.ObjectCount + 3));
                Assert.That(before.RunnerSubscriptionCount, Is.EqualTo(1));
                Assert.That(after.RunnerSubscriptionCount, Is.EqualTo(2));
                Assert.That(after.RunnerHandleCount, Is.Zero);
                Assert.That(after.Iteration, Is.EqualTo(2));
                Assert.That(after.TimestampUnixMs, Is.EqualTo(2000));
                Assert.That(after.ManagedBytes, Is.GreaterThan(0));
                if (after.AllocatedBytes.HasValue)
                    Assert.That(after.AllocatedBytes, Is.GreaterThan(0));
                else
                    Assert.That(after.Error, Does.Contain("unavailable"));
                host.Release();
                Assert.That(host.CaptureMetrics(3, 3000).RunnerSubscriptionCount, Is.Zero);
            }
            finally
            {
                host.Release();
                SceneManager.SetActiveScene(previous);
                EditorSceneManager.CloseScene(scene, true);
            }
        }

        [Test]
        public void HostResourceCaptureReportsTokensUntilTheirIdempotentRelease()
        {
            var host = new PlayScenarioPlayerHost(Array.Empty<string>());
            IDisposable subscription = PlayScenarioResourceTracker.RegisterSubscription("Player host test");
            IDisposable handle = PlayScenarioResourceTracker.RegisterHandle("Player host test");
            try
            {
                PlayScenarioRegisteredResources before = host.CaptureResources();
                Assert.That(before.SubscriptionCount, Is.EqualTo(1));
                Assert.That(before.HandleCount, Is.EqualTo(1));
                subscription.Dispose();
                subscription.Dispose();
                handle.Dispose();
                handle.Dispose();
                PlayScenarioRegisteredResources after = host.CaptureResources();
                Assert.That(after.SubscriptionCount, Is.Zero);
                Assert.That(after.HandleCount, Is.Zero);
                Assert.That(after.RegistrationFailureCount, Is.Zero);
            }
            finally
            {
                subscription.Dispose();
                handle.Dispose();
                host.Release();
            }
        }

        [Test]
        public void HostResourceCaptureUsesNativeCloneLifetimeEvenWithARetainedWrapper()
        {
            var host = new PlayScenarioPlayerHost(Array.Empty<string>());
            var clone = ScriptableObject.CreateInstance<MCPForUnityTests.Editor.Tools.PlayScenarios.PlayScenarioRegisteredClone>();
            try
            {
                PlayScenarioResourceTracker.RegisterScriptableObject(clone);
                Assert.That(host.CaptureResources().ScriptableObjectCount, Is.EqualTo(1));
                UnityEngine.Object.DestroyImmediate(clone);
                Assert.That(ReferenceEquals(clone, null), Is.False);
                Assert.That(host.CaptureResources().ScriptableObjectCount, Is.Zero);
            }
            finally
            {
                if (clone != null)
                    UnityEngine.Object.DestroyImmediate(clone);
                host.Release();
            }
        }

        [Test]
        public void ResourceCaptureRejectsWorkerThreadBeforeNativeLookup()
        {
            var host = new PlayScenarioPlayerHost(Array.Empty<string>());
            Exception observed = null;
            var thread = new Thread(() =>
            {
                try
                {
                    host.CaptureResources();
                }
                catch (Exception error)
                {
                    observed = error;
                }
            });
            thread.Start();
            Assert.That(thread.Join(5000), Is.True);
            Assert.That(observed, Is.TypeOf<InvalidOperationException>());
            Assert.That(observed.Message, Does.Contain("Unity main thread"));
            Assert.That(host.CaptureResources().RegistrationFailureCount, Is.Zero);
        }

        [Test]
        public void InvalidSubscriptionMeasurementIsUnavailableRatherThanZero()
        {
            var host = new PlayScenarioPlayerHost(Array.Empty<string>(), () => -1);
            PlayScenarioMetricsSnapshot snapshot = host.CaptureMetrics(1, 1000);
            Assert.That(snapshot.RunnerSubscriptionCount, Is.Null);
            Assert.That(snapshot.Error, Does.Contain("nonnegative"));
            host.Release();
        }
    }
}
