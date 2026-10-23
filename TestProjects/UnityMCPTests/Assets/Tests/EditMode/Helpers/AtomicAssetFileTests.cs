using System;
using System.IO;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class AtomicAssetFileTests
    {
        private string folder;
        private string target;

        [SetUp]
        public void SetUp()
        {
            folder = Path.Combine(Application.dataPath, "AtomicSource_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(folder);
            target = Path.Combine(folder, "source.txt");
            File.WriteAllText(target, "original", new UTF8Encoding(false));
            File.WriteAllText(target + ".meta", "original metadata");
        }

        [TearDown]
        public void TearDown()
        {
            if (folder != null && Path.GetFileName(folder).StartsWith("AtomicSource_", StringComparison.Ordinal))
            {
                if (Directory.Exists(folder))
                    Directory.Delete(folder, true);
                if (File.Exists(folder + ".meta"))
                    File.Delete(folder + ".meta");
            }
        }

        private static string Digest(byte[] value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(value)).Replace("-", "").ToLowerInvariant();
        }

        private void Replace(byte[] value, string expected = null, Action beforeCommit = null)
        {
            Type helper = typeof(ManageShader).Assembly.GetType("MCPForUnity.Editor.Helpers.AtomicAssetFile");
            Assert.IsNotNull(helper, "Asset updates need a shared nontruncating publication path.");
            MethodInfo method = helper.GetMethod("Replace", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            try
            {
                method.Invoke(null, new object[] { target, value, expected, beforeCommit });
            }
            catch (TargetInvocationException ex)
            {
                ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            }
        }

        [Test]
        public void ExistingReaderKeepsCompleteOriginalAfterAtomicPublication()
        {
            using var reader = new FileStream(target, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            Replace(Encoding.UTF8.GetBytes("complete replacement"));
            using var text = new StreamReader(reader);
            Assert.AreEqual("original", text.ReadToEnd(), "Replacing bytes in place corrupts already-open readers.");
            Assert.AreEqual("complete replacement", File.ReadAllText(target));
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
            Assert.AreEqual(2, Directory.GetFiles(folder).Length, "Confirmed staging artifacts must be released.");
        }

        [Test]
        public void InterruptedPreparedWriteLeavesCompleteOriginalAndMetadata()
        {
            bool prepared = false;
            Assert.Throws<IOException>(() =>
                Replace(
                    new byte[1024 * 1024],
                    null,
                    () =>
                    {
                        prepared = true;
                        Assert.AreEqual("original", File.ReadAllText(target));
                        throw new IOException("owned staging interruption");
                    }
                )
            );
            Assert.IsTrue(prepared);
            Assert.AreEqual("original", File.ReadAllText(target));
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
            Assert.AreEqual(2, Directory.GetFiles(folder).Length);
        }

        [Test]
        public void StalePreconditionNeverReplacesExternalBytes()
        {
            string expected = Digest(File.ReadAllBytes(target));
            File.WriteAllText(target, "external change");
            Assert.Throws<IOException>(() => Replace(Encoding.UTF8.GetBytes("candidate"), expected));
            Assert.AreEqual("external change", File.ReadAllText(target));
            Assert.AreEqual(2, Directory.GetFiles(folder).Length);
        }

        [Test]
        public void RenamedExternalReplacementBeforeCommitIsPreserved()
        {
            Assert.Throws<IOException>(() =>
                Replace(
                    Encoding.UTF8.GetBytes("candidate"),
                    null,
                    () =>
                    {
                        File.Move(target, target + ".external-original");
                        File.WriteAllText(target, "external replacement");
                    }
                )
            );
            Assert.AreEqual("external replacement", File.ReadAllText(target));
            Assert.AreEqual("original", File.ReadAllText(target + ".external-original"));
        }

        [Test]
        public void LargeReplacementPublishesEveryByteWithoutChangingMetadata()
        {
            byte[] bytes = new byte[8 * 1024 * 1024];
            for (int i = 0; i < bytes.Length; i++)
                bytes[i] = (byte)(i % 251);
            Replace(bytes);
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(target));
            Assert.AreEqual("original metadata", File.ReadAllText(target + ".meta"));
        }

        [Test]
        public void MissingDestinationDoesNotTurnUpdateIntoCreate()
        {
            File.Delete(target);
            Assert.Throws<FileNotFoundException>(() => Replace(new byte[] { 1, 2, 3 }));
            Assert.IsFalse(File.Exists(target));
        }
    }
}
