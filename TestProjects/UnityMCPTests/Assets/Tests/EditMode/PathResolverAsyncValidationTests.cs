using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor
{
    public class PathResolverAsyncValidationTests
    {
        [Test]
        public async Task ConcurrentRequestsShareProbeAndDoNotBlockCallingThread()
        {
            int calls = 0;
            int callingThread = Thread.CurrentThread.ManagedThreadId;
            int probeThread = callingThread;
            using var release = new ManualResetEventSlim();
            var resolver = new PathResolverService(path =>
            {
                Interlocked.Increment(ref calls);
                probeThread = Thread.CurrentThread.ManagedThreadId;
                release.Wait(5000);
                return "0.9.18";
            });
            Task<string> first = resolver.ValidateUvxExecutableAsync("synthetic-uvx");
            try
            {
                Assert.IsFalse(first.IsCompleted, "The blocked probe must not block its caller");
                Assert.AreSame(first, resolver.ValidateUvxExecutableAsync("synthetic-uvx"));
            }
            finally
            {
                release.Set();
            }
            Assert.AreEqual("0.9.18", await first);
            Assert.AreEqual(1, calls);
            Assert.AreNotEqual(callingThread, probeThread);
            Assert.AreSame(first, resolver.ValidateUvxExecutableAsync("synthetic-uvx"));
        }

        [Test]
        public async Task PathsHaveIndependentResultsAndFailuresAreCached()
        {
            int calls = 0;
            var resolver = new PathResolverService(path =>
            {
                Interlocked.Increment(ref calls);
                return path == "valid" ? "0.9.18" : null;
            });
            Assert.IsNull(await resolver.ValidateUvxExecutableAsync("invalid"));
            Assert.AreEqual("0.9.18", await resolver.ValidateUvxExecutableAsync("valid"));
            Assert.IsNull(await resolver.ValidateUvxExecutableAsync("invalid"));
            Assert.AreEqual(2, calls);
        }

        [Test]
        public void ResolvingAnExistingOverrideDoesNotRunTheProbe()
        {
            string path = Path.Combine(Path.GetTempPath(), "synthetic-uvx-" + Guid.NewGuid().ToString("N"));
            bool hadOverride = EditorPrefs.HasKey(EditorPrefKeys.UvxPathOverride);
            string savedOverride = EditorPrefs.GetString(EditorPrefKeys.UvxPathOverride, string.Empty);
            File.WriteAllText(path, "synthetic executable — never executed");
            try
            {
                int calls = 0;
                var resolver = new PathResolverService(candidate =>
                {
                    calls++;
                    return "0.9.18";
                });
                resolver.SetUvxPathOverride(path);
                Assert.AreEqual(path, resolver.GetUvxPath());
                Assert.AreEqual(path, resolver.GetUvxPath());
                Assert.AreEqual(0, calls, "Focus/path resolution must not start a version process");
            }
            finally
            {
                if (hadOverride)
                    EditorPrefs.SetString(EditorPrefKeys.UvxPathOverride, savedOverride);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.UvxPathOverride);
                File.Delete(path);
            }
        }

        [Test]
        public async Task SlowPreviousPathDoesNotReplaceTheNewPathResult()
        {
            using var release = new ManualResetEventSlim();
            var resolver = new PathResolverService(path =>
            {
                if (path == "old-path")
                {
                    release.Wait(5000);
                    return null;
                }
                return "0.9.18";
            });
            Task<string> old = resolver.ValidateUvxExecutableAsync("old-path");
            try
            {
                Assert.AreEqual("0.9.18", await resolver.ValidateUvxExecutableAsync("new-path"));
                Assert.IsFalse(old.IsCompleted);
            }
            finally
            {
                release.Set();
            }
            Assert.IsNull(await old);
            Assert.AreEqual("0.9.18", await resolver.ValidateUvxExecutableAsync("new-path"));
        }
    }
}
