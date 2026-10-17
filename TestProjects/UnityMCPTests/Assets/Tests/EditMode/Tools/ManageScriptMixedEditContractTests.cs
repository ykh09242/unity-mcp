using System;
using System.Collections.Generic;
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
    [Parallelizable(ParallelScope.None)]
    public class ManageScriptMixedEditContractTests
    {
        private const string Original = "public class OrderedProbe\n{\n    public void Existing() {}\n}\n";
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        private sealed class Probe
        {
            public string Folder;
            public string FullPath;
            public bool Bom;
            public bool Consent = true;
            public bool Enabled = true;
            public int RefreshCount = 0;
            public int ReplaceCount = 0;
            public bool ThrowRefresh;
            public Action<string> CommitStage;
            public Action<JObject> BeforeStage;

            public JObject Command(string action)
            {
                return new JObject
                {
                    ["action"] = action,
                    ["name"] = "OrderedProbe",
                    ["path"] = Folder,
                    ["options"] = new JObject
                    {
                        ["validate"] = "standard",
                        ["refresh"] = "debounced",
                        ["applyMode"] = "sequential",
                    },
                };
            }

            public JObject Snapshot()
            {
                return Invoke(Command("preview_script_buffer"));
            }

            public JObject Stage(JObject snapshot, string current, JObject edit, bool final)
            {
                var data = snapshot["data"];
                var command = Command("preview_script_buffer");
                command["original_sha256"] = data["original_sha256"];
                command["original_bytes_sha256"] = data["original_bytes_sha256"];
                command["original_bom"] = Bom;
                command["bom"] = Bom;
                command["source_contents"] = current;
                command["source_sha256"] = Hash(Bytes(current, false));
                command["source_bytes_sha256"] = Hash(Bytes(current, Bom));
                command["validate_final"] = final;
                if (edit != null)
                    command["edit"] = edit;
                BeforeStage?.Invoke(command);
                return Invoke(command);
            }

            public JObject Apply(JObject preview)
            {
                var command = Command("apply_prepared_script");
                command["original_bytes_sha256"] = preview["data"]["original_bytes_sha256"];
                command["candidate_bytes_sha256"] = preview["data"]["candidate_bytes_sha256"];
                command["candidate_contents"] = preview["data"]["new_contents"];
                command["bom"] = Bom;
                return Invoke(command);
            }
        }

        private static JObject Invoke(JObject command)
        {
            return JObject.FromObject(ManageScript.HandleCommand(command));
        }

        private static byte[] Bytes(string text, bool bom)
        {
            var payload = Utf8.GetBytes(text);
            if (!bom)
                return payload;
            var bytes = new byte[payload.Length + 3];
            bytes[0] = 0xef;
            bytes[1] = 0xbb;
            bytes[2] = 0xbf;
            Buffer.BlockCopy(payload, 0, bytes, 3, payload.Length);
            return bytes;
        }

        private static string Hash(byte[] bytes)
        {
            using var algorithm = SHA256.Create();
            return BitConverter.ToString(algorithm.ComputeHash(bytes)).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static JObject Replacement(string method = "Existing", string replacement = "public void Existing() { int value = 1; }")
        {
            return new JObject
            {
                ["op"] = "replace_method",
                ["className"] = "OrderedProbe",
                ["methodName"] = method,
                ["replacement"] = replacement,
            };
        }

        private static void WithProbe(Action<Probe> body, bool bom = false)
        {
            var probe = new Probe { Bom = bom, Folder = "Assets/OrderedScriptContract_" + Guid.NewGuid().ToString("N") };
            probe.FullPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), probe.Folder, "OrderedProbe.cs");
            var saved = new Dictionary<FieldInfo, object>();
            try
            {
                var seams = new Dictionary<string, Delegate>
                {
                    ["_explicitConsentForTests"] = new Func<bool>(() => probe.Consent),
                    ["_parentEnabledForTests"] = new Func<bool>(() => probe.Enabled),
                    ["_preparedRefreshForTests"] = new Action<string, bool>(
                        (path, immediate) =>
                        {
                            Assert.AreEqual(probe.Folder + "/OrderedProbe.cs", path);
                            probe.RefreshCount++;
                            if (probe.ThrowRefresh)
                                throw new IOException("Owned refresh fault");
                        }
                    ),
                    ["_preparedCommitStageForTests"] = new Action<string>(stage =>
                    {
                        if (stage == "after_replace")
                            probe.ReplaceCount++;
                        probe.CommitStage?.Invoke(stage);
                    }),
                };
                foreach (var seam in seams)
                {
                    var field = typeof(ManageScript).GetField(seam.Key, BindingFlags.Static | BindingFlags.NonPublic);
                    Assert.NotNull(field, "Missing ordered native facility: " + seam.Key);
                    saved.Add(field, field.GetValue(null));
                    field.SetValue(null, seam.Value);
                }
                Directory.CreateDirectory(Path.GetDirectoryName(probe.FullPath));
                File.WriteAllBytes(probe.FullPath, Bytes(Original, bom));
                body(probe);
            }
            finally
            {
                try
                {
                    string folder = Path.GetDirectoryName(probe.FullPath);
                    Assert.IsTrue(
                        Path.GetFullPath(folder)
                            .StartsWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                    );
                    if (Directory.Exists(folder))
                        Directory.Delete(folder, true);
                    if (File.Exists(folder + ".meta"))
                        File.Delete(folder + ".meta");
                }
                finally
                {
                    foreach (var seam in saved)
                        seam.Key.SetValue(null, seam.Value);
                }
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PublicPreviewPreservesRawBytesAndNeverRefreshes(bool bom)
        {
            WithProbe(
                probe =>
                {
                    byte[] before = File.ReadAllBytes(probe.FullPath);
                    var modified = File.GetLastWriteTimeUtc(probe.FullPath);
                    var snapshot = probe.Snapshot();
                    Assert.IsTrue(snapshot.Value<bool>("success"));
                    var preview = probe.Stage(snapshot, Original, Replacement(), true);
                    Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
                    Assert.AreEqual("passed", preview["data"].Value<string>("validation_status"));
                    Assert.AreEqual(Hash(before), preview["data"].Value<string>("original_bytes_sha256"));
                    Assert.AreEqual(bom, preview["data"].Value<bool>("bom"));
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(probe.FullPath));
                    Assert.AreEqual(modified, File.GetLastWriteTimeUtc(probe.FullPath));
                    Assert.AreEqual(0, probe.RefreshCount);
                    Assert.AreEqual(0, probe.ReplaceCount);
                },
                bom
            );
        }

        [Test]
        public void TextCreatedMethodIsResolvedInCurrentNativeBuffer()
        {
            WithProbe(probe =>
            {
                var snapshot = probe.Snapshot();
                var text = new JObject
                {
                    ["startLine"] = 4,
                    ["startCol"] = 1,
                    ["endLine"] = 4,
                    ["endCol"] = 1,
                    ["newText"] = "    public void Added() {}\n",
                };
                var first = probe.Stage(snapshot, Original, text, false);
                Assert.IsTrue(first.Value<bool>("success"), first.ToString());
                Assert.AreEqual("pending", first["data"].Value<string>("validation_status"));
                var final = probe.Stage(
                    snapshot,
                    first["data"].Value<string>("new_contents"),
                    Replacement("Added", "public void Added() { int value = 2; }"),
                    true
                );
                Assert.IsTrue(final.Value<bool>("success"), final.ToString());
                StringAssert.Contains("int value = 2;", final["data"].Value<string>("new_contents"));
                Assert.IsFalse(probe.Stage(snapshot, Original, Replacement("Added"), true).Value<bool>("success"));
                CollectionAssert.AreEqual(Bytes(Original, false), File.ReadAllBytes(probe.FullPath));
            });
        }

        [Test]
        public void WholeBufferValidationRunsOnlyAtFinalStep()
        {
            WithProbe(probe =>
            {
                var snapshot = probe.Snapshot();
                var open = new JObject
                {
                    ["startLine"] = 1,
                    ["startCol"] = 1,
                    ["endLine"] = 1,
                    ["endCol"] = 1,
                    ["newText"] = "namespace Temporary {\n",
                };
                var first = probe.Stage(snapshot, Original, open, false);
                Assert.IsTrue(first.Value<bool>("success"), first.ToString());
                string intermediate = first["data"].Value<string>("new_contents");
                Assert.IsFalse(probe.Stage(snapshot, intermediate, null, true).Value<bool>("success"));
                var close = new JObject
                {
                    ["startLine"] = 6,
                    ["startCol"] = 1,
                    ["endLine"] = 6,
                    ["endCol"] = 1,
                    ["newText"] = "}\n",
                };
                var second = probe.Stage(snapshot, intermediate, close, false);
                Assert.IsTrue(second.Value<bool>("success"), second.ToString());
                var final = probe.Stage(snapshot, second["data"].Value<string>("new_contents"), Replacement(), true);
                Assert.IsTrue(final.Value<bool>("success"), final.ToString());
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ActualPreparedCommitReplacesOnceAndPreservesBom(bool bom)
        {
            WithProbe(
                probe =>
                {
                    var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                    Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
                    byte[] expected = Bytes(preview["data"].Value<string>("new_contents"), bom);
                    var applied = probe.Apply(preview);
                    Assert.IsTrue(applied["data"].Value<bool>("applied"), applied.ToString());
                    CollectionAssert.AreEqual(expected, File.ReadAllBytes(probe.FullPath));
                    Assert.AreEqual(Hash(expected), applied["data"].Value<string>("bytes_sha256"));
                    Assert.AreEqual(1, probe.ReplaceCount);
                    Assert.AreEqual(1, probe.RefreshCount);
                    Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(probe.FullPath)).Length);
                },
                bom
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void NoopPreservesMtimeAndDoesNotReplaceOrRefresh(bool bom)
        {
            WithProbe(
                probe =>
                {
                    var modified = File.GetLastWriteTimeUtc(probe.FullPath);
                    var preview = probe.Stage(probe.Snapshot(), Original, null, true);
                    var applied = probe.Apply(preview);
                    Assert.IsTrue(applied["data"].Value<bool>("no_op"), applied.ToString());
                    Assert.AreEqual(modified, File.GetLastWriteTimeUtc(probe.FullPath));
                    CollectionAssert.AreEqual(Bytes(Original, bom), File.ReadAllBytes(probe.FullPath));
                    Assert.AreEqual(0, probe.ReplaceCount);
                    Assert.AreEqual(0, probe.RefreshCount);
                },
                bom
            );
        }

        [Test]
        public void BomOnlyDriftRefusesPreparedCommit()
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                File.WriteAllBytes(probe.FullPath, Bytes(Original, true));
                Assert.IsFalse(probe.Apply(preview).Value<bool>("success"));
                CollectionAssert.AreEqual(Bytes(Original, true), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [TestCase("before_compare")]
        [TestCase("before_replace")]
        public void DetectedRaceOrPreReplaceFaultNeverOverwritesExternalBytes(string stage)
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                byte[] external = Bytes(Original + "// external\n", false);
                probe.CommitStage = reached =>
                {
                    if (reached != stage)
                        return;
                    if (stage == "before_compare")
                        File.WriteAllBytes(probe.FullPath, external);
                    else
                        throw new IOException("Owned failure before replace");
                };
                var result = probe.Apply(preview);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                CollectionAssert.AreEqual(stage == "before_compare" ? external : Bytes(Original, false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.ReplaceCount);
                Assert.AreEqual(0, probe.RefreshCount);
                Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(probe.FullPath)).Length);
            });
        }

        [Test]
        public void RefreshFailureRetainsKnownCommitWithoutRollback()
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                probe.ThrowRefresh = true;
                var result = probe.Apply(preview);
                Assert.IsTrue(result["data"].Value<bool>("applied"), result.ToString());
                Assert.AreEqual("failed", result["data"].Value<string>("refresh_status"));
                CollectionAssert.AreEqual(Bytes(preview["data"].Value<string>("new_contents"), false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(1, probe.ReplaceCount);
                Assert.AreEqual(1, probe.RefreshCount);
            });
        }

        [TestCase(false, true)]
        [TestCase(true, false)]
        public void PublicPolicyGatesRefuseWithoutReadingPreferences(bool consent, bool enabled)
        {
            WithProbe(probe =>
            {
                probe.Consent = consent;
                probe.Enabled = enabled;
                Assert.IsFalse(probe.Snapshot().Value<bool>("success"));
                CollectionAssert.AreEqual(Bytes(Original, false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [TestCase("source_sha256")]
        [TestCase("source_bytes_sha256")]
        [TestCase("original_bytes_sha256")]
        public void CorruptLogicalOrRawSourceProofRefusesWithoutWrite(string field)
        {
            WithProbe(probe =>
            {
                var snapshot = probe.Snapshot();
                probe.BeforeStage = command => command[field] = new string('0', 64);
                Assert.IsFalse(probe.Stage(snapshot, Original, Replacement(), true).Value<bool>("success"));
                CollectionAssert.AreEqual(Bytes(Original, false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [Test]
        public void RawBooleanTokensAreNotCoercedFromStrings()
        {
            WithProbe(probe =>
            {
                var snapshot = probe.Snapshot();
                probe.BeforeStage = command => command["bom"] = "false";
                Assert.IsFalse(probe.Stage(snapshot, Original, Replacement(), true).Value<bool>("success"));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [TestCase("utf16")]
        [TestCase("invalid_utf8")]
        public void UnsupportedOrMalformedEncodingRefusesWithoutWriting(string encoding)
        {
            WithProbe(probe =>
            {
                byte[] bytes = encoding == "utf16" ? new byte[] { 0xff, 0xfe, 0x41, 0x00 } : new byte[] { 0xc3, 0x28 };
                File.WriteAllBytes(probe.FullPath, bytes);
                Assert.IsFalse(probe.Snapshot().Value<bool>("success"));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [Test]
        public void LiteralUnicodeMixedNewlinesAndMissingFinalNewlineCommitExactly()
        {
            WithProbe(
                probe =>
                {
                    string original = "public class OrderedProbe\r\n{\n    // 😀한\r\n}";
                    File.WriteAllBytes(probe.FullPath, Bytes(original, true));
                    var snapshot = probe.Snapshot();
                    var edit = new JObject
                    {
                        ["startLine"] = 3,
                        ["startCol"] = 10,
                        ["endLine"] = 3,
                        ["endCol"] = 10,
                        ["newText"] = "끝",
                    };
                    var preview = probe.Stage(snapshot, original, edit, true);
                    Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
                    string expected = "public class OrderedProbe\r\n{\n    // 😀한끝\r\n}";
                    Assert.AreEqual(expected, preview["data"].Value<string>("new_contents"));
                    Assert.IsTrue(probe.Apply(preview)["data"].Value<bool>("applied"));
                    CollectionAssert.AreEqual(Bytes(expected, true), File.ReadAllBytes(probe.FullPath));
                },
                true
            );
        }

        [Test]
        public void KnownUnsupportedReplaceRefusesWithoutCopyFallback()
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                probe.CommitStage = stage =>
                {
                    if (stage == "before_replace")
                        throw new PlatformNotSupportedException("Owned unsupported replacement");
                };
                var result = probe.Apply(preview);
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsFalse(result["data"].Value<bool>("applied"));
                CollectionAssert.AreEqual(Bytes(Original, false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
                Assert.AreEqual(1, Directory.GetFiles(Path.GetDirectoryName(probe.FullPath)).Length);
            });
        }

        [Test]
        public void IOExceptionAfterReturnedReplacementRetainsKnownCommit()
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                probe.CommitStage = stage =>
                {
                    if (stage == "after_replace")
                        throw new IOException("Owned postcommit observation fault");
                };
                var result = probe.Apply(preview);
                Assert.IsTrue(result["data"].Value<bool>("applied"), result.ToString());
                CollectionAssert.AreEqual(Bytes(preview["data"].Value<string>("new_contents"), false), File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(1, probe.ReplaceCount);
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [Test]
        public void LaterExternalBytesAreNotRolledBackOrRefreshed()
        {
            WithProbe(probe =>
            {
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                byte[] external = Bytes(Original + "// later external\n", false);
                probe.CommitStage = stage =>
                {
                    if (stage == "after_replace")
                        File.WriteAllBytes(probe.FullPath, external);
                };
                var result = probe.Apply(preview);
                Assert.IsTrue(result["data"].Value<bool>("applied"), result.ToString());
                Assert.AreEqual("conflict", result["data"].Value<string>("outcome"));
                CollectionAssert.AreEqual(external, File.ReadAllBytes(probe.FullPath));
                Assert.AreEqual(0, probe.RefreshCount);
            });
        }

        [Test]
        public void UnrelatedSidecarsArePreservedByActualCommit()
        {
            WithProbe(probe =>
            {
                string temp = probe.FullPath + ".unrelated.tmp";
                string backup = probe.FullPath + ".unrelated.bak";
                File.WriteAllBytes(temp, new byte[] { 1, 2, 3 });
                File.WriteAllBytes(backup, new byte[] { 4, 5, 6 });
                var preview = probe.Stage(probe.Snapshot(), Original, Replacement(), true);
                Assert.IsTrue(probe.Apply(preview)["data"].Value<bool>("applied"));
                CollectionAssert.AreEqual(new byte[] { 1, 2, 3 }, File.ReadAllBytes(temp));
                CollectionAssert.AreEqual(new byte[] { 4, 5, 6 }, File.ReadAllBytes(backup));
                Assert.AreEqual(3, Directory.GetFiles(Path.GetDirectoryName(probe.FullPath)).Length);
            });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ChangeThenRevertRemainsRawNoop(bool bom)
        {
            WithProbe(
                probe =>
                {
                    var snapshot = probe.Snapshot();
                    var insert = new JObject
                    {
                        ["startLine"] = 1,
                        ["startCol"] = 1,
                        ["endLine"] = 1,
                        ["endCol"] = 1,
                        ["newText"] = "// temporary\n",
                    };
                    var first = probe.Stage(snapshot, Original, insert, false);
                    var remove = new JObject
                    {
                        ["startLine"] = 1,
                        ["startCol"] = 1,
                        ["endLine"] = 2,
                        ["endCol"] = 1,
                        ["newText"] = "",
                    };
                    var final = probe.Stage(snapshot, first["data"].Value<string>("new_contents"), remove, true);
                    Assert.IsTrue(final["data"].Value<bool>("no_op"));
                    Assert.IsTrue(probe.Apply(final)["data"].Value<bool>("no_op"));
                    Assert.AreEqual(0, probe.ReplaceCount);
                    Assert.AreEqual(0, probe.RefreshCount);
                    CollectionAssert.AreEqual(Bytes(Original, bom), File.ReadAllBytes(probe.FullPath));
                },
                bom
            );
        }
    }
}
