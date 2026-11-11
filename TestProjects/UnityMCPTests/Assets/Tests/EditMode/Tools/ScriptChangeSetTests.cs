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
        public void QualitySelectionReadsPreparedAndAppliedBytesWithoutWriting()
        {
            string id = Prepare();
            string manifest = Path.Combine(storage, id, "manifest.json");
            byte[] original = Bytes("First"),
                savedManifest = File.ReadAllBytes(manifest);
            Seed("UnrelatedDirty", false, "\n");
            var selected = MCPForUnity.Editor.Services.ScriptQuality.ScriptSelection.Resolve(new JObject { ["change_set_id"] = id });
            Assert.That(selected.Paths, Is.EqualTo(new[] { relative + "/First.cs", relative + "/Second.cs" }));
            Assert.That(Bytes("First"), Is.EqualTo(original));
            Assert.That(File.ReadAllBytes(manifest), Is.EqualTo(savedManifest));
            Assert.That(File.Exists(Path.Combine(storage, id, "lease")), Is.False, "A diagnostic read must not create a lease file.");
            Assert.That((bool)Command("apply_change_set", id)["success"], Is.True);
            byte[] applied = Bytes("First");
            selected = MCPForUnity.Editor.Services.ScriptQuality.ScriptSelection.Resolve(new JObject { ["change_set_id"] = id });
            Assert.That(selected.Files[0].Text, Is.EqualTo(Candidate("First")));
            Assert.That(Bytes("First"), Is.EqualTo(applied));
        }

        [Test]
        public void QualitySelectionRefusesExternallyChangedChangeSetSources()
        {
            string id = Prepare();
            File.AppendAllText(Path.Combine(folder, "First.cs"), "// external");
            Assert.Throws<InvalidOperationException>(() =>
                MCPForUnity.Editor.Services.ScriptQuality.ScriptSelection.Resolve(new JObject { ["change_set_id"] = id })
            );
        }

        [Test]
        public void LargeChangeSetPreservesEverySourceThroughPreparationAndApply()
        {
            var files = new JArray();
            for (int index = 0; index < 40; index++)
            {
                string name = "Bulk" + index;
                Seed(name, false, "\n");
                if (index == 0)
                    File.AppendAllText(Path.Combine(folder, name + ".cs"), "/*" + new string('x', 700000) + "*/");
                JObject entry = Entry(name);
                if (index == 0)
                    entry["contents"] = Candidate(name) + "/*" + new string('y', 700000) + "*/";
                files.Add(entry);
            }
            JObject prepared = Command("prepare_change_set", files: files);
            Assert.IsTrue(prepared.Value<bool>("success"), prepared.ToString());
            string id = prepared["data"].Value<string>("change_set_id");
            JObject applied = Command("apply_change_set", id);
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual(40, ((JArray)applied["data"]["files"]).Count);
            for (int index = 0; index < 40; index++)
                Assert.AreEqual((string)files[index]["contents"], File.ReadAllText(Path.Combine(folder, "Bulk" + index + ".cs")));
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
        public void ApplyAndRecoveryPreserveUnattemptedExternalCandidate()
        {
            string id = Prepare();
            byte[] first = Bytes("First");
            byte[] external = new UTF8Encoding(false).GetBytes(Candidate("Second"));
            SetSeam(
                "_stageForTests",
                (Action<string, int>)(
                    (stage, index) =>
                    {
                        if (stage == "before_replace" && index == 1)
                            File.WriteAllBytes(Path.Combine(folder, "Second.cs"), external);
                    }
                )
            );

            JObject applied = Command("apply_change_set", id);
            CollectionAssert.AreEqual(external, Bytes("Second"), "Candidate equality does not give this operation ownership of an external write.");
            CollectionAssert.AreEqual(first, Bytes("First"));
            Assert.AreEqual("change_set_conflict", (string)applied["code"]);
            Assert.AreEqual("restored", (string)applied["data"]["files"][0]["state"]);
            SetSeam("_stageForTests", null);
            SetSeam("_domainForTests", (Func<string>)(() => "recovery-domain"));
            JObject recovered = Command("recover_change_set", id);
            Assert.AreEqual("change_set_conflict", (string)recovered["code"]);
            CollectionAssert.AreEqual(external, Bytes("Second"));
            CollectionAssert.AreEqual(first, Bytes("First"));
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void RecoveryRequiresDisplacedPreimageForReplacement(bool retainPreimage, bool replaceReturned)
        {
            byte[] original = Bytes("First");
            string id = Prepare();
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            Command("apply_change_set", id);
            byte[] candidate = Bytes("First");
            string manifestPath = Path.Combine(storage, id, "manifest.json");
            JObject manifest = JObject.Parse(File.ReadAllText(manifestPath));
            manifest["Files"][0]["ReplaceReturned"] = replaceReturned;
            manifest["Files"][0]["Backup"] = null;
            manifest["Files"][0]["State"] = "writing";
            File.WriteAllText(manifestPath, manifest.ToString(), new UTF8Encoding(false));
            string backup = Directory.GetFiles(folder, "*.bak").Single();
            if (retainPreimage)
                File.Move(backup, Path.ChangeExtension(backup, ".tmp"));
            else
                File.Delete(backup);
            SetSeam("_domainForTests", (Func<string>)(() => "recovery-domain"));

            for (int attempt = 0; attempt < 2; attempt++)
            {
                JObject recovered = Command("recover_change_set", id);
                Assert.AreEqual(retainPreimage, recovered.Value<bool>("success"), recovered.ToString());
                CollectionAssert.AreEqual(retainPreimage ? original : candidate, Bytes("First"));
            }
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
        public void RecoveryPublishesOriginalAtomicallyWhileExistingReaderKeepsCandidate()
        {
            byte[] original = Bytes("First");
            string id = Prepare();
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            Command("apply_change_set", id);
            byte[] candidate = Bytes("First");
            using var reader = new FileStream(Path.Combine(folder, "First.cs"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            JObject recovered = Command("recover_change_set", id);
            Assert.IsTrue(recovered.Value<bool>("success"), recovered.ToString());
            CollectionAssert.AreEqual(original, Bytes("First"));
            using var captured = new MemoryStream();
            reader.CopyTo(captured);
            CollectionAssert.AreEqual(candidate, captured.ToArray(), "Compensation must replace a complete file, never truncate the candidate inode.");
        }

        [Test]
        public void RecoveryAfterInterruptedExchangePreservesUnverifiedStagePreimage()
        {
            string id = Prepare();
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            Command("apply_change_set", id);
            byte[] candidate = Bytes("First");
            string backup = Directory.GetFiles(folder, "*.bak").Single();
            string stage = Path.ChangeExtension(backup, ".tmp");
            File.Move(backup, stage);
            File.WriteAllText(stage, "external displaced bytes");
            SetSeam("_domainForTests", (Func<string>)(() => "second-domain"));

            JObject recovered = Command("recover_change_set", id);
            Assert.IsFalse(recovered.Value<bool>("success"), recovered.ToString());
            Assert.AreEqual("uncertain", (string)recovered["data"]["files"][0]["state"]);
            CollectionAssert.AreEqual(candidate, Bytes("First"), "Unverified displaced preimages require explicit reconciliation.");
            Assert.AreEqual("external displaced bytes", File.ReadAllText(stage));
        }

        [Test]
        public void RecoveryRetriesInterruptedCompensationUsingTheSameOriginal()
        {
            byte[] original = Bytes("First");
            string id = Prepare();
            SetSeam("_continueForTests", (Func<int, bool>)(_ => false));
            Command("apply_change_set", id);
            byte[] candidate = Bytes("First");
            SetSeam(
                "_stageForTests",
                (Action<string, int>)(
                    (stage, index) =>
                    {
                        if (stage == "before_restore" && index == 0)
                            throw new IOException("Owned interrupted compensation");
                    }
                )
            );
            JObject interrupted = Command("recover_change_set", id);
            Assert.IsFalse(interrupted.Value<bool>("success"));
            CollectionAssert.AreEqual(candidate, Bytes("First"));
            SetSeam("_stageForTests", null);
            SetSeam("_domainForTests", (Func<string>)(() => "recovery-domain"));
            JObject recovered = Command("recover_change_set", id);
            Assert.IsTrue(recovered.Value<bool>("success"), recovered.ToString());
            CollectionAssert.AreEqual(original, Bytes("First"));
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
