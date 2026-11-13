using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Dependencies;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Dependencies.PlatformDetectors;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor
{
    public class PythonVersionDetectionTests
    {
        private sealed class PythonProbe : IDisposable
        {
            internal readonly string Root = Path.Combine(Path.GetTempPath(), "mcp-python-probe-" + Guid.NewGuid().ToString("N"), "First Last");
            internal string Selected;
            internal string Found;
            internal string Reported = "Python 3.11.14";
            internal bool FindSucceeds = true;
            internal bool VersionSucceeds = true;
            internal int FindCalls;
            internal int VersionCalls;

            internal PythonProbe()
            {
                Directory.CreateDirectory(Root);
                Selected = Path.Combine(Root, "uv.exe");
                Found = Path.Combine(Root, "python.exe");
                File.WriteAllText(Selected, "");
                File.WriteAllText(Found, "");
            }

            internal DependencyStatus Uv() => new DependencyStatus("uv") { IsAvailable = true, Path = Selected };

            internal bool Run(string executable, string arguments, out string stdout, out string stderr)
            {
                stderr = "";
                if (arguments == "--version")
                {
                    VersionCalls++;
                    Assert.AreEqual(Found, executable, "Validate the complete path, including spaces.");
                    stdout = Reported;
                    return VersionSucceeds;
                }
                FindCalls++;
                Assert.AreEqual(Path.Combine(Root, "uv.exe"), executable, "Use the selected uv or its uvx sibling.");
                Assert.AreEqual("python find --managed-python --no-python-downloads --system \">=3.11\"", arguments);
                stdout = Found + "\n";
                return FindSucceeds;
            }

            public void Dispose() => Directory.Delete(Path.GetDirectoryName(Root), true);
        }

        private sealed class WindowsProbe : WindowsPlatformDetector
        {
            internal PythonProbe Probe;

            public override DependencyStatus DetectUv() => Probe.Uv();

            protected override bool TryValidatePython(string path, out string version, out string fullPath)
            {
                version = "3.10.20";
                fullPath = path;
                return false;
            }

            protected override bool RunPythonDiscoveryCommand(string path, string arguments, out string stdout, out string stderr) =>
                Probe.Run(path, arguments, out stdout, out stderr);

            protected override bool TryFindInPath(string path, out string fullPath)
            {
                fullPath = null;
                return false;
            }
        }

        private sealed class MacProbe : MacOSPlatformDetector
        {
            internal PythonProbe Probe;

            public override DependencyStatus DetectUv() => Probe.Uv();

            protected override bool TryValidatePython(string path, out string version, out string fullPath)
            {
                version = "3.10.20";
                fullPath = path;
                return false;
            }

            protected override bool RunPythonDiscoveryCommand(string path, string arguments, out string stdout, out string stderr) =>
                Probe.Run(path, arguments, out stdout, out stderr);

            protected override bool TryFindInPath(string path, out string fullPath)
            {
                fullPath = null;
                return false;
            }
        }

        private sealed class LinuxProbe : LinuxPlatformDetector
        {
            internal PythonProbe Probe;

            public override DependencyStatus DetectUv() => Probe.Uv();

            protected override bool TryValidatePython(string path, out string version, out string fullPath)
            {
                version = "3.10.20";
                fullPath = path;
                return false;
            }

            protected override bool RunPythonDiscoveryCommand(string path, string arguments, out string stdout, out string stderr) =>
                Probe.Run(path, arguments, out stdout, out stderr);

            protected override bool TryFindInPath(string path, out string fullPath)
            {
                fullPath = null;
                return false;
            }
        }

        private static PlatformDetectorBase Detector(int platform, PythonProbe probe) =>
            platform == 0 ? new WindowsProbe { Probe = probe }
            : platform == 1 ? new MacProbe { Probe = probe }
            : new LinuxProbe { Probe = probe };

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public void ManagedPythonWithSpacesMakesEveryPlatformReadyDespiteOldSystemPython(int platform)
        {
            using var probe = new PythonProbe();
            var status = Detector(platform, probe).DetectPython();
            Assert.IsTrue(status.IsAvailable);
            Assert.AreEqual(probe.Found, status.Path);
            Assert.AreEqual("3.11.14", status.Version);
            Assert.AreEqual(1, probe.FindCalls);
            Assert.AreEqual(1, probe.VersionCalls);
            var result = new DependencyCheckResult();
            result.Dependencies.Add(status);
            result.Dependencies.Add(probe.Uv());
            result.GenerateSummary();
            Assert.IsTrue(result.IsSystemReady, "The dependency wizard must accept a compatible local managed Python.");
        }

        [TestCase(0, "3.10.20", true, true)]
        [TestCase(1, "3.10.20", true, true)]
        [TestCase(2, "3.10.20", true, true)]
        [TestCase(0, "3.11.14", false, true)]
        [TestCase(1, "3.11.14", false, true)]
        [TestCase(2, "3.11.14", false, true)]
        [TestCase(0, "3.11.14", true, false)]
        [TestCase(1, "3.11.14", true, false)]
        [TestCase(2, "3.11.14", true, false)]
        public void ManagedDiscoveryRejectsOldMissingAndBrokenInterpreters(int platform, string version, bool exists, bool runs)
        {
            using var probe = new PythonProbe { Reported = "Python " + version, VersionSucceeds = runs };
            if (!exists)
                File.Delete(probe.Found);
            Assert.IsFalse(Detector(platform, probe).DetectPython().IsAvailable);
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public void DownloadOnlyPythonNeverTriggersInstallation(int platform)
        {
            using var probe = new PythonProbe { FindSucceeds = false, Found = "cpython-3.13 <download available>" };
            Assert.IsFalse(Detector(platform, probe).DetectPython().IsAvailable);
            Assert.AreEqual(1, probe.FindCalls);
            Assert.AreEqual(0, probe.VersionCalls);
        }

        [Test]
        public void SelectedUvxUsesItsOwnUvSiblingAndNeverAnotherInstallation()
        {
            using var probe = new PythonProbe();
            probe.Selected = Path.Combine(probe.Root, "uvx.exe");
            File.WriteAllText(probe.Selected, "");
            Assert.IsTrue(Detector(0, probe).DetectPython().IsAvailable);
            File.Delete(Path.Combine(probe.Root, "uv.exe"));
            Assert.IsFalse(Detector(0, probe).DetectPython().IsAvailable);
            Assert.AreEqual(1, probe.FindCalls, "A missing selected sibling must not use an unrelated uv on PATH.");
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public void ManagedDiscoveryValidatesTheSelectedInterpreterAmongSeveralInstalled(int platform)
        {
            using var probe = new PythonProbe();
            File.WriteAllText(Path.Combine(probe.Root, "python3.10.exe"), "");
            File.WriteAllText(Path.Combine(probe.Root, "python3.11.exe"), "");
            probe.Found = Path.Combine(probe.Root, "python3.13.exe");
            probe.Reported = "Python 3.13.2";
            File.WriteAllText(probe.Found, "");
            var status = Detector(platform, probe).DetectPython();
            Assert.IsTrue(status.IsAvailable);
            Assert.AreEqual(probe.Found, status.Path);
            Assert.AreEqual("3.13.2", status.Version);
            Assert.AreEqual(1, probe.VersionCalls, "Validate the interpreter selected by the bounded >=3.11 uv query.");
        }

        [TestCase("3.10.20", false)]
        [TestCase("3.11.0", true)]
        [TestCase("3.11.14", true)]
        [TestCase("3.12.0", true)]
        [TestCase("2.7.18", false)]
        [TestCase("invalid", false)]
        [TestCase(null, false)]
        public void PlatformDetectors_EnforceMinimumPythonVersion(string version, bool supported)
        {
            foreach (var detector in new PlatformDetectorBase[] { new WindowsPlatformDetector(), new MacOSPlatformDetector(), new LinuxPlatformDetector() })
            {
                Assert.AreEqual(supported, detector.IsSupportedPythonVersion(version), detector.PlatformName);
            }
        }
    }

    public class GitDetectionTests
    {
        [TestCase("git version 2.45.1.windows.1", "2.45.1.windows.1")]
        [TestCase("git version 2.39.5 (Apple Git-154)", "2.39.5")]
        [TestCase("  git version 2.43.0\n", "2.43.0")]
        public void TryParseGitVersion_ExtractsTheVersionToken(string output, string expected)
        {
            Assert.IsTrue(PlatformDetectorBase.TryParseGitVersion(output, out string version));
            Assert.AreEqual(expected, version);
        }

        [TestCase("")]
        [TestCase(null)]
        [TestCase("'git' is not recognized as an internal or external command")]
        [TestCase("git version ")]
        [TestCase("git version beta")]
        public void TryParseGitVersion_RejectsAnythingThatIsNotAGitVersionLine(string output)
        {
            Assert.IsFalse(PlatformDetectorBase.TryParseGitVersion(output, out _));
        }

        [Test]
        public void CheckAllDependencies_ReportsGitAsOptional()
        {
            var result = DependencyManager.CheckAllDependencies();
            var git = result.Dependencies.FirstOrDefault(d => d.Name == "Git");

            Assert.IsNotNull(git, "The dependency check should always include a Git row");
            Assert.IsFalse(git.IsRequired, "Git must never block setup; only Git-URL installs need it");
            Assert.AreEqual(PlatformDetectorBase.GitInstallUrl, git.InstallationHint);
            if (git.IsAvailable)
            {
                Assert.IsFalse(string.IsNullOrEmpty(git.Version));
                Assert.IsFalse(string.IsNullOrEmpty(git.Path));
            }
            else
            {
                Assert.IsFalse(string.IsNullOrEmpty(git.ErrorMessage));
            }
        }

        [Test]
        public void MissingGit_DoesNotMakeTheSystemNotReady()
        {
            // Mirrors what the setup window computes: Python and uv present, git absent.
            var result = new DependencyCheckResult();
            result.Dependencies.Add(new DependencyStatus("Python") { IsAvailable = true, Version = "3.12.0" });
            result.Dependencies.Add(new DependencyStatus("uv Package Manager") { IsAvailable = true, Version = "0.8.0" });
            result.Dependencies.Add(new DependencyStatus("Git", isRequired: false) { IsAvailable = false, ErrorMessage = "git not found" });

            result.GenerateSummary();

            Assert.IsTrue(result.IsSystemReady);
            Assert.IsTrue(result.HasMissingOptional);
            Assert.IsEmpty(result.GetMissingRequired());
        }

        [Test]
        public void MissingOptionalRow_KeepsTheNeutralIndicatorAndExplainsWhatItIsFor()
        {
            var indicator = new VisualElement();
            indicator.AddToClassList("status-indicator-small");
            indicator.AddToClassList("valid"); // stale state from an earlier refresh
            var version = new Label();
            var details = new Label();
            var dep = new DependencyStatus("Git", isRequired: false)
            {
                IsAvailable = false,
                ErrorMessage = "git not found",
                Details = "Only needed to add or update MCP for Unity from a Git URL in the Package Manager.",
            };

            MCPSetupWindow.UpdateDependencyStatus(indicator, version, details, dep);

            // .status-indicator-small.invalid is red in Common.uss; an optional row must keep the
            // plain grey of the base class so it does not read as a blocker.
            Assert.IsFalse(indicator.ClassListContains("invalid"));
            Assert.IsFalse(indicator.ClassListContains("valid"));
            Assert.AreEqual(dep.Details, details.text);
            Assert.AreEqual(Color.gray, details.style.color.value);
        }

        [Test]
        public void MissingRequiredRow_StaysRed()
        {
            var indicator = new VisualElement();
            indicator.AddToClassList("status-indicator-small");
            var version = new Label();
            var details = new Label();
            var dep = new DependencyStatus("Python") { IsAvailable = false, ErrorMessage = "Python not found" };

            MCPSetupWindow.UpdateDependencyStatus(indicator, version, details, dep);

            Assert.IsTrue(indicator.ClassListContains("invalid"));
            Assert.AreEqual("Python not found", details.text);
            Assert.AreEqual(Color.red, details.style.color.value);
        }

        [Test]
        public void AvailableGitRow_ShowsTheVersionAndTheSafeDirectoryRemedy()
        {
            var indicator = new VisualElement();
            indicator.AddToClassList("status-indicator-small");
            indicator.AddToClassList("invalid"); // stale state from an earlier refresh
            var version = new Label();
            var details = new Label();
            var dep = new DependencyStatus("Git", isRequired: false)
            {
                IsAvailable = true,
                Version = "2.45.1",
                Details = "run git config --global --add safe.directory \"<your Unity project folder>\"",
            };

            MCPSetupWindow.UpdateDependencyStatus(indicator, version, details, dep);

            Assert.IsTrue(indicator.ClassListContains("valid"));
            Assert.IsFalse(indicator.ClassListContains("invalid"));
            Assert.AreEqual("v2.45.1", version.text);
            Assert.AreEqual(dep.Details, details.text);
        }
    }
}
