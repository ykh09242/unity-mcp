using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ScriptChangeSetTests
    {
        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;
        private string folder,
            storage,
            relative;
        private FieldInfo consent,
            enabled;
        private object previousConsent,
            previousEnabled;
        private Type helper;
        private readonly System.Collections.Generic.Dictionary<FieldInfo, object> saved = new();

        [SetUp]
        public void SetUp()
        {
            string id = Guid.NewGuid().ToString("N");
            relative = "Assets/Dev12Changes_" + id;
            folder = Path.Combine(Application.dataPath, "Dev12Changes_" + id);
            storage = Path.Combine(Path.GetTempPath(), "Dev12Changes_" + id);
            Directory.CreateDirectory(folder);
            Directory.CreateDirectory(storage);
            consent = typeof(ManageScript).GetField("_explicitConsentForTests", Flags);
            enabled = typeof(ManageScript).GetField("_parentEnabledForTests", Flags);
            previousConsent = consent.GetValue(null);
            previousEnabled = enabled.GetValue(null);
            consent.SetValue(null, (Func<bool>)(() => true));
            enabled.SetValue(null, (Func<bool>)(() => true));
            helper = typeof(ManageScript).Assembly.GetType("MCPForUnity.Editor.Tools.ScriptChangeSet");
            if (helper != null)
            {
                SetSeam("_storageRootForTests", (Func<string>)(() => storage));
                SetSeam("_domainForTests", (Func<string>)(() => "first-domain"));
            }
            Seed("First", true, "\r\n");
            Seed("Second", false, "\n");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var entry in saved)
                entry.Key.SetValue(null, entry.Value);
            saved.Clear();
            consent.SetValue(null, previousConsent);
            enabled.SetValue(null, previousEnabled);
            if (folder != null && Path.GetFileName(folder).StartsWith("Dev12Changes_", StringComparison.Ordinal) && Directory.Exists(folder))
                Directory.Delete(folder, true);
            if (folder != null && Path.GetFileName(folder).StartsWith("Dev12Changes_", StringComparison.Ordinal) && File.Exists(folder + ".meta"))
                File.Delete(folder + ".meta");
            if (storage != null && Path.GetFileName(storage).StartsWith("Dev12Changes_", StringComparison.Ordinal) && Directory.Exists(storage))
                Directory.Delete(storage, true);
        }

        private void SetSeam(string name, object value)
        {
            Assert.IsNotNull(helper, "The bounded change-set helper must be present after prepare succeeds.");
            var field = helper.GetField(name, Flags);
            Assert.IsNotNull(field, name);
            if (!saved.ContainsKey(field))
                saved.Add(field, field.GetValue(null));
            field.SetValue(null, value);
        }

        private static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private void Seed(string name, bool bom, string newline)
        {
            File.WriteAllText(Path.Combine(folder, name + ".cs"), "class " + name + " { string text = \"old\"; }" + newline, new UTF8Encoding(bom, true));
            File.WriteAllText(Path.Combine(folder, name + ".cs.meta"), "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n");
        }

        private byte[] Bytes(string name) => File.ReadAllBytes(Path.Combine(folder, name + ".cs"));

        private string Candidate(string name) => "class " + name + " { string text = \"새😀value\"; }" + (name == "First" ? "\r\n" : "\n");

        private JObject Entry(string name) =>
            new JObject
            {
                ["path"] = relative + "/" + name + ".cs",
                ["expected_bytes_sha256"] = Digest(Bytes(name)),
                ["contents"] = Candidate(name),
            };

        private JObject Command(string action, string id = null, JArray files = null)
        {
            var parameters = new JObject { ["action"] = action };
            if (id != null)
                parameters["change_set_id"] = id;
            if (files != null)
                parameters["files"] = files;
            return JObject.FromObject(ManageScript.HandleCommand(parameters));
        }

        private string Prepare()
        {
            var response = Command("prepare_change_set", files: new JArray(Entry("First"), Entry("Second")));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            string id = response["data"].Value<string>("change_set_id");
            Assert.IsNotEmpty(id);
            return id;
        }

        [Test]
        public void PrepareApply_PreservesOrderedBytesBomUnicodeAndMetadata()
        {
            // Given: two sources with distinct BOM/newline policies and stable metadata.
            byte[] before = Bytes("First");
            byte[] meta = File.ReadAllBytes(Path.Combine(folder, "First.cs.meta"));
            // When: a complete change set is prepared and then applied.
            string id = Prepare();
            CollectionAssert.AreEqual(before, Bytes("First"), "Preparation must not write source files.");
            var response = Command("apply_change_set", id);
            // Then: all ordered outcomes are confirmed and original byte policies survive.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("applied", (string)response["data"]["phase"]);
            CollectionAssert.AreEqual(
                new UTF8Encoding(true).GetPreamble().Concat(new UTF8Encoding(false).GetBytes(Candidate("First"))).ToArray(),
                Bytes("First")
            );
            CollectionAssert.AreEqual(new UTF8Encoding(false).GetBytes(Candidate("Second")), Bytes("Second"));
            CollectionAssert.AreEqual(meta, File.ReadAllBytes(Path.Combine(folder, "First.cs.meta")));
            Assert.AreEqual(relative + "/First.cs", (string)response["data"]["files"][0]["path"]);
            Assert.IsTrue(response["data"]["files"].All(row => (string)row["state"] == "confirmed"));
            Assert.IsFalse((bool)response["data"]["filesystem_transaction"]);
        }

        [Test]
        public void Apply_LongValidSourcePathUsesBoundedAdjacentBackupAndRecovers()
        {
            // Given: a valid source path close to Windows' legacy path limit.
            int nameLength = Math.Min(180, 244 - folder.Length - 4);
            Assert.GreaterOrEqual(nameLength, 48, "The owned fixture directory must leave room for a representative source filename.");
            string name = "Long" + new string('x', nameLength - 4);
            Seed(name, false, "\n");
            byte[] original = Bytes(name);
            var prepared = Command("prepare_change_set", files: new JArray(Entry(name)));
            Assert.IsTrue(prepared.Value<bool>("success"), prepared.ToString());
            string id = (string)prepared["data"]["change_set_id"];
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));

            // When: one replacement stops at a durable boundary, retaining its backup.
            var applied = Command("apply_change_set", id);
            Assert.AreEqual("applying", (string)applied["data"]["phase"], applied.ToString());
            Assert.AreEqual("confirmed", (string)applied["data"]["files"][0]["state"]);
            string[] backups = Directory.GetFiles(folder, "*.bak", SearchOption.TopDirectoryOnly);
            Assert.AreEqual(1, backups.Length, "The replacement preimage must remain adjacent to the source until recovery.");
            Assert.LessOrEqual(Path.GetFileName(backups[0]).Length, 48, "Staging names must be bounded independently of the source basename.");
            Assert.LessOrEqual(backups[0].Length, Path.Combine(folder, name + ".cs").Length);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(backups[0]));

            // Then: another recorded domain can derive the same backup and restore it.
            SetSeam("_domainForTests", (Func<string>)(() => "second-domain"));
            var recovered = Command("recover_change_set", id);
            Assert.IsTrue(recovered.Value<bool>("success"), recovered.ToString());
            CollectionAssert.AreEqual(original, Bytes(name));
            Assert.IsEmpty(Directory.GetFiles(folder, "*.bak", SearchOption.TopDirectoryOnly));
        }

        [Test]
        public void Apply_StaleSecondSourceRefusesEveryWrite()
        {
            // Given: a prepared batch whose second source changes externally.
            string id = Prepare();
            byte[] first = Bytes("First");
            File.AppendAllText(Path.Combine(folder, "Second.cs"), "// external\n");
            byte[] external = Bytes("Second");
            // When: all-file admission checks run before application.
            var response = Command("apply_change_set", id);
            // Then: neither file is replaced and the failed precondition is explicit.
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("stale_precondition", (string)response["code"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
            CollectionAssert.AreEqual(external, Bytes("Second"));
        }

        [Test]
        public void Apply_WriteFailureRestoresPriorConfirmedSource()
        {
            // Given: a second-file write failure after the first atomic replacement.
            string id = Prepare();
            byte[] first = Bytes("First"),
                second = Bytes("Second");
            SetSeam(
                "_stageForTests",
                (Action<string, int>)(
                    (stage, index) =>
                    {
                        if (stage == "before_replace" && index == 1)
                            throw new IOException("owned write failure");
                    }
                )
            );
            // When: application fails and compensates eligible prior writes.
            var response = Command("apply_change_set", id);
            // Then: restored and never-applied outcomes are retained in original order.
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("compensated", (string)response["data"]["phase"]);
            Assert.AreEqual("restored", (string)response["data"]["files"][0]["state"]);
            Assert.AreEqual("not_applied", (string)response["data"]["files"][1]["state"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
            CollectionAssert.AreEqual(second, Bytes("Second"));
        }

        [Test]
        public void Apply_CompensationPreservesInterferingExternalWriter()
        {
            // Given: an external edit to the first candidate before the second write fails.
            string id = Prepare();
            string external = "class First { /* external owner */ }\n";
            SetSeam(
                "_stageForTests",
                (Action<string, int>)(
                    (stage, index) =>
                    {
                        if (stage == "before_replace" && index == 1)
                        {
                            File.WriteAllText(Path.Combine(folder, "First.cs"), external, new UTF8Encoding(false));
                            throw new IOException("owned write failure");
                        }
                    }
                )
            );
            // When: compensation compares current bytes before restoration.
            var response = Command("apply_change_set", id);
            // Then: external bytes survive and the retained manifest marks uncertainty.
            Assert.AreEqual("conflict", (string)response["data"]["phase"]);
            Assert.AreEqual("uncertain", (string)response["data"]["files"][0]["state"]);
            Assert.AreEqual(external, File.ReadAllText(Path.Combine(folder, "First.cs")));
            Assert.IsNotNull(Command("get_change_set", id)["data"]["recovery_manifest"]);
        }

        [Test]
        public void Apply_PostReplaceFailureStillRestoresTheCommittedSource()
        {
            // Given: an exception after File.Replace returned but before the next file.
            string id = Prepare();
            byte[] first = Bytes("First");
            SetSeam(
                "_stageForTests",
                (Action<string, int>)(
                    (stage, index) =>
                    {
                        if (stage == "after_replace" && index == 0)
                            throw new IOException("owned postcommit failure");
                    }
                )
            );
            // When: the failure is handled using retained replacement evidence.
            var response = Command("apply_change_set", id);
            // Then: the committed file is restored rather than mislabeled as never written.
            Assert.AreEqual("restored", (string)response["data"]["files"][0]["state"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
        }

        [Test]
        public void Recovery_NewDomainRetainsConfirmedOutcomesAndRestoresEligibleSources()
        {
            // Given: a durable boundary after the first commit, followed by a new domain identity.
            string id = Prepare();
            byte[] first = Bytes("First"),
                second = Bytes("Second");
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            var partial = Command("apply_change_set", id);
            Assert.AreEqual("applying", (string)partial["data"]["phase"]);
            SetSeam("_domainForTests", (Func<string>)(() => "second-domain"));
            var status = Command("get_change_set", id);
            Assert.IsTrue((bool)status["data"]["interrupted"]);
            Assert.AreEqual("confirmed", (string)status["data"]["files"][0]["state"]);
            // When: explicit recovery loads only the retained on-disk plan.
            var response = Command("recover_change_set", id);
            // Then: observed candidates are conditionally restored and untouched files stay original.
            Assert.AreEqual("compensated", (string)response["data"]["phase"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
            CollectionAssert.AreEqual(second, Bytes("Second"));
        }

        [Test]
        public void Recovery_NewDomainNeverRestoresOverChangedExternalBytes()
        {
            // Given: an interrupted plan and an external write after its first confirmed commit.
            string id = Prepare();
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            Command("apply_change_set", id);
            byte[] external = new UTF8Encoding(false).GetBytes("class First { /* external */ }\n");
            File.WriteAllBytes(Path.Combine(folder, "First.cs"), external);
            SetSeam("_domainForTests", (Func<string>)(() => "second-domain"));
            // When: recovery is requested in the replacement domain.
            var response = Command("recover_change_set", id);
            // Then: it reports uncertainty and preserves the external source.
            Assert.AreEqual("uncertain", (string)response["data"]["files"][0]["state"]);
            CollectionAssert.AreEqual(external, Bytes("First"));
        }

        [Test]
        public void Prepare_DuplicateTargetsRefuseWithoutWriting()
        {
            // Given: two entries targeting the same physical source.
            byte[] first = Bytes("First");
            // When: the duplicate plan is prepared.
            var response = Command("prepare_change_set", files: new JArray(Entry("First"), Entry("First")));
            // Then: duplicate ownership is refused before writes.
            Assert.AreEqual("duplicate_path", (string)response["code"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
        }

        [Test]
        public void Prepare_InvalidLaterCandidateDoesNotApplyEarlierSource()
        {
            // Given: a valid first candidate and a structurally invalid second candidate.
            byte[] first = Bytes("First");
            var second = Entry("Second");
            second["contents"] = "class Second {";
            // When: all candidates undergo the existing per-file final validation.
            var response = Command("prepare_change_set", files: new JArray(Entry("First"), second));
            // Then: no source has been written and the validation refusal is explicit.
            Assert.AreEqual("validation_failed", (string)response["code"]);
            CollectionAssert.AreEqual(first, Bytes("First"));
        }
    }
}
