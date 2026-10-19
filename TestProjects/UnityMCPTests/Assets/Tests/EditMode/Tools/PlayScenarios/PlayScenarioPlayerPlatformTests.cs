using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class PlayScenarioPlayerPlatformTests
    {
        [TestCase("windows64", "MCPScenarioPlayer.exe", "MCPScenarioPlayer.exe")]
        [TestCase("linux64", "MCPScenarioPlayer.x86_64", "MCPScenarioPlayer.x86_64")]
        [TestCase("macos", "MCPScenarioPlayer.app/Contents/MacOS/MCPScenarioPlayer", "MCPScenarioPlayer.app")]
        public void PlatformPathsDistinguishBundleAndNativeExecutable(string target, string executable, string bundle)
        {
            Assert.That(PlayScenarioPlayerPlatform.Executable(target), Is.EqualTo(executable));
            Assert.That(PlayScenarioPlayerPlatform.BundleLocation(target), Is.EqualTo(bundle));
            Assert.That(PlayScenarioPlayerPlatform.TargetForExecutable(executable), Is.EqualTo(target));
        }

        [Test]
        public void InvalidBackendRefusesBeforeOutputAndSettingsMutation()
        {
            string output = Path.Combine(Path.GetTempPath(), "mcp-invalid-backend-" + Guid.NewGuid().ToString("N"));
#pragma warning disable CS0618
            ScriptingImplementation original = PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBuild.BuildFrozen(null, null, output, backend: "invalid"));
            Assert.That(PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone), Is.EqualTo(original));
#pragma warning restore CS0618
            Assert.That(Directory.Exists(output), Is.False);
        }

        [Test]
        public void UnsupportedTargetRefusesBeforeOutputAndSettingsMutation()
        {
            string output = Path.Combine(Path.GetTempPath(), "mcp-invalid-target-" + Guid.NewGuid().ToString("N"));
#pragma warning disable CS0618
            ScriptingImplementation original = PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBuild.BuildFrozen(null, null, output, target: "android"));
            Assert.That(PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone), Is.EqualTo(original));
#pragma warning restore CS0618
            Assert.That(Directory.Exists(output), Is.False);
        }
    }
}
