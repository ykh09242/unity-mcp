using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Dependencies;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class DependencySnapshotTests
    {
        private static object NewCache()
        {
            var type = typeof(DependencyManager).Assembly.GetType("MCPForUnity.Editor.Dependencies.DependencyCheckCache");
            Assert.IsNotNull(type, "Dependency requests need one reusable asynchronous snapshot");
            return Activator.CreateInstance(type, true);
        }

        private static Task<DependencyCheckResult> Request(
            object cache,
            string key,
            Func<CancellationToken, DependencyCheckResult> probe,
            bool force = false
        ) => (Task<DependencyCheckResult>)cache.GetType().GetMethod("Request").Invoke(cache, new object[] { key, probe, force });

        [Test]
        public void SlowProbeReturnsImmediatelyAndCoalescesOpenAndRefresh()
        {
            var cache = NewCache();
            using var started = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            int calls = 0;
            Func<CancellationToken, DependencyCheckResult> probe = token =>
            {
                Interlocked.Increment(ref calls);
                started.Set();
                if (!release.Wait(TimeSpan.FromSeconds(5), token))
                    throw new TimeoutException();
                return new DependencyCheckResult();
            };
            Task<DependencyCheckResult> task = null;
            try
            {
                task = Request(cache, "same-input", probe);
                Assert.IsTrue(started.Wait(2000));
                Assert.IsFalse(task.IsCompleted, "The caller must not wait for the subprocess");
                Assert.AreSame(task, Request(cache, "same-input", probe));
                Assert.AreSame(task, Request(cache, "same-input", probe, true));
            }
            finally
            {
                release.Set();
                if (task != null)
                    task.Wait(5000);
            }
            Assert.AreEqual(1, calls);
            Assert.AreSame(task, Request(cache, "same-input", probe));
            Assert.IsTrue(Request(cache, "same-input", probe, true).Wait(5000));
            Assert.AreEqual(2, calls);
        }

        [Test]
        public void ChangedInputsCancelPreviousSnapshotWithoutOverlappingProbes()
        {
            var cache = NewCache();
            using var started = new ManualResetEventSlim();
            int active = 0;
            int overlaps = 0;
            var old = Request(
                cache,
                "old",
                token =>
                {
                    Interlocked.Increment(ref active);
                    started.Set();
                    try
                    {
                        Assert.IsTrue(token.WaitHandle.WaitOne(5000));
                        token.ThrowIfCancellationRequested();
                        return new DependencyCheckResult();
                    }
                    finally
                    {
                        Interlocked.Decrement(ref active);
                    }
                }
            );
            Assert.IsTrue(started.Wait(2000));
            var current = Request(
                cache,
                "changed",
                token =>
                {
                    if (Interlocked.Increment(ref active) != 1)
                        Interlocked.Increment(ref overlaps);
                    Interlocked.Decrement(ref active);
                    return new DependencyCheckResult { Summary = "current" };
                }
            );
            Assert.IsTrue(current.Wait(5000));
            Assert.AreEqual("current", current.Result.Summary);
            Assert.IsTrue(old.IsCanceled);
            Assert.AreEqual(0, overlaps);
        }

        [Test]
        public void ClosingSetupWindowIgnoresLateDependencyResult()
        {
            var field = typeof(MCPSetupWindow).GetField("dependencyCheck", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "Setup must request asynchronous snapshots");
            var original = field.GetValue(null);
            var completion = new TaskCompletionSource<DependencyCheckResult>();
            MCPSetupWindow window = null;
            try
            {
                field.SetValue(null, new Func<bool, Task<DependencyCheckResult>>(_ => completion.Task));
                window = ScriptableObject.CreateInstance<MCPSetupWindow>();
                Invoke(window, "BeginDependencyCheck", false);
                Invoke(window, "OnDisable");
                completion.SetResult(new DependencyCheckResult { Summary = "stale" });
                Invoke(window, "PollDependencies");
                Assert.IsNull(typeof(MCPSetupWindow).GetField("_dependencyResult", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(window));
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                field.SetValue(null, original);
            }
        }

        [Test]
        public void ExecutableIdentityChangeInvalidatesCompletedSnapshot()
        {
            var type = NewCache().GetType();
            string identity = "before";
            var cache = Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[] { new Func<DependencyCheckResult, string>(_ => identity) },
                null
            );
            int calls = 0;
            Func<CancellationToken, DependencyCheckResult> probe = _ =>
            {
                calls++;
                return new DependencyCheckResult();
            };
            var first = Request(cache, "path", probe);
            Assert.IsTrue(first.Wait(5000));
            Assert.AreSame(first, Request(cache, "path", probe));
            identity = "after";
            var second = Request(cache, "path", probe);
            Assert.AreNotSame(first, second);
            Assert.IsTrue(second.Wait(5000));
            Assert.AreEqual(2, calls);
        }

        [Test]
        public void DetectorCapturesEditorOnlyPathsBeforeRunningUvOnWorker()
        {
            var slot = typeof(MCPForUnity.Editor.Services.MCPServiceLocator).GetField("_pathService", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(slot);
            var original = slot.GetValue(null);
            var paths = new ThreadCheckedPaths();
            try
            {
                slot.SetValue(null, paths);
                var method = typeof(MCPForUnity.Editor.Dependencies.PlatformDetectors.PlatformDetectorBase).GetMethod(
                    "CaptureForBackground",
                    BindingFlags.Instance | BindingFlags.NonPublic
                );
                var detector = (MCPForUnity.Editor.Dependencies.PlatformDetectors.PlatformDetectorBase)method.Invoke(new ProbeDetector(), null);
                var task = Task.Run(() => detector.DetectUv());
                Assert.IsTrue(task.Wait(5000));
                Assert.IsTrue(task.Result.IsAvailable, task.Result.ErrorMessage);
                Assert.AreEqual(1, paths.Probes);
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        private sealed class ProbeDetector : MCPForUnity.Editor.Dependencies.PlatformDetectors.PlatformDetectorBase
        {
            protected override bool TryFindInPath(string command, out string path)
            {
                path = null;
                return false;
            }

            public override string PlatformName => "memory";
            public override bool CanDetect => true;

            public override DependencyStatus DetectPython() => throw new InvalidOperationException();

            public override string GetPythonInstallUrl() => "";

            public override string GetUvInstallUrl() => "";

            public override string GetInstallationRecommendations() => "";
        }

        private sealed class ThreadCheckedPaths : MCPForUnity.Editor.Services.IPathResolverService
        {
            private readonly int editorThread = Thread.CurrentThread.ManagedThreadId;
            public int Probes;

            private void EditorOnly()
            {
                Assert.AreEqual(editorThread, Thread.CurrentThread.ManagedThreadId);
            }

            public string GetUvxPath()
            {
                EditorOnly();
                return "owned-uvx";
            }

            public bool HasUvxPathOverride
            {
                get
                {
                    EditorOnly();
                    return true;
                }
            }
            public bool HasUvxPathFallback
            {
                get
                {
                    EditorOnly();
                    return false;
                }
            }

            public Task<string> ValidateUvxExecutableAsync(string path)
            {
                Assert.AreNotEqual(editorThread, Thread.CurrentThread.ManagedThreadId);
                Interlocked.Increment(ref Probes);
                return Task.FromResult("0.9.18");
            }

            public bool TryValidateUvxExecutable(string path, out string version) => throw new InvalidOperationException();

            public string GetClaudeCliPath() => throw new InvalidOperationException();

            public bool HasClaudeCliPathOverride => false;

            public bool IsPythonDetected() => false;

            public bool IsClaudeCliDetected() => false;

            public void SetUvxPathOverride(string path) => throw new InvalidOperationException();

            public void SetClaudeCliPathOverride(string path) => throw new InvalidOperationException();

            public void ClearUvxPathOverride() => throw new InvalidOperationException();

            public void ClearClaudeCliPathOverride() => throw new InvalidOperationException();
        }

        private static void Invoke(object target, string method, params object[] args) =>
            target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, args);
    }
}
