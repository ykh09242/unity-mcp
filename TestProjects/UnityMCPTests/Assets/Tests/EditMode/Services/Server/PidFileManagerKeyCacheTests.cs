using System;
using System.IO;
using MCPForUnity.Editor.Services.Server;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services.Server
{
    public class PidFileManagerKeyCacheTests
    {
        [Test]
        public void PreferenceKey_PreservesNormalizedProjectHash()
        {
            string project = Path.Combine(Path.GetTempPath(), "pid-key-fixture", "child", "..");
            var manager = new PidFileManager(project);
            string normalized = Path.GetFullPath(project).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string identity = Application.platform == RuntimePlatform.WindowsEditor ? normalized.ToUpperInvariant() : normalized;
            Assert.That(manager.PreferenceKey("fixture"), Is.EqualTo("fixture.Project." + manager.ComputeShortHash(identity)));
            Assert.That(new PidFileManager(normalized).PreferenceKey("fixture"), Is.EqualTo(manager.PreferenceKey("fixture")));
            Assert.That(new PidFileManager(normalized + "-other").PreferenceKey("fixture"), Is.Not.EqualTo(manager.PreferenceKey("fixture")));
        }

        [Test]
        public void PreferenceKey_ReusesProjectFingerprintAcrossLookups()
        {
            var getAllocated = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", Type.EmptyTypes);
            if (getAllocated == null)
                Assert.Ignore("This managed runtime does not expose per-thread allocation counters.");
            var allocated = (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), getAllocated);
            var manager = new PidFileManager(Path.Combine(Path.GetTempPath(), "pid-key-allocation-fixture"));
            string expected = manager.PreferenceKey("fixture");
            for (int i = 0; i < 100; i++)
                manager.PreferenceKey("fixture");
            long before = allocated();
            string last = null;
            for (int i = 0; i < 1000; i++)
                last = manager.PreferenceKey("fixture");
            long bytes = allocated() - before;
            Assert.That(last, Is.EqualTo(expected));
            // A short concatenated key fits comfortably here; per-lookup SHA256 and UTF8 buffers do not.
            Assert.That(bytes, Is.LessThan(256000), "Repeated keys should allocate only their final strings, not cryptographic state and hash buffers.");
        }
    }
}
