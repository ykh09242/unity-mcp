using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Dependencies.PlatformDetectors;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    internal sealed class SyntheticClientPaths : IPathResolverService
    {
        internal string UvxPath;
        internal string ClaudePath;
        internal Action OnResolveUvx;

        public string GetUvxPath()
        {
            OnResolveUvx?.Invoke();
            return UvxPath;
        }

        public string GetClaudeCliPath() => ClaudePath;

        public bool IsPythonDetected() => true;

        public bool IsClaudeCliDetected() => true;

        public bool HasUvxPathOverride => true;
        public bool HasClaudeCliPathOverride => true;
        public bool HasUvxPathFallback => false;

        public void SetUvxPathOverride(string path) => throw new NotSupportedException();

        public void SetClaudeCliPathOverride(string path) => throw new NotSupportedException();

        public void ClearUvxPathOverride() => throw new NotSupportedException();

        public void ClearClaudeCliPathOverride() => throw new NotSupportedException();

        public bool TryValidateUvxExecutable(string path, out string version) => throw new NotSupportedException();

        public Task<string> ValidateUvxExecutableAsync(string path) => throw new NotSupportedException();
    }

    /// <summary>
    /// Covers the Windows interpreter-discovery path that pyenv-win exposed: batch shims have to be
    /// launchable, and a single executable name has to be resolvable to more than one PATH hit so a
    /// dead Microsoft Store alias cannot mask a working interpreter behind it.
    /// </summary>
    public class ExecPathBatchShimTests
    {
        private string _tempRoot;

        [DllImport("kernel32.dll", EntryPoint = "GetLongPathNameW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern uint GetLongPathName(string path, StringBuilder longPath, uint capacity);

        [SetUp]
        public void SetUp()
        {
            _tempRoot = Path.Combine(Path.GetTempPath(), "mcp_execpath_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempRoot);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (Directory.Exists(_tempRoot))
                    Directory.Delete(_tempRoot, true);
            }
            catch { }
        }

        private static void RequireWindows()
        {
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                Assert.Ignore("Batch shim launching is Windows-only (CI runs linux).");
            }
        }

        private string WriteShim(string directory, string fileName, string body)
        {
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, fileName);
            File.WriteAllText(path, "@echo off" + Environment.NewLine + body + Environment.NewLine);
            return path;
        }

        [Test]
        public void TryRun_BatchShim_RunsAndForwardsArguments()
        {
            RequireWindows();

            // pyenv-win publishes interpreters as .bat shims; CreateProcess cannot start those
            // directly while UseShellExecute is false, so TryRun has to route them via cmd.exe.
            string shim = WriteShim(_tempRoot, "fake_python.bat", "echo Python 3.12.9 %*");

            bool ok = ExecPath.TryRun(shim, "--version", null, out string stdout, out string stderr, 10000);

            Assert.IsTrue(ok, $"Batch shim should run. stderr: {stderr}");
            Assert.That(stdout, Does.Contain("Python 3.12.9"));
            Assert.That(stdout, Does.Contain("--version"), "Arguments must reach the shim, not be swallowed by cmd.exe quoting.");
        }

        [Test]
        public void TryRun_BatchShimInPathWithSpaces_RunsAndForwardsArguments()
        {
            RequireWindows();

            string spaced = Path.Combine(_tempRoot, "Program Files Like");
            string shim = WriteShim(spaced, "fake_python.cmd", "echo Python 3.12.9 %*");

            bool ok = ExecPath.TryRun(shim, "--version", null, out string stdout, out string stderr, 10000);

            Assert.IsTrue(ok, $"Batch shim under a path containing spaces should run. stderr: {stderr}");
            Assert.That(stdout, Does.Contain("Python 3.12.9"));
            Assert.That(stdout, Does.Contain("--version"));
        }

        [Test]
        public void TryRun_BatchExecutablePercentSegmentsRemainLiteral()
        {
            RequireWindows();
            string shim = WriteShim(
                Path.Combine(_tempRoot, "Program %MCP_SYNTHETIC_SHIM_SEGMENT% ! & (Files) ^"),
                "synthetic.cmd",
                "echo literal executable reached"
            );
            Assert.IsTrue(
                ExecPath.TryRun(
                    shim,
                    "",
                    _tempRoot,
                    out string stdout,
                    out string stderr,
                    10000,
                    environmentOverrides: new Dictionary<string, string> { ["MCP_SYNTHETIC_SHIM_SEGMENT"] = "expanded-incorrect" }
                ),
                stderr
            );
            Assert.AreEqual("literal executable reached", stdout.Trim());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TryRun_DistinguishesFinishedFailureFromUncertainTimeout(bool timeout)
        {
            RequireWindows();
            // A cmd builtin loop has no child process to leave behind.
            string shim = WriteShim(_tempRoot, "failure.cmd", timeout ? ":wait\r\ngoto wait" : "exit /b 1");
            bool ok = ExecPath.TryRun(shim, "", _tempRoot, out _, out _, out bool processMayBeRunning, timeout ? 100 : 10000);
            Assert.IsFalse(ok);
            Assert.AreEqual(timeout, processMayBeRunning);
        }

        [Test]
        public void FindAllInPath_ReturnsEveryMatchInPathOrder()
        {
            RequireWindows();

            // TEMP can use an 8.3 alias, while where.exe returns long path names.
            // Canonicalize the owned fixture, keeping exact match/order assertions below.
            var canonicalRoot = new StringBuilder(32768);
            uint length = GetLongPathName(_tempRoot, canonicalRoot, (uint)canonicalRoot.Capacity);
            Assert.That(length, Is.InRange(1U, (uint)canonicalRoot.Capacity - 1), "Cannot resolve the owned temporary directory's long path.");

            // The real-world shape: a non-working entry earlier on PATH shadowing a working one.
            string first = Path.Combine(canonicalRoot.ToString(), "first");
            string second = Path.Combine(canonicalRoot.ToString(), "second");
            string name = "mcp_probe_" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".cmd";
            string firstShim = WriteShim(first, name, "exit /b 1");
            string secondShim = WriteShim(second, name, "echo Python 3.12.9");

            string prepend = first + Path.PathSeparator + second;
            string[] matches = ExecPath.FindAllInPath(name, prepend);

            Assert.AreEqual(2, matches.Length, "Both PATH entries should be reported: " + string.Join(", ", matches));
            Assert.AreEqual(firstShim, matches[0]);
            Assert.AreEqual(secondShim, matches[1]);

            // FindInPath keeps its single-result contract, returning the first match only.
            Assert.AreEqual(firstShim, ExecPath.FindInPath(name, prepend));
        }

        [Test]
        public void TryRun_ExtraPathPrepend_IsVisibleToChildProcess()
        {
            RequireWindows();

            // Mono backs ProcessStartInfo.EnvironmentVariables with a case-sensitive dictionary while
            // Windows spells the inherited key "Path". Writing "PATH" therefore used to create a
            // second entry the child never read, making extraPathPrepend a silent no-op.
            string dir = Path.Combine(_tempRoot, "prepended");
            WriteShim(dir, "mcp_onpath.cmd", "echo shim reached");

            bool ok = ExecPath.TryRun("mcp_onpath.cmd", string.Empty, null, out string stdout, out string stderr, 10000, dir);

            Assert.IsTrue(ok, $"Prepended PATH entry should be resolvable by the child process. stderr: {stderr}");
            Assert.That(stdout, Does.Contain("shim reached"));
        }

        [Test]
        public void FindAllInPath_UnknownExecutable_ReturnsEmpty()
        {
            RequireWindows();

            string[] matches = ExecPath.FindAllInPath("mcp_definitely_missing_" + Guid.NewGuid().ToString("N") + ".exe");

            Assert.IsNotNull(matches);
            Assert.IsEmpty(matches);
        }

        [TestCase("python.exe", true)]
        [TestCase("python3.exe", true)]
        [TestCase("python.bat", true)]
        [TestCase("python3.bat", true)]
        [TestCase("python3.12.bat", true)]
        [TestCase("python.cmd", true)]
        [TestCase("PYTHON.EXE", true)]
        [TestCase("pythonw.exe", false)]
        [TestCase("pythonw3.12.bat", false)]
        [TestCase("python", false)]
        [TestCase("python.dll", false)]
        [TestCase("uv.exe", false)]
        public void IsPythonExecutable_ClassifiesUvListedPaths(string fileName, bool expected)
        {
            // Runs on every platform: this is pure path classification of `uv python list` output,
            // which used to accept only .exe and therefore hid pyenv-win's .bat shims.
            string path = Path.Combine("C:", "some", "dir", fileName);

            Assert.AreEqual(expected, WindowsPlatformDetector.IsPythonExecutable(path), fileName);
        }
    }
}
