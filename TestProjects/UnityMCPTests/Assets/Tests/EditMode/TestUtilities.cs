using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor
{
    /// <summary>
    /// Shared test utilities for EditMode tests across the MCP for Unity test suite.
    /// Consolidates common patterns to avoid duplication across test files.
    /// </summary>
    public static class TestUtilities
    {
        internal static double TaskWaitSeconds => MCPForUnity.Editor.Helpers.OperationTimeouts.EnvironmentSeconds("UNITY_MCP_TEST_WAIT_TIMEOUT", 60);

        internal static IEnumerator WaitUntil(Func<bool> ready, string phase, Func<bool> producerCompleted = null, double? timeoutSeconds = null)
        {
            var elapsed = Stopwatch.StartNew();
            double budget = timeoutSeconds ?? TaskWaitSeconds;
            while (!ready())
            {
                if (producerCompleted?.Invoke() == true)
                {
                    Assert.IsTrue(ready(), $"The producer completed before {phase}.");
                    yield break;
                }
                Assert.Less(elapsed.Elapsed.TotalSeconds, budget, $"Timed out waiting for {phase}.");
                yield return null;
            }
        }

        /// <summary>
        /// Safely converts a command result to JObject, handling both JSON objects and other types.
        /// Returns an empty JObject if result is null.
        /// </summary>
        public static JObject ToJObject(object result)
        {
            if (result == null)
                return new JObject();
            return result as JObject ?? JObject.FromObject(result);
        }

        /// <summary>
        /// Creates all parent directories for the given asset path if they don't exist.
        /// Handles normalization and validates against dangerous patterns.
        /// </summary>
        /// <param name="folderPath">An Assets-relative folder path (e.g., "Assets/Temp/MyFolder")</param>
        public static void EnsureFolder(string folderPath)
        {
            if (AssetDatabase.IsValidFolder(folderPath))
                return;

            var sanitized = MCPForUnity.Editor.Helpers.AssetPathUtility.SanitizeAssetPath(folderPath);
            if (string.Equals(sanitized, "Assets", StringComparison.OrdinalIgnoreCase))
                return;

            var parts = sanitized.Split('/');
            string current = "Assets";
            for (int i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                {
                    AssetDatabase.CreateFolder(current, parts[i]);
                }
                current = next;
            }
        }

        /// <summary>
        /// Waits for Unity to finish compiling and updating, with a configurable timeout.
        /// Some EditMode tests trigger script compilation/domain reload.
        /// Tools intentionally return "compiling_or_reloading" during these windows.
        /// </summary>
        /// <param name="timeoutSeconds">Maximum time to wait before failing the test.</param>
        public static IEnumerator WaitForUnityReady(double timeoutSeconds = 30.0)
        {
            double start = EditorApplication.timeSinceStartup;
            while (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                if (EditorApplication.timeSinceStartup - start > timeoutSeconds)
                {
                    Assert.Fail($"Timed out waiting for Unity to finish compiling/updating (>{timeoutSeconds:0.0}s).");
                }
                yield return null;
            }
        }

        /// <summary>
        /// Finds a fallback shader for creating materials in tests.
        /// Tries modern pipelines first, then falls back to Standard/Unlit.
        /// </summary>
        /// <returns>A shader suitable for test materials, or null if none found.</returns>
        public static Shader FindFallbackShader()
        {
            return Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("HDRP/Lit") ?? Shader.Find("Standard") ?? Shader.Find("Unlit/Color");
        }

        /// <summary>
        /// Safely deletes an asset if it exists.
        /// </summary>
        /// <param name="path">The asset path to delete.</param>
        public static void SafeDeleteAsset(string path)
        {
            if (!string.IsNullOrEmpty(path) && AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path) != null)
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        /// <summary>
        /// Cleans up empty parent folders recursively up to but not including "Assets".
        /// Useful in TearDown to avoid leaving folder debris.
        /// </summary>
        /// <param name="folderPath">The starting folder path to check.</param>
        public static void CleanupEmptyParentFolders(string folderPath)
        {
            if (string.IsNullOrEmpty(folderPath))
                return;

            var parent = Path.GetDirectoryName(folderPath)?.Replace('\\', '/');
            while (!string.IsNullOrEmpty(parent) && parent != "Assets")
            {
                if (AssetDatabase.IsValidFolder(parent))
                {
                    try
                    {
                        var dirs = Directory.GetDirectories(parent);
                        var files = Directory.GetFiles(parent);
                        if (dirs.Length == 0 && files.Length == 0)
                        {
                            AssetDatabase.DeleteAsset(parent);
                            parent = Path.GetDirectoryName(parent)?.Replace('\\', '/');
                        }
                        else
                        {
                            break;
                        }
                    }
                    catch
                    {
                        break;
                    }
                }
                else
                {
                    break;
                }
            }
        }
    }

    /// <summary>Releases test-owned gates and cancellation before asynchronously settling their tasks.</summary>
    internal sealed class OwnedTestTasks
    {
        private readonly List<Task> tasks = new List<Task>();
        private readonly List<Action> stop = new List<Action>();
        private readonly List<IDisposable> resources = new List<IDisposable>();

        internal T Track<T>(T task)
            where T : Task
        {
            tasks.Add(task);
            return task;
        }

        internal T Own<T>(T resource)
            where T : IDisposable
        {
            resources.Add(resource);
            return resource;
        }

        internal void OnCleanup(Action action) => stop.Add(action);

        internal IEnumerator Cleanup()
        {
            var errors = new List<Exception>();
            foreach (var action in stop)
            {
                try
                {
                    action();
                }
                catch (Exception error)
                {
                    errors.Add(error);
                }
            }
            var elapsed = Stopwatch.StartNew();
            while (tasks.Exists(task => !task.IsCompleted) && elapsed.Elapsed.TotalSeconds < TestUtilities.TaskWaitSeconds)
                yield return null;
            bool settled = tasks.TrueForAll(task => task.IsCompleted);
            foreach (var task in tasks)
                if (task.IsFaulted)
                    _ = task.Exception;
            // A late callback may still use these handles. Do not dispose them underneath it.
            if (settled)
            {
                foreach (var resource in resources)
                {
                    try
                    {
                        resource.Dispose();
                    }
                    catch (Exception error)
                    {
                        errors.Add(error);
                    }
                }
            }
            Assert.IsTrue(settled, "Test-owned tasks did not settle after cancellation and gate release.");
            Assert.IsEmpty(errors, "Test-owned task cleanup failed.");
        }
    }

    public class CoroutineWaitTests
    {
        [Test]
        public void IncompleteTaskKeepsYieldingPastTheLegacyFrameLimit()
        {
            var producer = new TaskCompletionSource<bool>();
            var wait = TestUtilities.WaitUntil(() => producer.Task.IsCompleted, "a deliberately late worker");
            for (int frame = 0; frame < 301; frame++)
                Assert.IsTrue(wait.MoveNext(), "An unfinished task must keep yielding, never synchronously retrieve its result.");
            producer.SetResult(true);
            Assert.IsFalse(wait.MoveNext());
        }

        [Test]
        public void ProducerCompletionBeforeEntryFailsWithoutWaitingForTheWatchdog()
        {
            var producer = new TaskCompletionSource<bool>();
            var wait = TestUtilities.WaitUntil(() => false, "reader entry", () => producer.Task.IsCompleted);
            Assert.IsTrue(wait.MoveNext());
            producer.SetResult(true);
            StringAssert.Contains("producer completed before reader entry", Assert.Throws<AssertionException>(() => wait.MoveNext()).Message);
        }

        [Test]
        public void MissingPhaseWithLiveProducerFailsAtTheMonotonicWatchdog()
        {
            var wait = TestUtilities.WaitUntil(() => false, "a missing phase", () => false, timeoutSeconds: 0);
            StringAssert.Contains("Timed out waiting for a missing phase", Assert.Throws<AssertionException>(() => wait.MoveNext()).Message);
        }

        [Test]
        public void CompletedPhaseWinsSimultaneousProducerCompletion()
        {
            Assert.IsFalse(TestUtilities.WaitUntil(() => true, "reader entry", () => true).MoveNext());
        }

        [Test]
        public void PhasePublishedBetweenReadinessAndCompletionChecksStillWins()
        {
            bool ready = false;
            var wait = TestUtilities.WaitUntil(
                () => ready,
                "reader entry",
                () =>
                {
                    ready = true;
                    return true;
                }
            );
            Assert.IsFalse(wait.MoveNext());
        }

        [Test]
        public void CleanupReleasesTheGateThenYieldsUntilOwnedWorkSettles()
        {
            var owned = new OwnedTestTasks();
            var completion = new TaskCompletionSource<bool>();
            var producer = owned.Track(completion.Task);
            bool stopped = false;
            owned.OnCleanup(() => stopped = true);
            var cleanup = owned.Cleanup();
            Assert.IsTrue(cleanup.MoveNext());
            Assert.IsTrue(stopped, "Cancellation and gate release must precede asynchronous settlement.");
            Assert.IsFalse(producer.IsCompleted);
            completion.SetResult(true);
            Assert.IsFalse(cleanup.MoveNext());
        }
    }

    /// <summary>
    /// Synchronous IMcpTransportClient fake for transport-lifecycle tests: StartAsync
    /// completes immediately with StartResult so retry loops run without awaits
    /// (the test framework floor cannot run async tests).
    /// </summary>
    public sealed class FakeTransportClient : IMcpTransportClient
    {
        public bool StartResult = true;
        public int StartCalls;
        public Action OnStart;

        public bool IsConnected { get; private set; }
        public string TransportName => "http";
        public TransportState State { get; private set; } = TransportState.Disconnected("http");

        public Task<bool> StartAsync()
        {
            StartCalls++;
            OnStart?.Invoke();
            IsConnected = StartResult;
            State = StartResult ? TransportState.Connected("http") : TransportState.Disconnected("http", "fake start failure");
            return Task.FromResult(StartResult);
        }

        public Task StopAsync()
        {
            IsConnected = false;
            State = TransportState.Disconnected("http");
            return Task.CompletedTask;
        }

        public Task<bool> VerifyAsync() => Task.FromResult(IsConnected);

        public Task ReregisterToolsAsync() => Task.CompletedTask;
    }
}
