using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Services.AssetGen;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class CatalogPersistenceTests
    {
        private string root;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [SetUp]
        public void SetUp()
        {
            root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "McpCatalogPersistence_" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(root);
            OpenRouterModelCatalog.ResetForTests(true);
            FalModelCatalog.ResetForTests(true);
        }

        [TearDown]
        public void TearDown()
        {
            OpenRouterModelCatalog.ResetForTests(true);
            FalModelCatalog.ResetForTests(true);
            Assert.That(root.StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase), Is.True);
            Assert.That(Path.GetFileName(root).StartsWith("McpCatalogPersistence_", StringComparison.Ordinal), Is.True);
            Directory.Delete(root, true);
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void SaveRefusesLinkedAncestorsAndFinalFiles(bool fal, bool directoryLink)
        {
            string outside = Path.Combine(root, "outside");
            Directory.CreateDirectory(outside);
            string external = Path.Combine(outside, "catalog.json");
            File.WriteAllText(external, "retain");
            string link = Path.Combine(root, directoryLink ? "linked" : "catalog.json");
            string target = directoryLink ? outside : external;
            bool created = Path.DirectorySeparatorChar == '\\' ? CreateSymbolicLinkW(link, target, (directoryLink ? 1 : 0) | 2) : symlink(target, link) == 0;
            if (!created)
                Assert.Ignore("Host cannot create this owned link fixture.");
            try
            {
                Save(fal, directoryLink ? Path.Combine(link, "catalog.json") : link);
                Assert.That(File.ReadAllText(external), Is.EqualTo("retain"));
                Assert.That(Directory.GetFiles(outside).Length, Is.EqualTo(1));
            }
            finally
            {
                if (directoryLink)
                    Directory.Delete(link, false);
                else
                    File.Delete(link);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SaveReplacesOnlyTheSelectedCacheAndLeavesNoStage(bool fal)
        {
            string path = Path.Combine(root, "catalog.json");
            File.WriteAllText(path, "old");
            File.WriteAllText(Path.Combine(root, "catalog.json.tmp"), "unowned");
            Save(fal, path);
            Assert.That(File.ReadAllText(path), Is.Not.EqualTo("old"));
            Assert.That(File.ReadAllText(Path.Combine(root, "catalog.json.tmp")), Is.EqualTo("unowned"));
            Assert.That(Directory.GetFiles(root).Length, Is.EqualTo(2));
        }

        private static void Save(bool fal, string path)
        {
            Type type = fal ? typeof(FalModelCatalog) : typeof(OpenRouterModelCatalog);
            type.GetField("CachePathOverrideForTests", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, path);
            type.GetMethod("Save", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        }
    }
}
