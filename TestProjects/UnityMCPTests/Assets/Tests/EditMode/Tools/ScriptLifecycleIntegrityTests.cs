using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
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
            Assert.IsFalse(
                File.Exists(physicalAssetPath) || Directory.Exists(physicalAssetPath) || File.Exists(physicalAssetPath + ".meta"),
                "Asset path collision."
            );
        }

        [TearDown]
        public void TearDown()
        {
            typeof(ManageScript).GetField("_scriptWriteBeforeCommitForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null);
            if (ownsTempRoot && Directory.Exists(tempRoot))
            {
                string[] entries = Directory.GetFileSystemEntries(tempRoot);
                bool known = true;
                foreach (string entry in entries)
                    known &= entry == target || entry == target + ".tmp" || entry == target + ".bak";
                if (known)
                {
                    foreach (string entry in entries)
                        if (File.Exists(entry))
                            File.Delete(entry);
                        else if (Directory.Exists(entry) && Directory.GetFileSystemEntries(entry).Length == 0)
                            Directory.Delete(entry);
                    if (Directory.GetFileSystemEntries(tempRoot).Length == 0)
                        Directory.Delete(tempRoot);
                }
                else
                    TestContext.WriteLine("Retaining unexpected proof artifacts: " + tempRoot);
            }
            if (observedOwnedAssetDirectory && Directory.Exists(physicalAssetPath))
            {
                if (Directory.Exists(target) && Directory.GetFileSystemEntries(target).Length == 0)
                    Directory.Delete(target);
                foreach (string entry in Directory.GetFiles(physicalAssetPath))
                {
                    string filename = Path.GetFileName(entry);
                    bool owned =
                        filename == "Fixture.txt"
                        || filename == "Fixture.txt.tmp"
                        || filename == "Fixture.txt.bak"
                        || filename == "Fixture.txt.meta"
                        || filename == "Fixture.txt.external-original"
                        || System.Text.RegularExpressions.Regex.IsMatch(filename, @"^\.mcp-[a-f0-9]{32}\.(tmp|bak)$");
                    if (owned)
                        File.Delete(entry);
                }
                if (Directory.GetFileSystemEntries(physicalAssetPath).Length == 0)
                {
                    Directory.Delete(physicalAssetPath);
                    if (File.Exists(physicalAssetPath + ".meta"))
                        File.Delete(physicalAssetPath + ".meta");
                }
                else
                    TestContext.WriteLine("Retaining unexpected rejected-request artifacts: " + physicalAssetPath);
            }
        }

        [Test]
        public void AbsoluteScriptDirectoryIsBoundToTheSelectedProject()
        {
            var resolve = typeof(ManageScript).GetMethod("TryResolveUnderAssets", BindingFlags.Static | BindingFlags.NonPublic);
            string nested = Path.Combine(Application.dataPath, "Nested", "Assets", "Scripts");
            object[] valid = { nested, null, null };
            Assert.IsTrue((bool)resolve.Invoke(null, valid));
            Assert.AreEqual(Path.GetFullPath(nested), valid[1]);
            Assert.AreEqual("Assets/Nested/Assets/Scripts", valid[2]);

            string outside = Path.Combine(Path.GetDirectoryName(Application.dataPath), "OtherProject", "Assets", "Scripts");
            object[] invalid = { outside, null, null };
            Assert.IsFalse((bool)resolve.Invoke(null, invalid));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WriterPreservesUnrelatedSidecarsAndExactUtf8(bool overwrite)
        {
            PrepareWriterTarget();
            const string contents = "// 😀 café\r\nclass Fixture {}\r\n";
            if (overwrite)
                File.WriteAllText(target, "old");
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            Write(contents, overwrite);

            CollectionAssert.AreEqual(new UTF8Encoding(false).GetBytes(contents), File.ReadAllBytes(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*.tmp").Length);
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*.bak").Length);
        }

        [TestCase(false, 0xD800)]
        [TestCase(false, 0xDC00)]
        [TestCase(true, 0xD800)]
        [TestCase(true, 0xDC00)]
        public void WriterRejectsInvalidUnicodeBeforeCreatingTemporaryFiles(bool overwrite, int surrogate)
        {
            PrepareWriterTarget();
            if (overwrite)
                File.WriteAllText(target, "original", new UTF8Encoding(false));
            var entries = Directory.GetFileSystemEntries(physicalAssetPath);
            var original = overwrite ? File.ReadAllBytes(target) : null;

            var error = Assert.Throws<TargetInvocationException>(() => Write("/* " + (char)surrogate + " */", overwrite));

            Assert.IsInstanceOf<EncoderFallbackException>(error.InnerException);
            CollectionAssert.AreEquivalent(entries, Directory.GetFileSystemEntries(physicalAssetPath));
            if (overwrite)
                CollectionAssert.AreEqual(original, File.ReadAllBytes(target));
            else
                Assert.IsFalse(File.Exists(target));
        }

        [Test]
        public void WriterCreateNeverOverwritesOccupiedDestination()
        {
            PrepareWriterTarget();
            File.WriteAllText(target, "other writer");
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            var error = Assert.Throws<TargetInvocationException>(() => Write("replacement", false));

            Assert.IsInstanceOf<IOException>(error.InnerException);
            Assert.AreEqual("other writer", File.ReadAllText(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*.tmp").Length);
        }

        [Test]
        public void WriterCreateFailureCleansOnlyOwnedTemporaryFile()
        {
            PrepareWriterTarget();
            Directory.CreateDirectory(target);
            File.WriteAllText(target + ".tmp", "unrelated tmp");
            File.WriteAllText(target + ".bak", "unrelated backup");

            Assert.Throws<TargetInvocationException>(() => Write("replacement", false));

            Assert.IsTrue(Directory.Exists(target));
            Assert.AreEqual("unrelated tmp", File.ReadAllText(target + ".tmp"));
            Assert.AreEqual("unrelated backup", File.ReadAllText(target + ".bak"));
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*.tmp").Length);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WriterRejectsDestinationChangedSincePreparation(bool deleted)
        {
            PrepareWriterTarget();
            File.WriteAllText(target, "original", new UTF8Encoding(false));
            string expected = Digest(File.ReadAllBytes(target));
            File.WriteAllText(target + ".meta", "original metadata");
            if (deleted)
                File.Delete(target);
            else
                File.WriteAllText(target, "competing contents", new UTF8Encoding(false));

            var error = Assert.Throws<TargetInvocationException>(() => Write("candidate", true, expected));

            Assert.IsInstanceOf<IOException>(error.InnerException);
            Assert.AreEqual(!deleted, File.Exists(target));
            if (!deleted)
                Assert.AreEqual("competing contents", File.ReadAllText(target));
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*").Length);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WriterRejectsDestinationChangedAtPublication(bool deleted)
        {
            PrepareWriterTarget();
            File.WriteAllText(target, "original", new UTF8Encoding(false));
            File.WriteAllText(target + ".meta", "original metadata");
            SetBeforeCommit(path =>
            {
                if (deleted)
                    File.Delete(path);
                else
                {
                    File.Move(path, path + ".external-original");
                    File.WriteAllText(path, "competing contents", new UTF8Encoding(false));
                }
            });

            var error = Assert.Throws<TargetInvocationException>(() => Write("candidate", true));

            Assert.IsInstanceOf<IOException>(error.InnerException);
            Assert.AreEqual(!deleted, File.Exists(target));
            if (!deleted)
            {
                Assert.AreEqual("competing contents", File.ReadAllText(target));
                Assert.AreEqual("original", File.ReadAllText(target + ".external-original"));
            }
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
            Assert.AreEqual(0, Directory.GetFiles(physicalAssetPath, ".mcp-*").Length);
        }

        [Test]
        public void WriterReplaceFailureRetainsCandidateWithoutCopyingOverSource()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Windows deny-delete handle exercises File.Replace failure.");
            PrepareWriterTarget();
            File.WriteAllText(target, "original", new UTF8Encoding(false));
            File.WriteAllText(target + ".meta", "original metadata");
            FileStream blocker = null;
            SetBeforeCommit(path => blocker = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read));
            TargetInvocationException error;
            try
            {
                error = Assert.Throws<TargetInvocationException>(() => Write("candidate", true));
            }
            finally
            {
                blocker?.Dispose();
            }

            Assert.IsInstanceOf<IOException>(error.InnerException);
            StringAssert.Contains("publication is uncertain", error.InnerException.Message);
            Assert.AreEqual("original", File.ReadAllText(target));
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
            string[] stages = Directory.GetFiles(physicalAssetPath, ".mcp-*.tmp");
            Assert.AreEqual(1, stages.Length, "Unconfirmed publication must retain recovery evidence.");
            Assert.AreEqual("candidate", File.ReadAllText(stages[0]));
        }

        [TestCase("create")]
        [TestCase("update")]
        public void PredictablePublicRejectionCreatesNoDirectory(string action)
        {
            JObject response;
            try
            {
                response = JObject.FromObject(
                    ManageScript.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["name"] = "Fixture",
                            ["path"] = assetPath,
                            ["contents"] = action == "create" ? "class Fixture {" : "class Fixture {}",
                        }
                    )
                );
            }
            finally
            {
                observedOwnedAssetDirectory = Directory.Exists(physicalAssetPath);
            }

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(Directory.Exists(physicalAssetPath));
            Assert.IsFalse(File.Exists(Path.Combine(physicalAssetPath, "Fixture.cs")));
        }

        [TestCase("wyg=")]
        [TestCase("77u/wyg=")]
        [TestCase("//4A2A==")]
        [TestCase("/v/YAA==")]
        [TestCase("//4AAAAAEQA=")]
        [TestCase("AAD+/wARAAA=")]
        public void DiskReadRejectsMalformedUnicodeWithoutChangingBytes(string encoded)
        {
            byte[] original = Convert.FromBase64String(encoded);
            File.WriteAllBytes(target, original);

            JObject response = ReadOwnedFile();

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("read script", (string)response["error"]);
            Assert.IsNull(response["data"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(target));
        }

        [TestCase("utf8")]
        [TestCase("utf8_bom")]
        [TestCase("utf16le")]
        [TestCase("utf16be")]
        [TestCase("utf32le")]
        [TestCase("utf32be")]
        public void DiskReadPreservesUnicodeBomAndLogicalHash(string kind)
        {
            Encoding encoding;
            switch (kind)
            {
                case "utf8":
                    encoding = new UTF8Encoding(false);
                    break;
                case "utf8_bom":
                    encoding = new UTF8Encoding(true);
                    break;
                case "utf16le":
                    encoding = new UnicodeEncoding(false, true);
                    break;
                case "utf16be":
                    encoding = new UnicodeEncoding(true, true);
                    break;
                case "utf32le":
                    encoding = new UTF32Encoding(false, true);
                    break;
                case "utf32be":
                    encoding = new UTF32Encoding(true, true);
                    break;
                default:
                    throw new ArgumentException("Unknown test encoding.", nameof(kind));
            }
            byte[] preamble = encoding.GetPreamble();
            string text = (preamble.Length > 0 ? "\uFEFF" : "") + "// \uD55C\uAE00 😀 intentional \uFFFD\r\nclass Fixture {}\r\n";
            byte[] payload = encoding.GetBytes(text);
            byte[] original = new byte[preamble.Length + payload.Length];
            Array.Copy(preamble, original, preamble.Length);
            Array.Copy(payload, 0, original, preamble.Length, payload.Length);
            File.WriteAllBytes(target, original);

            JObject response = ReadOwnedFile();

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(text, (string)response["data"]["contents"]);
            MethodInfo hash = typeof(ManageScript).GetMethod("ComputeSha256", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hash);
            using (var sha = SHA256.Create())
                Assert.AreEqual(
                    BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant(),
                    hash.Invoke(null, new object[] { (string)response["data"]["contents"] })
                );
            CollectionAssert.AreEqual(original, File.ReadAllBytes(target));

            File.WriteAllBytes(target, preamble);
            response = ReadOwnedFile();
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("", (string)response["data"]["contents"]);
            CollectionAssert.AreEqual(preamble, File.ReadAllBytes(target));
        }

        private JObject ReadOwnedFile()
        {
            // The production read path is pure; use the owned .txt target without importing C#.
            MethodInfo reader = typeof(ManageScript).GetMethod("ReadScript", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(reader);
            return JObject.FromObject(reader.Invoke(null, new object[] { target, "Assets/Fixture.txt" }));
        }

        private void PrepareWriterTarget()
        {
            Directory.CreateDirectory(physicalAssetPath);
            observedOwnedAssetDirectory = true;
            target = Path.Combine(physicalAssetPath, "Fixture.txt");
        }

        private static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static void SetBeforeCommit(Action<string> callback)
        {
            var hook = typeof(ManageScript).GetField("_scriptWriteBeforeCommitForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            hook.SetValue(null, callback);
        }

        private void Write(string contents, bool overwrite, string expectedDigest = null)
        {
            MethodInfo writer = typeof(ManageScript).GetMethod("WriteScriptFile", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(writer);
            if (expectedDigest == null && overwrite && File.Exists(target))
                expectedDigest = Digest(File.ReadAllBytes(target));
            writer.Invoke(null, new object[] { target, contents, overwrite, expectedDigest });
        }
    }
}
