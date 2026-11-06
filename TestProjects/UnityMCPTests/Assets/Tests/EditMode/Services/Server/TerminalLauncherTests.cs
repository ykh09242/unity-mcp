using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Services.Server;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services.Server
{
    /// <summary>
    /// Unit tests for TerminalLauncher component.
    /// Note: Tests avoid actually launching terminals to prevent test instability.
    /// </summary>
    [TestFixture]
    public class TerminalLauncherTests
    {
        private TerminalLauncher _launcher;

        [SetUp]
        public void SetUp()
        {
            _launcher = new TerminalLauncher();
        }

        #region GetProjectRootPath Tests

        [Test]
        public void GetProjectRootPath_ReturnsNonEmpty()
        {
            // Act
            string path = _launcher.GetProjectRootPath();

            // Assert
            Assert.IsNotNull(path);
            Assert.IsNotEmpty(path);
        }

        [Test]
        public void GetProjectRootPath_ReturnsValidDirectory()
        {
            // Act
            string path = _launcher.GetProjectRootPath();

            // Assert
            Assert.IsTrue(System.IO.Directory.Exists(path), $"Project root path should exist: {path}");
        }

        [Test]
        public void GetProjectRootPath_DoesNotContainAssets()
        {
            // Act
            string path = _launcher.GetProjectRootPath();

            // Assert
            Assert.IsFalse(path.EndsWith("Assets"), "Project root should not end with Assets");
        }

        #endregion

        #region CreateTerminalProcessStartInfo Tests

        [Test]
        public void CreateTerminalProcessStartInfo_EmptyCommand_ThrowsArgumentException()
        {
            // Act & Assert
            Assert.Throws<ArgumentException>(() =>
            {
                _launcher.CreateTerminalProcessStartInfo(string.Empty);
            });
        }

        [Test]
        public void CreateTerminalProcessStartInfo_NullCommand_ThrowsArgumentException()
        {
            // Act & Assert
            Assert.Throws<ArgumentException>(() =>
            {
                _launcher.CreateTerminalProcessStartInfo(null);
            });
        }

        [Test]
        public void CreateTerminalProcessStartInfo_WhitespaceCommand_ThrowsArgumentException()
        {
            // Act & Assert
            Assert.Throws<ArgumentException>(() =>
            {
                _launcher.CreateTerminalProcessStartInfo("   ");
            });
        }

        [Test]
        public void CreateTerminalProcessStartInfo_ValidCommand_ReturnsStartInfo()
        {
            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo("echo hello");

            // Assert
            Assert.IsNotNull(startInfo);
            Assert.IsNotNull(startInfo.FileName);
            Assert.IsNotEmpty(startInfo.FileName);
        }

        [Test]
        public void CreateTerminalProcessStartInfo_ValidCommand_SetsUseShellExecuteFalse()
        {
            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo("echo hello");

            // Assert
            Assert.IsFalse(startInfo.UseShellExecute, "UseShellExecute should be false");
        }

        [Test]
        public void CreateTerminalProcessStartInfo_ValidCommand_SetsCreateNoWindowTrue()
        {
            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo("echo hello");

            // Assert
            Assert.IsTrue(startInfo.CreateNoWindow, "CreateNoWindow should be true");
        }

        [Test]
        public void CreateTerminalProcessStartInfo_CommandWithNewlines_StripsNewlines()
        {
            // Act - Should not throw
            var startInfo = _launcher.CreateTerminalProcessStartInfo("echo\nhello\r\nworld");

            // Assert
            Assert.IsNotNull(startInfo);
        }

        [Test]
        public void CreateTerminalProcessStartInfo_LongCommand_HandlesGracefully()
        {
            // Arrange
            string longCommand = new string('a', 1000);

            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo(longCommand);

            // Assert
            Assert.IsNotNull(startInfo);
        }

        [Test]
        public void CreateTerminalProcessStartInfo_SpecialCharacters_HandlesGracefully()
        {
            // Arrange
            string command = "echo \"hello world\" && echo 'test' | cat";

            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo(command);

            // Assert
            Assert.IsNotNull(startInfo);
        }

        #endregion

#if UNITY_EDITOR_WIN
        [Test]
        public void CreateTerminalProcessStartInfo_UnicodeCommand_WritesUtf8WithoutBomAfterCodePageSwitch()
        {
            string path = System.IO.Path.Combine(_launcher.GetProjectRootPath(), "Library", "MCPForUnity", "TerminalScripts", "mcp-terminal.cmd");
            byte[] original = System.IO.File.Exists(path) ? System.IO.File.ReadAllBytes(path) : null;
            try
            {
                const string command = "echo \uD55C\uAE00 \u30C6\u30B9\u30C8 \u00E9";
                _launcher.CreateTerminalProcessStartInfo(command);
                byte[] bytes = System.IO.File.ReadAllBytes(path);
                Assert.IsFalse(bytes.Length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf);
                string script = new System.Text.UTF8Encoding(false, true).GetString(bytes);
                StringAssert.StartsWith("@echo off\r\nchcp 65001 >nul\r\n", script);
                StringAssert.EndsWith(command + "\r\n", script);
            }
            finally
            {
                if (original != null)
                    System.IO.File.WriteAllBytes(path, original);
                else if (System.IO.File.Exists(path))
                    System.IO.File.Delete(path);
            }
        }
#endif

        #region CreateHeadlessProcessStartInfo Tests

        [Test]
        public void CreateHeadlessProcessStartInfo_EmptyCommand_ThrowsArgumentException()
        {
            Assert.Throws<ArgumentException>(() => _launcher.CreateHeadlessProcessStartInfo(string.Empty, "/tmp/log.txt"));
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_EmptyLogPath_ThrowsArgumentException()
        {
            Assert.Throws<ArgumentException>(() => _launcher.CreateHeadlessProcessStartInfo("echo hello", string.Empty));
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_IsHiddenNoWindow()
        {
            var startInfo = _launcher.CreateHeadlessProcessStartInfo("echo hello", LogPath());

            Assert.IsFalse(startInfo.UseShellExecute, "UseShellExecute should be false for headless launch");
            Assert.IsTrue(startInfo.CreateNoWindow, "CreateNoWindow should be true for headless launch");
            Assert.AreEqual(System.Diagnostics.ProcessWindowStyle.Hidden, startInfo.WindowStyle, "WindowStyle should be Hidden for headless launch");
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_DoesNotOpenTerminal()
        {
            var startInfo = _launcher.CreateHeadlessProcessStartInfo("echo hello", LogPath());

            // Must NOT route through Terminal.app / start / a terminal emulator.
#if UNITY_EDITOR_WIN
            Assert.AreEqual("cmd.exe", startInfo.FileName, "Windows headless should run via cmd.exe");
            StringAssert.DoesNotContain("start ", startInfo.Arguments, "Windows headless must not use 'start' (new window)");
#else
            Assert.AreEqual("/bin/bash", startInfo.FileName, "macOS/Linux headless should run via /bin/bash");
            StringAssert.DoesNotContain("open", startInfo.Arguments, "macOS headless must not use 'open -a Terminal'");
            StringAssert.DoesNotContain("Terminal", startInfo.Arguments, "macOS headless must not open Terminal.app");
#endif
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_RedirectsOutputToLogFile()
        {
            string logPath = LogPath();

            var startInfo = _launcher.CreateHeadlessProcessStartInfo("echo hello", logPath);
            Assert.AreEqual(logPath, startInfo.EnvironmentVariables["UNITY_MCP_LAUNCH_LOG"]);

            // The redirect to the log file is part of the shell payload.
            StringAssert.Contains(logPath, startInfo.Arguments, "Arguments should reference the log file path");
            StringAssert.Contains("2>&1", startInfo.Arguments, "stderr should be redirected to stdout (and the log)");
            StringAssert.Contains(">>", startInfo.Arguments, "output should be appended to the log via >>");
        }

#if UNITY_EDITOR_WIN
        [Test]
        public void CreateHeadlessProcessStartInfo_RedirectsStdinFromNul()
        {
            // Regression guard for #1279: the Editor is a console-less GUI process, so a child
            // launched with CreateNoWindow inherits an invalid stdin and uvx.exe fails with
            // "The handle is invalid. (os error 6)". stdin must come from NUL instead.
            var startInfo = _launcher.CreateHeadlessProcessStartInfo("uvx run-server", LogPath());

            StringAssert.Contains("< NUL", startInfo.Arguments, "stdin should be redirected from NUL");
        }
#endif

        [Test]
        public void CreateHeadlessProcessStartInfo_LogPathWithSpaces_IsQuoted()
        {
            // A log path containing spaces must remain a single token.
            string logPath = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "Mcp Logs", "server launch.log");

            var startInfo = _launcher.CreateHeadlessProcessStartInfo("uvx run-server", logPath);

            StringAssert.Contains(logPath, startInfo.Arguments, "Arguments should contain the full spaced log path");
#if UNITY_EDITOR_WIN
            StringAssert.Contains($"\"{logPath}\"", startInfo.Arguments, "Windows should double-quote a spaced log path");
#else
            StringAssert.Contains($"'{logPath}'", startInfo.Arguments, "macOS/Linux should single-quote a spaced log path");
#endif
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_CommandWithSpaces_Preserved()
        {
            string command = "/path with spaces/uvx --no-cache run mcp-for-unity";

            var startInfo = _launcher.CreateHeadlessProcessStartInfo(command, LogPath());

            StringAssert.Contains(command, startInfo.Arguments, "The command (incl. spaces) should be preserved in Arguments");
        }

        [Test]
        public void CreateHeadlessProcessStartInfo_StripsNewlines()
        {
            var startInfo = _launcher.CreateHeadlessProcessStartInfo("echo\nhello\r\nworld", LogPath());

            Assert.IsNotNull(startInfo);
            StringAssert.DoesNotContain("\n", startInfo.Arguments);
            StringAssert.DoesNotContain("\r", startInfo.Arguments);
        }

        private static string LogPath()
        {
            return System.IO.Path.Combine(System.IO.Path.GetTempPath(), "mcp-headless-test.log");
        }

        #endregion

        [Test]
        public void LiteralHeadlessLaunch_RecordsExactArgumentsAndOwnsLogUntilExit()
        {
            string python = Environment.GetEnvironmentVariable("MCP_UNITY_TEST_PYTHON");
            if (string.IsNullOrEmpty(python) || !File.Exists(python))
                Assert.Ignore("Set MCP_UNITY_TEST_PYTHON to an existing Python executable for the harmless argv recorder.");

            string root = Path.Combine(_launcher.GetProjectRootPath(), "Library", "MCPForUnity", "Tests", "argv-" + Guid.NewGuid().ToString("N"));
            string special = Path.Combine(root, "R&D %MCP_R3_EXPAND% $cash's!^ (space) 한글");
            Directory.CreateDirectory(special);
            string script = Path.Combine(special, "record.py");
            string log = Path.Combine(special, "launch.log");
            string record = Path.Combine(special, "argv.json");
            string source = Path.Combine(special, "source");
#if !UNITY_EDITOR_WIN
            source += "\"quoted";
#endif
            string[] expected =
            {
                "--from",
                source,
                "--pidfile",
                Path.Combine(special, "server.pid"),
                "--unity-instance-token",
                "synthetic-owned-launch",
                "",
                "quoted\"&echo should-stay-data",
                "slash\\\"quote",
                "trailing\\",
                "line\nnext",
                "$HOME `echo literal` %MCP_R3_EXPAND% ! ^ | < >",
            };
            File.WriteAllText(
                script,
                "import json, os, sys\n"
                    + "with open(os.environ['MCP_R3_RECORD'], 'w', encoding='utf-8') as f: json.dump(sys.argv[1:], f, ensure_ascii=False)\n"
                    + "assert sys.stdin.read() == ''\n"
                    + "print('recorder stdout', flush=True)\n"
                    + "print('recorder stderr', file=sys.stderr, flush=True)\n"
                    + "sys.exit(7)\n",
                new UTF8Encoding(false)
            );
            Process child = null;
            try
            {
                var arguments = new List<string> { "-I", script };
                arguments.AddRange(expected);
                var info = _launcher.CreateHeadlessProcessStartInfo(python, arguments, log);
                info.EnvironmentVariables["MCP_R3_RECORD"] = record;
                info.EnvironmentVariables["MCP_R3_EXPAND"] = "must-not-expand";
                Assert.IsFalse(info.RedirectStandardOutput, "Log handles must survive an Editor domain reload.");
                Assert.IsFalse(info.RedirectStandardError);
                Assert.IsFalse(info.UseShellExecute);
                Assert.IsTrue(info.CreateNoWindow);
                child = Process.Start(info);
                Assert.IsNotNull(child);
                Assert.IsTrue(child.WaitForExit(10000), "The harmless recorder must exit within the watchdog.");
                Assert.AreEqual(7, child.ExitCode);
                CollectionAssert.AreEqual(expected, JArray.Parse(File.ReadAllText(record)).Select(value => (string)value).ToArray());
                string captured = File.ReadAllText(log);
                StringAssert.Contains("recorder stdout", captured);
                StringAssert.Contains("recorder stderr", captured);
                using (File.Open(log, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            }
            finally
            {
                if (child != null)
                {
                    if (!child.HasExited)
                    {
                        child.Kill();
                        child.WaitForExit(5000);
                    }
                    child.Dispose();
                }
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void LiteralHeadlessLaunch_RejectsNulWithoutTruncatingArguments()
        {
            Assert.Throws<ArgumentException>(() => _launcher.CreateHeadlessProcessStartInfo("uvx", new[] { "before\0after" }, LogPath()));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void LiteralHeadlessLaunch_PlatformAdapterKeepsValuesOutOfShellSource(bool windows)
        {
            const string executable = "fixture-executable &'$%!";
            const string argument = "fixture-argument \'\" & %NAME% ! ^ $() `literal` \\";
            string log = LogPath();
            var info = TerminalLauncher.CreateLiteralHeadlessProcessStartInfo(executable, new[] { argument, "" }, log, windows);
            Assert.AreEqual(log, info.EnvironmentVariables["UNITY_MCP_LAUNCH_LOG"]);
            StringAssert.DoesNotContain(executable, info.Arguments);
            StringAssert.DoesNotContain(argument, info.Arguments);
            StringAssert.DoesNotContain(log, info.Arguments);
            Assert.AreEqual(windows ? TerminalLauncher.QuoteWindowsArgument(executable) : executable, info.EnvironmentVariables["MCP_UNITY_EXECUTABLE"]);
            Assert.AreEqual(windows ? TerminalLauncher.QuoteWindowsArgument(argument) : argument, info.EnvironmentVariables["MCP_UNITY_ARGUMENT_0"]);
            Assert.AreEqual(windows ? "\"\"" : "", info.EnvironmentVariables["MCP_UNITY_ARGUMENT_1"]);
            if (windows)
                StringAssert.StartsWith("/d /v:on /s /c", info.Arguments);
            else
            {
                Assert.AreEqual("/bin/bash", info.FileName);
                StringAssert.StartsWith("-c \"exec ", info.Arguments);
            }
        }

        [Test]
        public void LiteralHeadlessLaunch_BoundsPythonOutputWithInheritedHandles()
        {
            string python = Environment.GetEnvironmentVariable("MCP_UNITY_TEST_PYTHON");
            string serverSource = Environment.GetEnvironmentVariable("MCP_UNITY_TEST_SERVER_SRC");
            if (string.IsNullOrEmpty(python) || !File.Exists(python) || string.IsNullOrEmpty(serverSource))
                Assert.Ignore("Set MCP_UNITY_TEST_PYTHON and MCP_UNITY_TEST_SERVER_SRC for the bounded output integration test.");

            string root = Path.Combine(_launcher.GetProjectRootPath(), "Library", "MCPForUnity", "Tests", "bounded-log-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string script = Path.Combine(root, "write_log.py");
            string log = Path.Combine(root, "launch 한글 & %.log");
            File.WriteAllText(
                script,
                "import logging, os, sys\n"
                    + "sys.path.insert(0, os.environ['MCP_UNITY_TEST_SERVER_SRC'])\n"
                    + "from core.launch_output import install_managed_launch_output\n"
                    + "assert install_managed_launch_output()\n"
                    + "sys.stdout.buffer.write(b'x' * (10 * 1024 * 1024))\n"
                    + "logging.basicConfig()\n"
                    + "for i in range(10000): logging.warning('x' * 1024)\n"
                    + "sys.stderr.write('final diagnostic 한글\\n')\n",
                new UTF8Encoding(false)
            );
            Process child = null;
            try
            {
                var info = _launcher.CreateHeadlessProcessStartInfo(python, new[] { "-I", "-X", "utf8", script }, log);
                info.EnvironmentVariables["MCP_UNITY_TEST_SERVER_SRC"] = serverSource;
                child = Process.Start(info);
                Assert.IsNotNull(child);
                Assert.IsTrue(child.WaitForExit(30000), "The bounded output writer must exit within the watchdog.");
                Assert.AreEqual(0, child.ExitCode);
                Assert.LessOrEqual(new FileInfo(log).Length, 8 * 1024 * 1024);
                string captured = File.ReadAllText(log, Encoding.UTF8);
                StringAssert.Contains("older launch output discarded", captured);
                StringAssert.EndsWith("final diagnostic 한글", captured.Trim());
                using (File.Open(log, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            }
            finally
            {
                if (child != null)
                {
                    if (!child.HasExited)
                    {
                        child.Kill();
                        child.WaitForExit(5000);
                    }
                    child.Dispose();
                }
                Directory.Delete(root, true);
            }
        }

#if UNITY_EDITOR_WIN
        [Test]
        public void LiteralHeadlessLaunch_RejectsExpandedWindowsCommandBeyondLimit()
        {
            var error = Assert.Throws<ArgumentException>(() => _launcher.CreateHeadlessProcessStartInfo("uvx", new[] { new string('a', 8192) }, LogPath()));
            StringAssert.Contains("8191", error.Message);
        }

        [Test]
        public void LiteralHeadlessLaunch_RejectsAggregateWindowsCommandBeyondLimit()
        {
            var error = Assert.Throws<ArgumentException>(() =>
                _launcher.CreateHeadlessProcessStartInfo("uvx", Enumerable.Repeat(new string('a', 410), 20).ToArray(), LogPath())
            );
            StringAssert.Contains("8191", error.Message);
        }

        [Test]
        public void LiteralHeadlessLaunch_ExecutablePathWithMetacharactersIsLiteral()
        {
            string root = Path.Combine(_launcher.GetProjectRootPath(), "Library", "MCPForUnity", "Tests", "executable-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string executable = Path.Combine(root, "R&D %MCP_R3_EXPAND% $cash's!^.exe");
            File.Copy(Path.Combine(Environment.SystemDirectory, "where.exe"), executable);
            Process child = null;
            try
            {
                var info = _launcher.CreateHeadlessProcessStartInfo(executable, new[] { "cmd.exe" }, Path.Combine(root, "launch.log"));
                info.EnvironmentVariables["MCP_R3_EXPAND"] = "must-not-expand";
                child = Process.Start(info);
                Assert.IsTrue(child.WaitForExit(10000));
                Assert.AreEqual(0, child.ExitCode);
            }
            finally
            {
                if (child != null)
                {
                    if (!child.HasExited)
                    {
                        child.Kill();
                        child.WaitForExit(5000);
                    }
                    child.Dispose();
                }
                Directory.Delete(root, true);
            }
        }
#endif

        #region Interface Implementation Tests

        [Test]
        public void TerminalLauncher_ImplementsITerminalLauncher()
        {
            // Assert
            Assert.IsInstanceOf<ITerminalLauncher>(_launcher);
        }

        [Test]
        public void TerminalLauncher_CanBeUsedViaInterface()
        {
            // Arrange
            ITerminalLauncher launcher = new TerminalLauncher();

            // Act & Assert
            Assert.DoesNotThrow(() =>
            {
                launcher.GetProjectRootPath();
                launcher.CreateTerminalProcessStartInfo("test");
            });
        }

        #endregion

        #region Platform-Specific Behavior Tests

        [Test]
        public void CreateTerminalProcessStartInfo_ReturnsAppropriateTerminal()
        {
            // Act
            var startInfo = _launcher.CreateTerminalProcessStartInfo("echo test");

            // Assert - Platform-specific
#if UNITY_EDITOR_OSX
            Assert.AreEqual("/usr/bin/open", startInfo.FileName, "macOS should use 'open'");
#elif UNITY_EDITOR_WIN
            Assert.AreEqual("cmd.exe", startInfo.FileName, "Windows should use 'cmd.exe'");
#else
            // Linux uses detected terminal
            Assert.IsNotNull(startInfo.FileName, "Linux should have a terminal command");
#endif
        }

        #endregion
    }
}
