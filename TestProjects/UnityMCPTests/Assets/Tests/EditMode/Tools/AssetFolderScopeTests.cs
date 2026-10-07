using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class AssetFolderScopeTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__McpFolderScope_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(root)));
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(root) && AssetDatabase.IsValidFolder(root))
                AssetDatabase.DeleteAsset(root);
        }

        [Test]
        public void FailedOperation_RemovesOnlyNewEmptyAncestors()
        {
            string existing = root + "/Existing";
            string guid = AssetDatabase.CreateFolder(root, "Existing");
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureParentDirectory(existing + "/New/Nested/Failed.asset");
                Assert.IsTrue(AssetDatabase.IsValidFolder(existing + "/New/Nested"));
            }

            Assert.IsFalse(Directory.Exists(FullPath(existing + "/New")));
            Assert.IsFalse(File.Exists(FullPath(existing + "/New") + ".meta"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(existing));
        }

        [Test]
        public void ExplicitlyCompletedFolderCreation_PreservesEmptyOutput()
        {
            string target = root + "/New/Nested";
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureFolder(target);
                folders.Complete();
            }
            Assert.IsTrue(AssetDatabase.IsValidFolder(target));
        }

#if UNITY_EDITOR_WIN
        [Test]
        public void ExistingParentWithDifferentCasing_AllowsNewChild()
        {
            string parentGuid = AssetDatabase.CreateFolder(root, "MixedCase");
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureFolder(root + "/mixedcase/Child");
                folders.Complete();
            }

            Assert.IsTrue(AssetDatabase.IsValidFolder(root + "/MixedCase/Child"));
            Assert.AreEqual(parentGuid, AssetDatabase.AssetPathToGUID(root + "/MixedCase"));
        }
#endif

        [Test]
        public void FailedOperation_PreservesPartialFilesAndPrunesEmptySibling()
        {
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureFolder(root + "/New/Successful");
                folders.EnsureFolder(root + "/New/Failed");
                File.WriteAllText(FullPath(root + "/New/Successful/Partial.txt"), "Keep partial output.");
            }

            Assert.IsTrue(File.Exists(FullPath(root + "/New/Successful/Partial.txt")));
            Assert.IsTrue(AssetDatabase.IsValidFolder(root + "/New/Successful"));
            Assert.IsFalse(AssetDatabase.IsValidFolder(root + "/New/Failed"));
        }

        [Test]
        public void FailedOperation_PreservesFolderRecreatedWithDifferentGuid()
        {
            string path = root + "/Replaced";
            string replacement;
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureFolder(path);
                string original = AssetDatabase.AssetPathToGUID(path);
                Assert.IsTrue(AssetDatabase.DeleteAsset(path));
                replacement = AssetDatabase.CreateFolder(root, "Replaced");
                Assert.AreNotEqual(original, replacement);
            }
            Assert.AreEqual(replacement, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void RegisterPreexistingDiskFolder_DoesNotOwnItOrImportUnrelatedFiles()
        {
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string existing = root + "/DiskOnly";
                string pending = root + "/Unrelated.txt";
                Directory.CreateDirectory(FullPath(existing));
                File.WriteAllText(FullPath(pending), "Not part of the requested output.");
                Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending, AssetPathToGUIDOptions.OnlyExistingAssets));

                using (var folders = new AssetFolderScope())
                    folders.EnsureFolder(existing);

                Assert.IsTrue(AssetDatabase.IsValidFolder(existing));
                Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending, AssetPathToGUIDOptions.OnlyExistingAssets));
            }
            finally { AssetDatabase.AllowAutoRefresh(); }
        }

        [Test]
        public void LaterFolderFailure_UnwindsEarlierEmptyFoldersAndPreservesBlockingFile()
        {
            string blocking = root + "/Blocked.txt";
            File.WriteAllText(FullPath(blocking), "Existing data.");
            Assert.Throws<IOException>(() =>
            {
                using (var folders = new AssetFolderScope())
                {
                    folders.EnsureFolder(root + "/New/Nested");
                    folders.EnsureFolder(blocking);
                }
            });
            Assert.IsFalse(Directory.Exists(FullPath(root + "/New")));
            Assert.AreEqual("Existing data.", File.ReadAllText(FullPath(blocking)));
        }

        private static string FullPath(string path) =>
            Path.Combine(Application.dataPath, path.Substring("Assets/".Length));
    }
}
