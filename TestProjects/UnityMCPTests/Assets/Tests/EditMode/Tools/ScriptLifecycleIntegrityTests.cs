using System;
using System.IO;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ScriptLifecycleIntegrityTests
    {
        private string tempRoot;
        private string target;
        private string assetPath;
        private string physicalAssetPath;
        private bool ownsTempRoot;
        private bool observedOwnedAssetDirectory;

        [SetUp]
        public void SetUp()
        {
            ownsTempRoot = false;
            observedOwnedAssetDirectory = false;
            string suffix = Guid.NewGuid().ToString("N");
            tempRoot = Path.Combine(Path.GetTempPath(), "McpScriptLifecycleIntegrity_" + suffix);
            Assert.IsFalse(File.Exists(tempRoot) || Directory.Exists(tempRoot), "Temporary root collision.");
            Directory.CreateDirectory(tempRoot);
            ownsTempRoot = true;
            target = Path.Combine(tempRoot, "Fixture.txt");
            assetPath = "Assets/__McpScriptLifecycleIntegrity_" + suffix;
            physicalAssetPath = Path.Combine(Application.dataPath, Path.GetFileName(assetPath));
            Assert.IsFalse(File.Exists(physicalAssetPath) || Directory.Exists(physicalAssetPath)
                || File.Exists(physicalAssetPath + ".meta"), "Asset path collision.");
        }

        [TearDown]
        public void TearDown()
        {
            if (ownsTempRoot && Directory.Exists(tempRoot))
            {
                string[] entries = Directory.GetFileSystemEntries(tempRoot);
                bool known = true;
                foreach (string entry in entries)
                    known &= entry == target || entry == target + ".tmp" || entry == target + ".bak";
                if (known)
                {
                    foreach (string entry in entries)
                        if (File.Exists(entry)) File.Delete(entry);
                        else if (Directory.Exists(entry) && Directory.GetFileSystemEntries(entry).Length == 0)
                            Directory.Delete(entry);
                    if (Directory.GetFileSystemEntries(tempRoot).Length == 0) Directory.Delete(tempRoot);
                }
                else TestContext.WriteLine("Retaining unexpected proof artifacts: " + tempRoot);
            }
            if (observedOwnedAssetDirectory && Directory.Exists(physicalAssetPath))
            {
                if (Directory.GetFileSystemEntries(physicalAssetPath).Length == 0)
                    Directory.Delete(physicalAssetPath);
                else TestContext.WriteLine("Retaining unexpected rejected-request artifacts: " + physicalAssetPath);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WriterPreservesUnrelatedSidecarsAndExactUtf8(bool overwrite)
        {
            const string contents = "// 😀 café\r\nclass Fixture {}\r\n";
            if (overwrite) File.WriteAllText(target, "old");
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            Write(contents, overwrite);

            CollectionAssert.AreEqual(new UTF8Encoding(false).GetBytes(contents), File.ReadAllBytes(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(tempRoot, "Fixture.txt.*.tmp").Length);
            Assert.AreEqual(0, Directory.GetFiles(tempRoot, "Fixture.txt.*.bak").Length);
        }

        [Test]
        public void WriterCreateNeverOverwritesOccupiedDestination()
        {
            File.WriteAllText(target, "other writer");
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            var error = Assert.Throws<TargetInvocationException>(() => Write("replacement", false));

            Assert.IsInstanceOf<IOException>(error.InnerException);
            Assert.AreEqual("other writer", File.ReadAllText(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(tempRoot, "Fixture.txt.*.tmp").Length);
        }

        [Test]
        public void WriterCreateFailureCleansOnlyOwnedTemporaryFile()
        {
            Directory.CreateDirectory(target);
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            Assert.Throws<TargetInvocationException>(() => Write("replacement", false));

            Assert.IsTrue(Directory.Exists(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(tempRoot, "Fixture.txt.*.tmp").Length);
        }

        [TestCase("create")]
        [TestCase("update")]
        public void PredictablePublicRejectionCreatesNoDirectory(string action)
        {
            JObject response;
            try
            {
                response = JObject.FromObject(ManageScript.HandleCommand(new JObject
                {
                    ["action"] = action,
                    ["name"] = "Fixture",
                    ["path"] = assetPath,
                    ["contents"] = action == "create" ? "class Fixture {" : "class Fixture {}"
                }));
            }
            finally { observedOwnedAssetDirectory = Directory.Exists(physicalAssetPath); }

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(Directory.Exists(physicalAssetPath));
            Assert.IsFalse(File.Exists(Path.Combine(physicalAssetPath, "Fixture.cs")));
        }

        private void Write(string contents, bool overwrite)
        {
            MethodInfo writer = typeof(ManageScript).GetMethod("WriteScriptFile", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(writer);
            writer.Invoke(null, new object[] { target, contents, overwrite });
        }
    }
}
