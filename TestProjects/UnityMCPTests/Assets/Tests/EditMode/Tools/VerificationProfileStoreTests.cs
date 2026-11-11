using System;
using System.IO;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class VerificationProfileStoreTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "MCPVerification_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            Directory.Delete(root, true);
        }

        [Test]
        public void ReadingAbsentProfilesNeverCreatesProjectSettings()
        {
            var result = VerificationProfileStore.Read(root);
            Assert.That((string)result["byte_hash"], Is.EqualTo("missing"));
            Assert.That(Directory.GetFileSystemEntries(root), Is.Empty);
        }

        [Test]
        public void ExplicitProfileWritesRequireCurrentHashAndPreserveConflictingBytes()
        {
            var created = VerificationProfileStore.Write(root, "local", new JObject(), "missing");
            string path = Path.Combine(root, "ProjectSettings", "MCPVerificationProfiles.json");
            byte[] original = File.ReadAllBytes(path);
            Assert.Throws<InvalidOperationException>(() => VerificationProfileStore.Write(root, "local", new JObject(), "missing"));
            Assert.That(File.ReadAllBytes(path), Is.EqualTo(original));
            var updated = VerificationProfileStore.Write(root, "local", new JObject { ["refresh_policy"] = "never" }, (string)created["byte_hash"]);
            Assert.That((string)updated["byte_hash"], Is.Not.EqualTo((string)created["byte_hash"]));
            Assert.Throws<InvalidOperationException>(() =>
                VerificationProfileStore.Write(root, "other", new JObject(), (string)updated["byte_hash"], () => File.AppendAllText(path, "\n "))
            );
            Assert.That(File.ReadAllText(path), Does.EndWith("\n "));
            Assert.That(Directory.GetFiles(Path.GetDirectoryName(path), "*.tmp"), Is.Empty);
        }

        [TestCase("format", "true")]
        [TestCase("command", "\"cmd.exe\"")]
        [TestCase("test_mode", "\"Everything\"")]
        [TestCase("deadlines", "{\"tests\":0}")]
        [TestCase("deadlines", "{\"tests\":601}")]
        public void NativeBoundaryRejectsUnknownStagesAndInvalidProfiles(string field, string value)
        {
            Assert.Throws<ArgumentException>(() => VerificationProfileStore.Write(root, "bad", new JObject { [field] = JToken.Parse(value) }, "missing"));
            Assert.That(Directory.GetFileSystemEntries(root), Is.Empty);
        }
    }
}
