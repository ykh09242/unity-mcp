using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ManageAssetLabelTests
    {
        private string _folder;
        private string _first;
        private string _second;
        private string _third;
        private Type _helper;
        private Action<string> _previousObserved;
        private Action<int, string> _previousBefore;
        private Action<string> _previousAfter;
        private int _writes;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/AssetLabelTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _first = CreateText("first");
            _second = CreateText("second");
            _third = CreateText("third");
            _helper = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.AssetLabels", true);
            _previousObserved = (Action<string>)Field("OperationObserved").GetValue(null);
            _previousBefore = (Action<int, string>)Field("BeforeCommit").GetValue(null);
            _previousAfter = (Action<string>)Field("AfterSetLabels").GetValue(null);
            _writes = 0;
            Field("OperationObserved")
                .SetValue(
                    null,
                    new Action<string>(op =>
                    {
                        if (op == "set_labels")
                            _writes++;
                    })
                );
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (_helper != null)
                {
                    Field("OperationObserved").SetValue(null, _previousObserved);
                    Field("BeforeCommit").SetValue(null, _previousBefore);
                    Field("AfterSetLabels").SetValue(null, _previousAfter);
                }
            }
            finally
            {
                if (!string.IsNullOrEmpty(_folder))
                    AssetDatabase.DeleteAsset(_folder);
            }
        }

        private FieldInfo Field(string name) => _helper.GetField(name, BindingFlags.Static | BindingFlags.NonPublic);

        private string CreateText(string name)
        {
            string path = _folder + "/" + name + ".txt";
            File.WriteAllText(path, "owned label fixture " + name);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        private static JObject Call(JObject request) => JObject.FromObject(ManageAsset.HandleCommand(request));

        private static JObject Get(string path) => Call(new JObject { ["action"] = "get_labels", ["path"] = path });

        private static JObject Row(JObject result, int index = 0) => (JObject)result["data"]["results"][index];

        private static string Revision(string path)
        {
            var result = Get(path);
            Assert.That((bool)result["success"], Is.True, result.ToString());
            string revision = (string)Row(result)["revision"];
            Assert.That(revision, Does.Match("^mcp-al-v1_[0-9a-f]{32}_[0-9a-f]{64}_[0-9a-f]{64}$"));
            Assert.That(revision.Length, Is.EqualTo(172));
            return revision;
        }

        private static JObject Change(string action, string path, string revision, params string[] labels) =>
            Call(
                new JObject
                {
                    ["action"] = action,
                    ["path"] = path,
                    ["labelRevision"] = revision,
                    ["labels"] = new JArray(labels),
                }
            );

        private static string[] NativeLabels(string path) => AssetDatabase.GetLabels(AssetDatabase.LoadMainAssetAtPath(path));

        private static void NativeSet(string path, params string[] labels) => AssetDatabase.SetLabels(AssetDatabase.LoadMainAssetAtPath(path), labels);

        private static JArray Targets(params string[] paths) =>
            new JArray(paths.Select(path => new JObject { ["path"] = path, ["revision"] = Revision(path) }));

        [Test]
        public void NativeApiControl_ObservesExactSpellingCaseAndPersistedMeta()
        {
            // Given: unique labels avoid global spelling already present elsewhere in the project.
            string label = "Case_" + Guid.NewGuid().ToString("N");
            byte[] before = File.ReadAllBytes(_first + ".meta");
            // When: the actual API writes, reloads and reads the owned imported object.
            NativeSet(_first, label, "Unicode_한글");
            string[] observed = NativeLabels(_first);
            TestContext.Out.WriteLine("native_labels=" + JsonConvert.SerializeObject(observed));
            TestContext.Out.WriteLine("native_meta=" + File.ReadAllText(_first + ".meta"));
            // Then: control witnesses spelling and immediate raw persistence; no expected normalization.
            Assert.That(observed, Is.EquivalentTo(new[] { label, "Unicode_한글" }));
            Assert.That(File.ReadAllBytes(_first + ".meta"), Is.Not.EqualTo(before));
            NativeSet(_first, label.ToLowerInvariant());
            string[] lowerObserved = NativeLabels(_first);
            TestContext.Out.WriteLine("native_case_labels=" + JsonConvert.SerializeObject(lowerObserved));
            Assert.That(lowerObserved, Has.Length.EqualTo(1), "Raw spelling above is a native API observation, not a normalization guarantee.");
        }

        [Test]
        public void GetLabels_ReturnsBoundedRevisionAndNeverWrites()
        {
            // Given: an existing imported asset with an unrelated label.
            NativeSet(_first, "keep");
            byte[] meta = File.ReadAllBytes(_first + ".meta");
            // When: reading through manage_asset.
            var result = Get(_first);
            // Then: the complete set and raw-meta revision are returned without writing.
            Assert.That((bool)result["success"], Is.True, result.ToString());
            Assert.That(Row(result)["labels"].Values<string>(), Is.EqualTo(new[] { "keep" }));
            Assert.That((string)Row(result)["guid"], Is.EqualTo(AssetDatabase.AssetPathToGUID(_first)));
            Assert.That((string)Row(result)["revision"], Has.Length.EqualTo(172));
            using (var sha = SHA256.Create())
            {
                string metaDigest = BitConverter.ToString(sha.ComputeHash(meta)).Replace("-", "").ToLowerInvariant();
                Assert.That(((string)Row(result)["revision"]).Substring(43, 64), Is.EqualTo(metaDigest));
                string labelDigest = BitConverter
                    .ToString(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes("[\"keep\"]")))
                    .Replace("-", "")
                    .ToLowerInvariant();
                Assert.That(((string)Row(result)["revision"]).Substring(108, 64), Is.EqualTo(labelDigest));
            }
            Assert.That((bool)Row(result)["read_only"], Is.False);
            Assert.That(File.ReadAllBytes(_first + ".meta"), Is.EqualTo(meta));
            Assert.That(_writes, Is.Zero);
            Assert.That(System.Text.Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)), Is.LessThan(1024 * 1024));
        }

        [TestCase("add_labels", new[] { "keep", "old", "new" })]
        [TestCase("remove_labels", new[] { "keep", "old" })]
        public void DeltaLabels_PreservesUnrelatedLabelsGuidContentAndImporter(string action, string[] expected)
        {
            // Given: unrelated labels, source bytes and importer metadata belong to the user.
            NativeSet(_first, "keep", "old");
            var importer = AssetImporter.GetAtPath(_first);
            importer.userData = "unrelated owned metadata";
            importer.SaveAndReimport();
            byte[] content = File.ReadAllBytes(_first);
            string guid = AssetDatabase.AssetPathToGUID(_first);
            // When: adding a new exact label or removing an absent one.
            var result = Change(action, _first, Revision(_first), "new");
            // Then: complement and every unrelated field survive.
            Assert.That((bool)result["success"], Is.True, result.ToString());
            Assert.That(NativeLabels(_first), Is.EquivalentTo(expected));
            Assert.That(AssetDatabase.AssetPathToGUID(_first), Is.EqualTo(guid));
            Assert.That(File.ReadAllBytes(_first), Is.EqualTo(content));
            Assert.That(AssetImporter.GetAtPath(_first).userData, Is.EqualTo("unrelated owned metadata"));
        }

        [Test]
        public void SetLabels_EmptyListClearsRatherThanOmittingOperation()
        {
            NativeSet(_first, "keep", "old"); // Given.
            var result = Change("set_labels", _first, Revision(_first)); // When.
            Assert.That((bool)result["success"], Is.True, result.ToString()); // Then.
            Assert.That(NativeLabels(_first), Is.Empty);
            Assert.That(_writes, Is.EqualTo(1));
        }

        [Test]
        public void RemoveExistingLabel_PreservesAnotherLabel()
        {
            // Given: both the removed label and an unrelated label actually exist.
            NativeSet(_first, "keep", "remove_me");
            Assert.That(NativeLabels(_first), Is.EquivalentTo(new[] { "keep", "remove_me" }));
            string revision = Revision(_first);
            // When: removing the present exact label through the public handler.
            var result = Change("remove_labels", _first, revision, "remove_me");
            // Then: the removal is confirmed and the unrelated label remains.
            Assert.That((bool)result["success"], Is.True, result.ToString());
            Assert.That((string)Row(result)["status"], Is.EqualTo("changed"));
            Assert.That(NativeLabels(_first), Is.EqualTo(new[] { "keep" }));
            Assert.That(Row(result)["labels"].Values<string>(), Is.EqualTo(new[] { "keep" }));
            Assert.That(_writes, Is.EqualTo(1));
        }

        [Test]
        public void CaseOnlyWrite_RequiresExactReadbackOrReportsUnknown()
        {
            string upper = "Case_" + Guid.NewGuid().ToString("N");
            NativeSet(_first, upper);
            string requested = upper.ToLowerInvariant();
            var result = Change("set_labels", _first, Revision(_first), requested);
            string[] actual = NativeLabels(_first);
            TestContext.Out.WriteLine("case_only_readback=" + JsonConvert.SerializeObject(actual));
            if (actual.SequenceEqual(new[] { requested }, StringComparer.Ordinal))
                Assert.That((bool)result["success"], Is.True, result.ToString());
            else
            {
                Assert.That((string)result["code"], Is.EqualTo("asset_label_outcome_unknown"));
                Assert.That((string)Row(result)["status"], Is.EqualTo("outcome_unknown"));
            }
        }

        [TestCase("set_labels", new[] { "keep", "keep" })]
        [TestCase("add_labels", new string[] { })]
        [TestCase("remove_labels", new string[] { })]
        public void NoOp_DoesNotWriteOrChangeMeta(string action, string[] labels)
        {
            NativeSet(_first, "keep");
            byte[] meta = File.ReadAllBytes(_first + ".meta"); // Given.
            var search = new JObject
            {
                ["action"] = "search_metadata_v1",
                ["path"] = _folder,
                ["pageSize"] = 10,
            };
            var warm = Call(search);
            Assert.That((bool)warm["success"], Is.True, warm.ToString());
            search["searchSnapshot"] = (string)warm["data"]["searchSnapshot"];
            var result = Change(action, _first, Revision(_first), labels); // When.
            Assert.That((bool)result["success"], Is.True, result.ToString()); // Then.
            Assert.That((string)Row(result)["status"], Is.EqualTo("unchanged"));
            Assert.That(_writes, Is.Zero);
            Assert.That(File.ReadAllBytes(_first + ".meta"), Is.EqualTo(meta));
            var continued = Call(search);
            Assert.That((bool)continued["success"], Is.True, continued.ToString());
        }

        [TestCase(false, "labels_changed")]
        [TestCase(true, "metadata_changed_without_label_set_change")]
        public void StaleRevision_DistinguishesLabelAndOtherMetadata(bool metadataOnly, string reason)
        {
            NativeSet(_first, "keep");
            string revision = Revision(_first);
            if (metadataOnly)
            {
                var importer = AssetImporter.GetAtPath(_first);
                importer.userData = "external metadata change";
                importer.SaveAndReimport();
            }
            else
                NativeSet(_first, "external");
            byte[] external = File.ReadAllBytes(_first + ".meta"); // Given.
            var result = Change("add_labels", _first, revision, "new"); // When.
            Assert.That((bool)result["success"], Is.False, result.ToString()); // Then.
            Assert.That((string)result["code"], Is.EqualTo("asset_label_conflict"));
            Assert.That((string)result["data"]["reason"], Is.EqualTo(reason));
            Assert.That(File.ReadAllBytes(_first + ".meta"), Is.EqualTo(external));
            Assert.That(_writes, Is.Zero);
        }

        [Test]
        public void WholeBatchAdmission_RejectsStaleSecondBeforeFirstWrite()
        {
            var targets = Targets(_first, _second);
            NativeSet(_second, "concurrent"); // Given.
            var result = Call(
                new JObject
                {
                    ["action"] = "add_labels",
                    ["labelTargets"] = targets,
                    ["labels"] = new JArray("new"),
                }
            ); // When.
            Assert.That((bool)result["success"], Is.False, result.ToString()); // Then.
            Assert.That(_writes, Is.Zero);
            Assert.That(NativeLabels(_first), Is.Empty);
        }

        [Test]
        public void LateConflict_KeepsEarlierConfirmedWriteAndMarksRemainingNotAttempted()
        {
            var targets = Targets(_first, _second, _third);
            Field("BeforeCommit")
                .SetValue(
                    null,
                    new Action<int, string>(
                        (index, path) =>
                        {
                            if (index == 1)
                                NativeSet(path, "external");
                        }
                    )
                );
            try
            {
                var result = Call(
                    new JObject
                    {
                        ["action"] = "add_labels",
                        ["labelTargets"] = targets,
                        ["labels"] = new JArray("new"),
                    }
                );
                Assert.That((bool)result["success"], Is.False, result.ToString());
                Assert.That((bool)result["data"]["partial"], Is.True);
                Assert.That((string)Row(result, 0)["status"], Is.EqualTo("changed"));
                Assert.That((string)Row(result, 1)["status"], Is.EqualTo("conflict"));
                Assert.That((string)Row(result, 2)["status"], Is.EqualTo("not_attempted"));
                Assert.That(NativeLabels(_first), Is.EqualTo(new[] { "new" }));
                Assert.That(NativeLabels(_second), Is.EqualTo(new[] { "external" }));
                Assert.That(NativeLabels(_third), Is.Empty);
                Assert.That(_writes, Is.EqualTo(1));
            }
            finally
            {
                Field("BeforeCommit").SetValue(null, _previousBefore);
            }
        }

        [Test]
        public void ExceptionAfterSetLabels_IsUnknownAndDoesNotRollBack()
        {
            string revision = Revision(_first);
            var search = new JObject
            {
                ["action"] = "search_metadata_v1",
                ["path"] = _folder,
                ["pageSize"] = 10,
            };
            var warm = Call(search);
            Assert.That((bool)warm["success"], Is.True, warm.ToString());
            search["searchSnapshot"] = (string)warm["data"]["searchSnapshot"];
            Field("AfterSetLabels").SetValue(null, new Action<string>(_ => throw new IOException("owned after-write fault")));
            try
            {
                var result = Change("add_labels", _first, revision, "new");
                Assert.That((bool)result["success"], Is.False, result.ToString());
                Assert.That((string)result["code"], Is.EqualTo("asset_label_outcome_unknown"));
                Assert.That((string)Row(result)["status"], Is.EqualTo("outcome_unknown"));
                Assert.That(NativeLabels(_first), Is.EqualTo(new[] { "new" }));
                Assert.That(_writes, Is.EqualTo(1));
                Assert.That((string)Call(search)["code"], Is.EqualTo("stale_search_snapshot"));
            }
            finally
            {
                Field("AfterSetLabels").SetValue(null, _previousAfter);
            }
        }

        [Test]
        public void LabelWrite_InvalidatesWarmMetadataSnapshotWithoutRefresh()
        {
            string unique = "new_" + Guid.NewGuid().ToString("N");
            var request = new JObject
            {
                ["action"] = "search_metadata_v1",
                ["path"] = _folder,
                ["searchPattern"] = "l:" + unique,
                ["pageSize"] = 10,
            };
            var first = Call(request);
            Assert.That((bool)first["success"], Is.True, first.ToString());
            string token = (string)first["data"]["searchSnapshot"];
            var changed = Change("add_labels", _first, Revision(_first), unique);
            Assert.That((bool)changed["success"], Is.True, changed.ToString());
            request["searchSnapshot"] = token;
            var stale = Call(request);
            Assert.That((string)stale["code"], Is.EqualTo("stale_search_snapshot"));
            request.Remove("searchSnapshot");
            var fresh = Call(request);
            Assert.That((bool)fresh["success"], Is.True, fresh.ToString());
            Assert.That((int)fresh["data"]["totalAssets"], Is.EqualTo(1));
        }

        [Test]
        public void FolderLabels_AffectOnlyTheFolder()
        {
            var result = Change("add_labels", _folder, Revision(_folder), "folder_only");
            Assert.That((bool)result["success"], Is.True, result.ToString());
            Assert.That(NativeLabels(_folder), Is.EqualTo(new[] { "folder_only" }));
            Assert.That(NativeLabels(_first), Is.Empty);
        }

        [Test]
        public void PackageGet_IsReadOnlyAndEveryMutationRefusedBeforeConsent()
        {
            const string packagePath = "Packages/com.ykh09242.unity-mcp/package.json";
            Assert.That(AssetDatabase.LoadMainAssetAtPath(packagePath), Is.Not.Null, "Owned profile must install this package prerequisite.");
            var get = Get(packagePath);
            Assert.That((bool)get["success"], Is.True, get.ToString());
            Assert.That((bool)Row(get)["read_only"], Is.True);
            var revision = Row(get)["revision"];
            string wire = JsonConvert.SerializeObject(get);
            TestContext.WriteLine("package_revision_in_memory_type=" + (revision == null ? "missing" : revision.Type.ToString()));
            TestContext.WriteLine("package_read_only_get_wire=" + wire);
            Assert.That(revision, Is.Not.Null);
            Assert.That((string)revision, Is.Null);
            Assert.That(Row(JObject.Parse(wire))["revision"].Type, Is.EqualTo(JTokenType.Null));
            var result = Change("add_labels", packagePath, "mcp-al-v1_" + new string('a', 32) + "_" + new string('b', 64) + "_" + new string('c', 64));
            Assert.That((string)result["code"], Is.EqualTo("asset_labels_read_only"));
            Assert.That(_writes, Is.Zero);
        }

        [TestCase("Assets")]
        [TestCase("Assets/../outside")]
        [TestCase("Resources/unity_builtin_extra")]
        public void InvalidTarget_RefusesWithoutWrites(string path)
        {
            var result = Get(path);
            Assert.That((bool)result["success"], Is.False, result.ToString());
            Assert.That(_writes, Is.Zero);
        }

        [Test]
        public void MetaTarget_RefusesRatherThanEditingTheMetaAsAnAsset()
        {
            var result = Get(_first + ".meta");
            Assert.That((bool)result["success"], Is.False, result.ToString());
            Assert.That(_writes, Is.Zero);
        }

        [Test]
        public void CanonicalAliasBatch_RefusesWithoutWrites()
        {
            var result = Call(
                new JObject
                {
                    ["action"] = "add_labels",
                    ["labels"] = new JArray("new"),
                    ["labelTargets"] = new JArray(
                        new JObject { ["path"] = _first, ["revision"] = Revision(_first) },
                        new JObject { ["path"] = _first.Substring(7), ["revision"] = Revision(_first) }
                    ),
                }
            );
            Assert.That((string)result["code"], Is.EqualTo("invalid_asset_labels"));
            Assert.That(_writes, Is.Zero);
            Assert.That(NativeLabels(_first), Is.Empty);
        }

        [Test]
        public void DifferentGuidRevision_IsAnIdentityConflictWithoutWrites()
        {
            var result = Change("add_labels", _first, Revision(_second), "new");
            Assert.That((string)result["code"], Is.EqualTo("asset_label_conflict"));
            Assert.That((string)result["data"]["reason"], Is.EqualTo("asset_identity_changed"));
            Assert.That(_writes, Is.Zero);
        }

        [Test]
        public void ReadOnlyMeta_IsRefusedWithoutChangingAttributes()
        {
            string revision = Revision(_first);
            string meta = _first + ".meta";
            FileAttributes original = File.GetAttributes(meta);
            try
            {
                File.SetAttributes(meta, original | FileAttributes.ReadOnly);
                var result = Change("add_labels", _first, revision, "new");
                Assert.That((string)result["code"], Is.EqualTo("asset_labels_read_only"));
                Assert.That(File.GetAttributes(meta), Is.EqualTo(original | FileAttributes.ReadOnly));
                Assert.That(_writes, Is.Zero);
            }
            finally
            {
                File.SetAttributes(meta, original);
            }
        }

        [TestCase(32, true)]
        [TestCase(33, false)]
        public void TargetCount_IsBoundedBeforeLoads(int count, bool expected)
        {
            var targets = new JArray(
                Enumerable.Range(0, count).Select(i => new JObject { ["path"] = i < 3 ? new[] { _first, _second, _third }[i] : CreateText("batch" + i) })
            );
            int observations = 0;
            Field("OperationObserved").SetValue(null, new Action<string>(_ => observations++));
            try
            {
                var result = Call(new JObject { ["action"] = "get_labels", ["labelTargets"] = targets });
                Assert.That((bool)result["success"], Is.EqualTo(expected), result.ToString());
                if (expected)
                    Assert.That(((JArray)result["data"]["results"]).Count, Is.EqualTo(32));
                else
                    Assert.That(observations, Is.Zero);
            }
            finally
            {
                Field("OperationObserved").SetValue(null, _previousObserved);
            }
        }

        [TestCase("", 1)]
        [TestCase(" x", 1)]
        [TestCase("x\n", 1)]
        [TestCase("x", 129)]
        public void InvalidLabels_RefuseBeforeObjectOrMetaObservation(string label, int count)
        {
            int observations = 0;
            Field("OperationObserved").SetValue(null, new Action<string>(_ => observations++));
            try
            {
                var result = Call(
                    new JObject
                    {
                        ["action"] = "set_labels",
                        ["path"] = _first,
                        ["labelRevision"] = RevisionLiteral(),
                        ["labels"] = new JArray(Enumerable.Repeat(label, count)),
                    }
                );
                Assert.That((string)result["code"], Is.EqualTo("invalid_asset_labels"));
                Assert.That(observations, Is.Zero);
            }
            finally
            {
                Field("OperationObserved").SetValue(null, _previousObserved);
            }
        }

        [Test]
        public void OversizedMeta_RefusesWithoutTruncationOrWrite()
        {
            byte[] original = File.ReadAllBytes(_first + ".meta");
            try
            {
                File.WriteAllText(_first + ".meta", new string(' ', 1024 * 1024 + 1));
                var result = Get(_first);
                Assert.That((string)result["code"], Is.EqualTo("asset_labels_limit_exceeded"));
                Assert.That(_writes, Is.Zero);
            }
            finally
            {
                File.WriteAllBytes(_first + ".meta", original);
            }
        }

        [Test]
        public void AggregateObservedLabels_RefuseWithoutReturningTruncatedSuccess()
        {
            string[] labels = Enumerable.Range(0, 128).Select(i => i.ToString("D3") + new string('x', 253)).ToArray();
            NativeSet(_first, labels);
            NativeSet(_second, labels);
            NativeSet(_third, labels);
            var result = Call(
                new JObject
                {
                    ["action"] = "get_labels",
                    ["labelTargets"] = new JArray(new JObject { ["path"] = _first }, new JObject { ["path"] = _second }, new JObject { ["path"] = _third }),
                }
            );
            Assert.That((bool)result["success"], Is.False, result.ToString());
            Assert.That((string)result["code"], Is.EqualTo("asset_labels_limit_exceeded"));
            Assert.That(_writes, Is.Zero);
            Assert.That(System.Text.Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)), Is.LessThan(1024 * 1024));
        }

        [Test]
        public void AggregateMetaRead_RefusesBeyondFourMiBAndRestoresOwnedMeta()
        {
            string[] paths = Enumerable.Range(0, 5).Select(i => CreateText("large" + i)).ToArray();
            byte[][] original = paths.Select(path => File.ReadAllBytes(path + ".meta")).ToArray();
            try
            {
                for (int i = 0; i < paths.Length; i++)
                {
                    // Native object/GUID already imported. Padding is owned, observed raw metadata only.
                    byte[] padded = new byte[900 * 1024];
                    Array.Copy(original[i], padded, original[i].Length);
                    for (int j = original[i].Length; j < padded.Length; j++)
                        padded[j] = (byte)' ';
                    File.WriteAllBytes(paths[i] + ".meta", padded);
                }
                var targets = new JArray(paths.Select(path => new JObject { ["path"] = path }));
                var result = Call(new JObject { ["action"] = "get_labels", ["labelTargets"] = targets });
                Assert.That((string)result["code"], Is.EqualTo("asset_labels_limit_exceeded"));
                Assert.That(_writes, Is.Zero);
            }
            finally
            {
                for (int i = 0; i < paths.Length; i++)
                    File.WriteAllBytes(paths[i] + ".meta", original[i]);
            }
        }

        private static string RevisionLiteral() => "mcp-al-v1_" + new string('a', 32) + "_" + new string('b', 64) + "_" + new string('c', 64);
    }
}
