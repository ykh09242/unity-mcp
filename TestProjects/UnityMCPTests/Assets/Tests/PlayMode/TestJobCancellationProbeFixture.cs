using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Text;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayMode
{
    /// <summary>Opt-in cooperative workload for an outside-runner disposable-Editor coordinator.</summary>
    public sealed class TestJobCancellationProbeFixture
    {
        private static double deadline = double.NaN;
        private static int frames;

        [UnityTest, Explicit("Only the separately approved PLAY03 executeMethod coordinator may select this test."), Order(0)]
        public IEnumerator CooperatesUntilCancelled()
        {
            MarkStart();
            try
            {
                Mark("inside-try.txt");
                // No iterator locals survive across this yield. Helpers recover their owned
                // deadline/path from durable data after a real managed domain reload.
                while (StillWithinOwnedDeadline())
                {
                    if (!File.Exists(OwnedPath("yield-ready.txt")))
                        Mark("yield-ready.txt");
                    yield return null;
                }
                Assert.Fail("The cooperative fixture reached its own bound without native cancellation.");
            }
            finally
            {
                Mark("teardown.txt");
            }
        }

        private static bool IsCooperatorContext() =>
            string.Equals(
                NUnit.Framework.TestContext.CurrentContext.Test.FullName,
                typeof(TestJobCancellationProbeFixture).FullName + "." + nameof(CooperatesUntilCancelled),
                StringComparison.Ordinal
            );

        [TearDown]
        public void ObserveNUnitTearDown()
        {
            if (IsCooperatorContext())
                Mark("nunit-teardown.txt");
        }

        [UnityTearDown]
        public IEnumerator ObserveUnityTearDown()
        {
            if (IsCooperatorContext())
                Mark("unity-teardown.txt");
            yield break;
        }

        [Test, Explicit("Cancellation must leave this selected sentinel NotRun."), Order(1)]
        public void SentinelMustRemainNotRun()
        {
            Mark("sentinel.txt");
            Assert.Fail("Native cancellation did not stop the remaining selected sentinel.");
        }

        [Test, Explicit("Only a confirmed-idle replacement run may select this test.")]
        public void ReplacementCompletes()
        {
            Mark("replacement.txt");
        }

        private static string OwnedPath(string name)
        {
#if UNITY_EDITOR
            string requested = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_PROJECT");
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_NONCE");
            Assert.IsTrue(Guid.TryParseExact(nonce, "N", out _), "The explicit QA nonce is required.");
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar);
            Assert.IsFalse(string.IsNullOrWhiteSpace(requested));
            Assert.IsTrue(string.Equals(project, Path.GetFullPath(requested).TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase));
            string directory = Path.Combine(project, "Library", "Play03Qa", nonce);
            Assert.IsTrue(Directory.Exists(directory), "The reviewed coordinator must own the evidence directory before Execute.");
            string path = Path.Combine(directory, name);
            for (string current = path; !string.Equals(current, project, StringComparison.OrdinalIgnoreCase); current = Path.GetDirectoryName(current))
                if (File.Exists(current) || Directory.Exists(current))
                    Assert.AreEqual(0, (int)(File.GetAttributes(current) & FileAttributes.ReparsePoint), "Linked QA paths are refused.");
            Assert.AreEqual(0, (int)(File.GetAttributes(project) & FileAttributes.ReparsePoint));
            return path;
#else
            Assert.Ignore("This explicit fixture is supported only in its owned disposable Editor.");
            return null;
#endif
        }

        private static double Uptime()
        {
#if UNITY_EDITOR
            return UnityEditor.EditorApplication.timeSinceStartup;
#else
            return 0;
#endif
        }

        private static void MarkStart()
        {
            string path = OwnedPath("started.txt");
            Assert.IsFalse(File.Exists(path), "An executing job must not restart its fixture after reload.");
            deadline = Uptime() + 120;
            File.WriteAllText(path, deadline.ToString("R", CultureInfo.InvariantCulture), new UTF8Encoding(false));
            frames = 0;
        }

        private static bool StillWithinOwnedDeadline()
        {
            if (double.IsNaN(deadline))
            {
                using var input = new FileStream(OwnedPath("started.txt"), FileMode.Open, FileAccess.Read, FileShare.Read);
                byte[] bytes = new byte[65];
                int count = 0;
                while (count < bytes.Length)
                {
                    int read = input.Read(bytes, count, bytes.Length - count);
                    if (read == 0)
                        break;
                    count += read;
                }
                Assert.LessOrEqual(count, 64);
                string saved = new UTF8Encoding(false, true).GetString(bytes, 0, count);
                Assert.IsTrue(double.TryParse(saved, NumberStyles.Float, CultureInfo.InvariantCulture, out deadline));
                Assert.IsFalse(double.IsNaN(deadline) || double.IsInfinity(deadline));
            }
            return ++frames <= 60000 && Uptime() < deadline;
        }

        private static void Mark(string name)
        {
            string path = OwnedPath(name);
            Assert.IsFalse(File.Exists(path), "An owned lifecycle witness must be emitted once.");
            File.WriteAllText(path, "observed", new UTF8Encoding(false));
        }
    }
}
