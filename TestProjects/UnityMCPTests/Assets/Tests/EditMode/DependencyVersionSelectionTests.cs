using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;

namespace MCPForUnityTests.Editor
{
    public class DependencyVersionSelectionTests
    {
        [TestCase("com.unity.cinemachine@2.9.7", true)]
        [TestCase("com.unity.cinemachine@3.0.0-pre.1", true)]
        [TestCase("com.unity.cinemachine", false)]
        [TestCase("com.unity.cinemachine@latest", false)]
        [TestCase("com.unity.cinemachine@^3.0.0", false)]
        [TestCase("com.unity.probuilder@3.0.0", false)]
        public void OnlyExactVersionOfTheSelectedPackageIsAccepted(string selection, bool expected)
        {
            Assert.AreEqual(expected, MCPForUnityEditorWindow.TryGetExactPackageSelection("com.unity.cinemachine", selection, out string exact));
            Assert.AreEqual(expected ? selection : null, exact);
        }

        [Test]
        public void BulkInstallPreservesInstalledVersionsEvenWhenANewerVersionWasSelected()
        {
            var selected = new Dictionary<string, string>
            {
                ["com.unity.cinemachine"] = "com.unity.cinemachine@3.0.0",
                ["com.unity.probuilder"] = "com.unity.probuilder@5.2.3",
            };
            var installed = new Dictionary<string, string> { ["com.unity.cinemachine"] = "2.9.7" };
            CollectionAssert.AreEqual(new[] { "com.unity.probuilder@5.2.3" }, MCPForUnityEditorWindow.SelectMissingPackages(selected, installed));
            installed["com.unity.probuilder"] = "file:../local";
            Assert.IsEmpty(MCPForUnityEditorWindow.SelectMissingPackages(selected, installed));
        }

        [Test]
        public void MissingVersionFailsBeforeAnyBulkRequestCanBeMade()
        {
            Assert.Throws<ArgumentException>(() =>
                MCPForUnityEditorWindow.SelectMissingPackages(
                    new Dictionary<string, string> { ["com.unity.cinemachine"] = "com.unity.cinemachine@" },
                    new Dictionary<string, string>()
                )
            );
        }

        [Test]
        public void ManifestParsingOnlyCountsDependenciesAndPreservesDeclaredSources()
        {
            var versions = MCPForUnityEditorWindow.ParseDeclaredUpmVersions(
                "{\"dependencies\":{\"com.unity.cinemachine\":\"2.9.7\",\"com.unity.probuilder\":\"file:../local\"},\"notes\":\"com.unity.visualeffectgraph\"}"
            );
            Assert.AreEqual("2.9.7", versions["com.unity.cinemachine"]);
            Assert.AreEqual("file:../local", versions["com.unity.probuilder"]);
            Assert.IsFalse(versions.ContainsKey("com.unity.visualeffectgraph"));
        }
    }
}
