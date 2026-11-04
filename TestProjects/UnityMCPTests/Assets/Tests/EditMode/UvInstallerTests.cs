using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Dependencies;
using NUnit.Framework;

namespace MCPForUnityTests.Editor
{
    /// <summary>
    /// Verifies the one-click uv installer builds the correct command per platform. The command
    /// is built by a pure method so it can be asserted without actually running the installer.
    /// </summary>
    public class UvInstallerTests
    {
        [Test]
        public void BuildInstallCommand_MatchesPlatform()
        {
            var (file, args) = UvInstaller.BuildInstallCommand();
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                Assert.AreEqual("powershell", file);
                StringAssert.Contains("astral.sh/uv/install.ps1", args);
                StringAssert.DoesNotContain("|", args, "A successful pipeline tail can hide a failed download.");
            }
            else
            {
                Assert.AreEqual("curl", file);
                StringAssert.DoesNotContain("|", args);
                StringAssert.Contains("astral.sh/uv/install.sh", args);
            }
        }

        [Test]
        public void DescribeCommand_IncludesFileAndInstallerUrl()
        {
            var (file, _) = UvInstaller.BuildInstallCommand();
            string desc = UvInstaller.DescribeCommand();
            StringAssert.Contains(file, desc);
            StringAssert.Contains("astral.sh/uv", desc);
        }

        [Test]
        public void IsSupported_IsTrueOnDesktopEditorPlatforms()
        {
            // EditMode tests only run on Windows/macOS/Linux editors, all of which are supported.
            Assert.IsTrue(UvInstaller.IsSupported);
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void DownloadAndExecutionAreSeparateOwnedSteps(bool downloadSucceeds, bool executionSucceeds)
        {
            var seam = typeof(UvInstaller).GetField("runCommand", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(seam, "An inert process boundary is required: no installer may run in this test.");
            object previous = seam.GetValue(null);
            var calls = new List<string>();
            string downloadedPath = null;
            try
            {
                seam.SetValue(
                    null,
                    (Func<string, string, int, UvInstaller.UvInstallResult>)(
                        (file, arguments, _) =>
                        {
                            calls.Add(file + " " + arguments);
                            if (calls.Count == 1)
                            {
                                // Only create inert content at the product-owned download destination.
                                var match = Regex.Match(arguments, "(?:-OutFile|--output) [\\\"']([^\\\"']+)[\\\"']");
                                Assert.IsTrue(match.Success, arguments);
                                downloadedPath = match.Groups[1].Value;
                                Assert.IsTrue(Directory.Exists(Path.GetDirectoryName(downloadedPath)));
                                File.WriteAllText(downloadedPath, "inert installer fixture; never executed");
                                return new UvInstaller.UvInstallResult(downloadSucceeds, "controlled download result");
                            }
                            Assert.IsTrue(File.Exists(downloadedPath), "Download must finish before execution.");
                            StringAssert.Contains(downloadedPath, arguments);
                            return new UvInstaller.UvInstallResult(executionSucceeds, "controlled execution result");
                        }
                    )
                );
                var result = UvInstaller.Run();
                Assert.AreEqual(downloadSucceeds && executionSucceeds, result.Success);
                Assert.AreEqual(downloadSucceeds ? 2 : 1, calls.Count);
                Assert.IsFalse(File.Exists(downloadedPath));
                Assert.IsFalse(Directory.Exists(Path.GetDirectoryName(downloadedPath)));
                if (!downloadSucceeds)
                    StringAssert.Contains("download", result.Output.ToLowerInvariant());
                else if (!executionSucceeds)
                    StringAssert.Contains("execution", result.Output.ToLowerInvariant());
            }
            finally
            {
                seam.SetValue(null, previous);
            }
        }
    }
}
