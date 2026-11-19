using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools.Profiler;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class MemoryAnalysisTests
    {
        private readonly List<Object> owned = new List<Object>();
        private string prefix;
        private string folder;
        private CaptureScope captureScope;

        private sealed class CaptureScope
        {
            internal string Folder;
            internal string Prefix;
            internal bool InProgress;
            internal bool CleanupRequested;
            internal bool RetainPairOnFailure;
        }

        [SetUp]
        public void SetUp()
        {
            prefix = "McpMemory_" + Guid.NewGuid().ToString("N") + "_";
            folder = Path.Combine(Application.temporaryCachePath, prefix);
            captureScope = new CaptureScope { Folder = folder, Prefix = prefix };
            Directory.CreateDirectory(folder);
        }

        [TearDown]
        public void TearDown()
        {
            for (int i = owned.Count - 1; i >= 0; i--)
                if (owned[i] != null)
                    Object.DestroyImmediate(owned[i]);
            owned.Clear();
            captureScope.CleanupRequested = true;
            if (captureScope.RetainPairOnFailure && TestContext.CurrentContext.Result.Outcome.Status == NUnit.Framework.Interfaces.TestStatus.Failed)
            {
                RetainFailedPair(captureScope);
                return;
            }
            if (!captureScope.InProgress)
                DeleteOwnedFolder(captureScope);
        }

        private static void RetainFailedPair(CaptureScope scope)
        {
            if (scope.InProgress || Path.GetFileName(scope.Folder) != scope.Prefix)
                throw new InvalidOperationException("Only the exact completed owned capture pair can be retained.");
            var files = new JArray();
            foreach (string name in new[] { "before.snap", "after.snap" })
            {
                string path = Path.Combine(scope.Folder, name);
                long bytes = new FileInfo(path).Length;
                string hash = null;
                if (bytes <= 512L * 1024 * 1024)
                    using (var input = File.OpenRead(path))
                    using (var sha = SHA256.Create())
                        hash = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "");
                files.Add(
                    new JObject
                    {
                        ["path"] = path,
                        ["container_bytes"] = bytes,
                        ["sha256"] = hash,
                    }
                );
            }
            string receipt = Path.Combine(scope.Folder, "failed-pair-receipt.json");
            File.WriteAllText(
                receipt,
                new JObject
                {
                    ["fixture"] = typeof(MemoryAnalysisTests).FullName,
                    ["retention_reason"] =
                        "Failed real pair assertion; runtime objects were destroyed, completed capture files retained for bounded diagnosis.",
                    ["name_prefix"] = scope.Prefix,
                    ["files"] = files,
                    ["request"] = new JObject
                    {
                        ["action"] = "memory_analyze_snapshots",
                        ["snapshot_a"] = Path.Combine(scope.Folder, "before.snap"),
                        ["snapshot_b"] = Path.Combine(scope.Folder, "after.snap"),
                        ["name_prefix"] = scope.Prefix,
                        ["object_limit"] = 128,
                    },
                    ["expected_added_name"] = scope.Prefix + "Added",
                    ["expected_removed_name"] = scope.Prefix + "Removed",
                    ["expected_stable_name"] = scope.Prefix + "Stable",
                }.ToString()
            );
            TestContext.WriteLine("MCP_MEMORY_RETAINED_PAIR " + receipt);
        }

        private static void DeleteOwnedFolder(CaptureScope scope)
        {
            if (Path.GetFileName(scope.Folder) != scope.Prefix)
                throw new InvalidOperationException("Fixture folder identity changed.");
            if (Directory.Exists(scope.Folder))
                Directory.Delete(scope.Folder, true);
        }

        private static JObject Call(JObject options)
        {
            MethodInfo method = typeof(ManageProfiler).GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method);
            var task = (Task<object>)method.Invoke(null, new object[] { options });
            return JObject.FromObject(task.GetAwaiter().GetResult());
        }

        private static void Success(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());

        private Texture2D Texture(string name, int size)
        {
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false) { name = prefix + name };
            owned.Add(texture);
            return texture;
        }

        private GameObject Root(string name, Sprite sprite)
        {
            var go = new GameObject(prefix + name);
            owned.Add(go);
            go.AddComponent<SpriteRenderer>().sprite = sprite;
            return go;
        }

        private JObject Aggregate(params GameObject[] roots) =>
            Call(
                new JObject
                {
                    ["action"] = "memory_aggregate",
                    ["targets"] = new JArray(roots.Select(root => root.GetInstanceIDCompat().ToString())),
                    ["include_references"] = true,
                    ["include_objects"] = true,
                    ["object_limit"] = 256,
                }
            );

        [Test]
        public void SharedTextureAcrossHierarchyRoots_IsCountedOnceWithBothOwners()
        {
            var texture = Texture("Shared", 16);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 16, 16), Vector2.zero);
            sprite.name = prefix + "SharedSprite";
            owned.Add(sprite);
            var first = Root("First", sprite);
            var second = Root("Second", sprite);
            var response = Aggregate(first, second);
            Success(response);
            var data = response["data"];
            var textures = data["objects"].Where(row => row.Value<int>("object_id") == texture.GetInstanceIDCompat()).ToArray();
            Assert.AreEqual(1, textures.Length, response.ToString());
            Assert.Greater(textures[0].Value<long>("runtime_size_bytes"), 0);
            CollectionAssert.AreEquivalent(new[] { first.GetInstanceIDCompat(), second.GetInstanceIDCompat() }, textures[0]["owner_ids"].Values<int>());
            Assert.AreEqual(data["objects"].Count(), data.Value<int>("unique_object_count"));
            Assert.AreEqual(data["objects"].Sum(row => row.Value<long>("runtime_size_bytes")), data.Value<long>("runtime_size_bytes_sum"));
            Assert.AreEqual(JTokenType.Null, data["managed_heap_bytes"].Type);
            Assert.AreEqual(JTokenType.Null, data["retained_bytes"].Type);
            Assert.AreSame(sprite, first.GetComponent<SpriteRenderer>().sprite);
            Assert.AreSame(texture, sprite.texture);
        }

        [Test]
        public void DuplicateRoot_DoesNotDoubleCountTheSameUnityObjects()
        {
            var texture = Texture("RootTexture", 8);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 8, 8), Vector2.zero);
            owned.Add(sprite);
            var root = Root("Root", sprite);
            var one = Aggregate(root);
            var twice = Aggregate(root, root);
            Success(one);
            Success(twice);
            // Runtime size estimates can change between observations; deduplication is about object identity.
            CollectionAssert.AreEquivalent(
                one["data"]["objects"].Select(row => row.Value<int>("object_id")),
                twice["data"]["objects"].Select(row => row.Value<int>("object_id"))
            );
            foreach (var response in new[] { one, twice })
            {
                var data = response["data"];
                var rows = data["objects"].ToArray();
                Assert.IsFalse(data.Value<bool>("objects_truncated"), response.ToString());
                CollectionAssert.AreEqual(new[] { root.GetInstanceIDCompat() }, data["root_ids"].Values<int>(), response.ToString());
                Assert.AreEqual(rows.Length, rows.Select(row => row.Value<int>("object_id")).Distinct().Count(), response.ToString());
                Assert.AreEqual(rows.Length, data.Value<int>("unique_object_count"), response.ToString());
                Assert.AreEqual(rows.Sum(row => row.Value<long>("runtime_size_bytes")), data.Value<long>("runtime_size_bytes_sum"), response.ToString());
                Assert.AreEqual(1, rows.Count(row => row.Value<int>("object_id") == texture.GetInstanceIDCompat()), response.ToString());
                Assert.AreEqual(1, rows.Count(row => row.Value<int>("object_id") == sprite.GetInstanceIDCompat()), response.ToString());
                foreach (var row in rows)
                    CollectionAssert.AreEqual(new[] { root.GetInstanceIDCompat() }, row["owner_ids"].Values<int>(), response.ToString());
            }
        }

        [Test]
        public void LargeHierarchy_DefaultBudgetIncludesLateChildrenWithBoundedOutput()
        {
            var root = new GameObject(prefix + "Large");
            owned.Add(root);
            for (int i = 0; i < 4097; i++)
                new GameObject("Child" + i).transform.SetParent(root.transform, false);
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_aggregate",
                    ["targets"] = new JArray(root.GetInstanceIDCompat()),
                    ["include_references"] = false,
                    ["include_objects"] = true,
                    ["object_limit"] = 1,
                }
            );
            Success(response);
            Assert.AreEqual(8196, response["data"].Value<int>("unique_object_count"));
            Assert.AreEqual(1, ((JArray)response["data"]["objects"]).Count);
            Assert.IsTrue(response["data"].Value<bool>("objects_truncated"));
            Assert.IsTrue(response["data"].Value<bool>("complete_within_declared_scope"));
        }

        [Test]
        public void WorkBudgetRefusal_DoesNotReturnAnUnderestimatedSuccessfulTotal()
        {
            var root = Root("Budget", null);
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_aggregate",
                    ["targets"] = new JArray(root.GetInstanceIDCompat()),
                    ["max_objects"] = 1,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("memory_work_limit", response.Value<string>("code"));
            Assert.IsNotNull(root.GetComponent<SpriteRenderer>());
        }

        [TestCase("max_objects", "0")]
        [TestCase("max_objects", "1000001")]
        [TestCase("include_references", "1")]
        [TestCase("object_limit", "257")]
        [TestCase("targets", "[]")]
        public void InvalidAggregateAdmission_RefusesBeforeTraversal(string key, string json)
        {
            var root = Root("Invalid", null);
            var options = new JObject { ["action"] = "memory_aggregate", ["targets"] = new JArray(root.GetInstanceIDCompat()) };
            options[key] = JToken.Parse(json);
            var response = Call(options);
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("Invalid", response.Value<string>("error"));
        }

        [Test]
        public void UnsupportedSnapshotContainer_IsExplicitAndNeverFileSizeMemoryEvidence()
        {
            string path = Path.Combine(folder, "unsupported.snap");
            File.WriteAllText(path, "This is not a supported Unity native memory snapshot.");
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = path,
                    ["snapshot_b"] = path,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("snapshot_format_unsupported", response.Value<string>("code"));
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [Test]
        public void FutureSnapshotVersion_IsRejectedBeforeNativeReaderOpen()
        {
            string path = Path.Combine(folder, "future-version.snap");
            // Minimal queried-snapshot container with a real four-byte Metadata_Version entry.
            // It is intentionally version99, never used as positive memory evidence.
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xAEABCDCDu);
                writer.Write(99u);
                long block = writer.BaseStream.Position;
                writer.Write(4UL);
                writer.Write(4UL);
                writer.Write(4UL);
                long blocks = writer.BaseStream.Position;
                writer.Write(0x20170724u);
                writer.Write(1u);
                writer.Write((ulong)block);
                long version = writer.BaseStream.Position;
                writer.Write((ushort)1);
                writer.Write(0u);
                writer.Write(4u);
                writer.Write(0UL);
                long directory = writer.BaseStream.Position;
                writer.Write(0xCDCDAEABu);
                writer.Write(0x20170724u);
                writer.Write((ulong)blocks);
                writer.Write(1u);
                writer.Write((ulong)version);
                writer.Write((ulong)directory);
                writer.Write(0xABCDCDAEu);
            }
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = path,
                    ["snapshot_b"] = path,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("snapshot_format_unsupported", response.Value<string>("code"));
            StringAssert.Contains("actual 99", response["data"].Value<string>("message"));
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EntryAllocationBounds_RefuseBeforeNativeReaderOpen(bool dynamic)
        {
            string path = Path.Combine(folder, "entry-bound.snap");
            uint count = dynamic ? 1000001u : 5u;
            // Deliberately invalid metadata, never positive snapshot evidence. The dynamic offsets are
            // omitted: the count must be refused before the reader can allocate or try to read them.
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xAEABCDCDu);
                writer.Write(11u);
                long block = writer.BaseStream.Position;
                writer.Write(4UL);
                writer.Write(4UL);
                writer.Write(4UL);
                long blocks = writer.BaseStream.Position;
                writer.Write(0x20170724u);
                writer.Write(1u);
                writer.Write((ulong)block);
                long version = writer.BaseStream.Position;
                writer.Write((ushort)1);
                writer.Write(0u);
                writer.Write(4u);
                writer.Write(0UL);
                long entry = writer.BaseStream.Position;
                writer.Write((ushort)(dynamic ? 3 : 2));
                writer.Write(0u);
                writer.Write(dynamic ? count : 1u);
                if (dynamic)
                    writer.Write(0UL);
                else
                    writer.Write(count);
                long directory = writer.BaseStream.Position;
                writer.Write(0xCDCDAEABu);
                writer.Write(0x20170724u);
                writer.Write((ulong)blocks);
                writer.Write(2u);
                writer.Write((ulong)version);
                writer.Write((ulong)entry);
                writer.Write((ulong)directory);
                writer.Write(0xABCDCDAEu);
            }
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = path,
                    ["snapshot_b"] = path,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(dynamic ? "memory_work_limit" : "snapshot_column_invalid", response.Value<string>("code"), response.ToString());
            StringAssert.Contains("Metadata_RecordDate", response["data"].Value<string>("message"));
            StringAssert.Contains(count.ToString(), response["data"].Value<string>("message"));
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [TestCase(0L)]
        [TestCase(3221225472L)]
        [TestCase(9663676416L)]
        public void PackedConstantHeaders_UseOnlyTheReaderUIntCount(long virtualLength)
        {
            string path = Path.Combine(folder, "packed-constants.snap");
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xAEABCDCDu);
                writer.Write(18u);
                long block = writer.BaseStream.Position;
                writer.Write(4UL);
                writer.Write(4UL);
                writer.Write(4UL);
                long blocks = writer.BaseStream.Position;
                writer.Write(0x20170724u);
                writer.Write(1u);
                writer.Write((ulong)block);
                long version = writer.BaseStream.Position;
                writer.Write((ushort)1);
                writer.Write(0u);
                writer.Write(4u);
                writer.Write(0UL);
                long first = writer.BaseStream.Position;
                writer.Write((ushort)2);
                writer.Write(0u);
                writer.Write(1u);
                writer.Write(1u);
                // Constant counts occupy four meaningful bytes. The reader's 18-byte header
                // overlay also sees the next entry's format/block bytes, which are not count bits.
                long second = writer.BaseStream.Position;
                Assert.AreEqual(14, second - first);
                writer.Write((ushort)2);
                writer.Write(0u);
                writer.Write(1u);
                writer.Write(1u);
                long directory = writer.BaseStream.Position;
                writer.Write(0xCDCDAEABu);
                writer.Write(0x20170724u);
                writer.Write((ulong)blocks);
                writer.Write(3u);
                writer.Write((ulong)version);
                writer.Write((ulong)first);
                writer.Write((ulong)second);
                writer.Write((ulong)directory);
                writer.Write(0xABCDCDAEu);
            }
            Type operations = typeof(ManageProfiler).Assembly.GetType("MCPForUnity.Editor.Tools.Profiler.MemorySnapshotAnalysisOps");
            Assert.IsNotNull(operations);
            MethodInfo preflight = operations.GetMethod("Preflight", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(preflight);
            Type entryType = Type.GetType("Unity.MemoryProfiler.Editor.Format.QueriedSnapshot.EntryType, Unity.MemoryProfiler.Editor");
            Assert.IsNotNull(entryType);
            // This exercises only container preflight; synthetic data is never used as object-memory evidence.
            using (var input = new PaddedSnapshot(File.ReadAllBytes(path), virtualLength))
            {
                if (virtualLength > 8L * 1024 * 1024 * 1024)
                {
                    var error = Assert.Throws<TargetInvocationException>(() => preflight.Invoke(null, new object[] { input, entryType, 18u }));
                    StringAssert.Contains("input budget", error.InnerException.Message);
                }
                else
                    Assert.DoesNotThrow(() => preflight.Invoke(null, new object[] { input, entryType, 18u }));
                Assert.Less(input.ReadBytes, 1024, "Preflight reads metadata, never the padded capture body.");
            }
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        private sealed class PaddedSnapshot : Stream
        {
            private readonly byte[] bytes;
            public long ReadBytes;

            public PaddedSnapshot(byte[] bytes, long length)
            {
                this.bytes = bytes;
                Length = Math.Max(bytes.Length, length);
            }

            public override long Length { get; }
            public override long Position { get; set; }
            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;

            public override int Read(byte[] buffer, int offset, int count)
            {
                int amount = (int)Math.Min(count, Length - Position);
                for (int i = 0; i < amount; i++)
                {
                    long at = Position + i;
                    buffer[offset + i] =
                        at < bytes.Length - 12 ? bytes[(int)at]
                        : at >= Length - 12 ? bytes[(int)(bytes.Length - (Length - at))]
                        : (byte)0;
                }
                Position += amount;
                ReadBytes += amount;
                return amount;
            }

            public override long Seek(long offset, SeekOrigin origin) =>
                Position =
                    (
                        origin == SeekOrigin.Begin ? 0
                        : origin == SeekOrigin.Current ? Position
                        : Length
                    ) + offset;

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }

        private IEnumerator Capture(string path)
        {
            Type profilerType = Type.GetType("Unity.Profiling.Memory.MemoryProfiler, UnityEngine.CoreModule")
                ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.MemoryProfiler, UnityEngine.CoreModule");
            Type flagsType = Type.GetType("Unity.Profiling.Memory.CaptureFlags, UnityEngine.CoreModule")
                ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.CaptureFlags, UnityEngine.CoreModule");
            Assert.IsNotNull(profilerType, "The installed Editor must expose its real native memory capture API.");
            Assert.IsNotNull(flagsType);
            MethodInfo method = profilerType.GetMethod("TakeSnapshot", new[] { typeof(string), typeof(Action<string, bool>), flagsType });
            Assert.IsNotNull(method);
            bool completed = false;
            bool succeeded = false;
            CaptureScope scope = captureScope;
            scope.InProgress = true;
            Action<string, bool> callback = (capturedPath, ok) =>
            {
                succeeded = ok && capturedPath == path;
                completed = true;
                scope.InProgress = false;
                if (scope.CleanupRequested)
                    DeleteOwnedFolder(scope);
            };
            method.Invoke(null, new object[] { path, callback, Enum.Parse(flagsType, "NativeObjects") });
            double deadline = EditorApplication.timeSinceStartup + 60;
            while (!completed && EditorApplication.timeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(completed, "Owned real native capture exceeded its bounded callback deadline; late cleanup stays callback-owned.");
            Assert.IsTrue(succeeded, "The real native snapshot capture failed.");
            Assert.IsTrue(File.Exists(path));
        }

        [UnityTest]
        public IEnumerator RealSnapshots_ReportNativeTypedBytesAndCreatedDeletedObjectIdentities()
        {
            var capability = Call(new JObject { ["action"] = "memory_snapshot_reader_status" });
            Success(capability);
            Assert.IsTrue(capability["data"].Value<bool>("reader_supported"), capability.ToString());
            var removed = Texture("Removed", 8);
            var stable = Texture("Stable", 16);
            string before = Path.Combine(folder, "before.snap");
            string after = Path.Combine(folder, "after.snap");
            yield return Capture(before);
            Object.DestroyImmediate(removed);
            var added = Texture("Added", 64);
            yield return Capture(after);
            captureScope.RetainPairOnFailure = true;
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = before,
                    ["snapshot_b"] = after,
                    ["name_prefix"] = prefix,
                    ["object_limit"] = 128,
                }
            );
            Success(response);
            var data = response["data"];
            Assert.AreEqual("native_object_size", data.Value<string>("byte_semantics"));
            var texture = data["types"].Single(row => row.Value<string>("type") == "Texture2D");
            Assert.AreEqual(2, texture.Value<int>("count_a"));
            Assert.AreEqual(2, texture.Value<int>("count_b"));
            Assert.AreEqual(0, texture.Value<int>("count_delta"));
            Assert.Greater(texture.Value<long>("bytes_delta"), 0);
            Assert.AreEqual("same_capture_session", data["object_delta"].Value<string>("status"));
            Assert.IsTrue(data["object_delta"]["added"].Any(row => row.Value<string>("name") == added.name));
            Assert.IsTrue(data["object_delta"]["removed"].Any(row => row.Value<string>("name") == prefix + "Removed"));
            Assert.IsFalse(data["object_delta"]["added"].Any(row => row.Value<string>("name") == stable.name));
            Assert.AreEqual(JTokenType.Null, data["managed_heap_bytes_delta"].Type);
            Assert.AreEqual(JTokenType.Null, data["retained_bytes_delta"].Type);
            using (File.Open(before, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            using (File.Open(after, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [Test]
        public void ReaderCapability_ReportsActualProfileTargetLayout()
        {
            Type target = Type.GetType("Unity.MemoryProfiler.Editor.Format.ProfileTargetInfo, Unity.MemoryProfiler.Editor");
            Assert.IsNotNull(target);
            var response = Call(new JObject { ["action"] = "memory_snapshot_reader_status" });
            Success(response);
            Assert.IsTrue(response["data"].Value<bool>("reader_supported"), response.ToString());
            Assert.AreEqual(Marshal.SizeOf(target), response["data"].Value<int>("profile_target_info_bytes"), response.ToString());
            Assert.AreEqual(Marshal.OffsetOf(target, "SessionGUID").ToInt32(), response["data"].Value<int>("profile_target_session_offset"));
            Assert.AreEqual(0, response["data"].Value<int>("profile_target_session_offset"));
            Assert.AreEqual(8, response["data"].Value<int>("minimum_format"));
            Assert.AreEqual(11, response["data"].Value<int>("session_info_minimum_format"));
            var versions = target.Assembly.GetType("Unity.MemoryProfiler.Editor.Format.FormatVersion", true);
            if (Enum.IsDefined(versions, "StaticFieldAndNonGCHeapVersion"))
                Assert.AreEqual(20, response["data"].Value<int>("maximum_format"), response.ToString());
        }

        [Test]
        public void ReaderStatusAndAnalysisUseTheSameInstalledContractBeforeOpeningAFile()
        {
            var status = Call(new JObject { ["action"] = "memory_snapshot_reader_status" });
            Success(status);
            var capability = status["data"];
            Assert.AreEqual("com.unity.memoryprofiler", (string)capability["required_package"]);
            string path = Path.Combine(folder, "malformed.snap");
            File.WriteAllBytes(path, new byte[32]);
            var comparison = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = path,
                    ["snapshot_b"] = path,
                }
            );
            if ((bool)capability["reader_supported"])
                Assert.AreEqual("snapshot_format_unsupported", (string)comparison["code"], comparison.ToString());
            else
            {
                Assert.AreEqual("snapshot_reader_unavailable", (string)comparison["code"], comparison.ToString());
                Assert.AreEqual((string)capability["reason"], (string)comparison["data"]["reason"]);
                Assert.AreEqual((string)capability["missing_contract"], (string)comparison["data"]["missing_contract"]);
            }
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        // This deterministic queried-snapshot fixture contains only the selected native columns and
        // representative optional chapters. RealSnapshots_ exercises actual Editor captures separately.
        private static void WriteNativeColumnFixture(
            string path,
            uint format,
            ulong bytes,
            bool invalidIdWidth = false,
            bool empty = false,
            int[] omit = null,
            int undefinedChapter = -1
        )
        {
            using var writer = new BinaryWriter(File.Create(path));
            writer.Write(0xAEABCDCDu);
            var chapters = new ulong[
                format >= 20 ? 105
                : format == 19 ? 102
                : format >= 11 ? 60
                : 59
            ];
            var blocks = new List<ulong>();
            void Column(int id, ushort kind, byte[] payload)
            {
                if (omit?.Contains(id) == true)
                    return;
                bool emptyArray = empty && (id == 5 || id == 7 || id >= 10 && id <= 13);
                uint elementSize = kind == 3 ? 1u : (uint)payload.Length;
                if (emptyArray)
                {
                    kind = 2;
                    payload = new byte[1];
                    if (id == 10 && invalidIdWidth)
                        elementSize = 1;
                }
                ulong data = (ulong)writer.BaseStream.Position;
                writer.Write(payload);
                blocks.Add((ulong)writer.BaseStream.Position);
                writer.Write(1024UL);
                writer.Write((ulong)payload.Length);
                writer.Write(data);
                chapters[id] = (ulong)writer.BaseStream.Position;
                writer.Write(id == undefinedChapter ? (ushort)0 : kind);
                writer.Write((uint)blocks.Count - 1);
                writer.Write(elementSize);
                writer.Write(kind == 2 && !emptyArray ? 1UL : 0UL);
                if (kind == 3)
                    writer.Write((ulong)payload.Length);
            }
            Column(0, 1, BitConverter.GetBytes(format));
            Column(3, 1, BitConverter.GetBytes(2u));
            Column(5, 3, System.Text.Encoding.UTF8.GetBytes("FixtureTexture"));
            Column(7, 2, BitConverter.GetBytes(0));
            Column(10, 2, format >= 18 && !invalidIdWidth ? BitConverter.GetBytes(0xfedcba9876543210UL) : BitConverter.GetBytes(-42));
            Column(11, 3, System.Text.Encoding.UTF8.GetBytes("FixtureObject"));
            Column(12, 2, BitConverter.GetBytes(0x123456789abcdef0UL));
            Column(13, 2, BitConverter.GetBytes(bytes));
            if (format >= 11)
            {
                var target = new byte[520];
                BitConverter.GetBytes(1234u).CopyTo(target, 0);
                Column(59, 1, target);
            }
            if (format >= 19)
                Column(101, 2, BitConverter.GetBytes(0UL)); // NativeObjects_GCHandle
            if (format >= 20)
                Column(103, 2, BitConverter.GetBytes(0u)); // FieldDescriptions_Flags
            ulong blockDirectory = (ulong)writer.BaseStream.Position;
            writer.Write(0x20170724u);
            writer.Write((uint)blocks.Count);
            foreach (ulong block in blocks)
                writer.Write(block);
            ulong directory = (ulong)writer.BaseStream.Position;
            writer.Write(0xCDCDAEABu);
            writer.Write(0x20170724u);
            writer.Write(blockDirectory);
            writer.Write((uint)chapters.Length);
            foreach (ulong chapter in chapters)
                writer.Write(chapter);
            writer.Write(directory);
            writer.Write(0xABCDCDAEu);
        }

        [TestCase(8u)]
        [TestCase(9u)]
        [TestCase(10u)]
        [TestCase(12u)]
        public void NativeCaptureFlagRequiresDeclaredColumnsEvenAtZeroRows(uint version)
        {
            string path = Path.Combine(folder, "empty-native.snap");
            JObject Compare() =>
                Call(
                    new JObject
                    {
                        ["action"] = "memory_analyze_snapshots",
                        ["snapshot_a"] = path,
                        ["snapshot_b"] = path,
                    }
                );
            JObject CompareEmpty(params int[] omit)
            {
                WriteNativeColumnFixture(path, version, 0, false, true, omit);
                return Compare();
            }
            var nativeColumns = new[] { 5, 7, 10, 11, 12, 13 };
            foreach (var omitted in new[] { nativeColumns }.Concat(nativeColumns.Select(id => new[] { id })))
            {
                var missing = CompareEmpty(omitted);
                Assert.AreEqual("snapshot_column_invalid", (string)missing["code"], missing.ToString());
            }
            WriteNativeColumnFixture(path, version, 0, empty: true, undefinedChapter: 11);
            var undefinedName = Compare();
            Assert.AreEqual("snapshot_column_invalid", (string)undefinedName["code"], undefinedName.ToString());
            WriteNativeColumnFixture(path, version, 0, invalidIdWidth: true, empty: true);
            var invalidWidth = Compare();
            Assert.AreEqual("snapshot_column_invalid", (string)invalidWidth["code"], invalidWidth.ToString());
            StringAssert.Contains("NativeObjects_InstanceId", (string)invalidWidth["data"]["message"]);
            var present = CompareEmpty();
            Success(present);
            Assert.AreEqual(0, (int)present["data"]["snapshot_a"]["selected_object_count"]);
            Assert.AreEqual(0L, (long)present["data"]["native_object_bytes_delta"]);
            Assert.AreEqual(0, ((JArray)present["data"]["types"]).Count);
        }

        [TestCase(7u)]
        [TestCase(8u)]
        [TestCase(9u)]
        [TestCase(10u)]
        [TestCase(19u)]
        [TestCase(20u)]
        [TestCase(21u)]
        [TestCase(19u, true)]
        [TestCase(20u, true)]
        public void QueriedSnapshotWireVersionsPreserveTypedDeltasAndIdentityAvailability(uint version, bool invalidIdWidth = false)
        {
            var status = Call(new JObject { ["action"] = "memory_snapshot_reader_status" });
            Success(status);
            var capability = status["data"];
            Assert.IsTrue((bool)capability["reader_supported"], status.ToString());
            string a = Path.Combine(folder, "wire-before.snap");
            string b = Path.Combine(folder, "wire-after.snap");
            WriteNativeColumnFixture(a, version, 100, invalidIdWidth);
            WriteNativeColumnFixture(b, version, 163, invalidIdWidth);
            var response = Call(
                new JObject
                {
                    ["action"] = "memory_analyze_snapshots",
                    ["snapshot_a"] = a,
                    ["snapshot_b"] = b,
                }
            );
            if (version < 8 || version > (uint)capability["maximum_format"])
                Assert.AreEqual("snapshot_format_unsupported", (string)response["code"], response.ToString());
            else if (invalidIdWidth)
                Assert.AreEqual("snapshot_column_invalid", (string)response["code"], response.ToString());
            else
            {
                Success(response);
                var data = response["data"];
                Assert.AreEqual(version, (uint)data["snapshot_a"]["format_version"]);
                Assert.AreEqual("FixtureTexture", (string)data["types"][0]["type"]);
                Assert.AreEqual(1, (int)data["types"][0]["count_a"]);
                Assert.AreEqual(1, (int)data["types"][0]["count_b"]);
                Assert.AreEqual(63L, (long)data["native_object_bytes_delta"]);
                if (version < 11)
                {
                    Assert.AreEqual(JTokenType.Null, data["snapshot_a"]["session_id"].Type);
                    Assert.AreEqual(JTokenType.Null, data["object_delta"]["changed_count"].Type);
                    Assert.AreEqual(JTokenType.Null, data["object_delta"]["added"].Type);
                    Assert.AreEqual(JTokenType.Null, data["object_delta"]["removed"].Type);
                }
                else
                {
                    Assert.AreEqual(1234u, (uint)data["snapshot_a"]["session_id"]);
                    Assert.AreEqual(1, (int)data["object_delta"]["changed_count"]);
                    var row = data["object_delta"]["changed"][0];
                    Assert.AreEqual("18364758544493064720", (string)row["native_id"]);
                    Assert.AreEqual("18364758544493064720:123456789abcdef0:FixtureTexture", (string)row["snapshot_identity"]);
                }
            }
            using (File.Open(a, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            using (File.Open(b, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [Test, Explicit("Requires MCP_MEMORY_RETAINED_PAIR_RECEIPT for an exact owned failed-capture receipt; never captures new snapshots.")]
        public void RetainedSnapshotPair_ReanalysisReleasesHandlesOnSuccessAndRefusal()
        {
            string receiptPath = Environment.GetEnvironmentVariable("MCP_MEMORY_RETAINED_PAIR_RECEIPT");
            Assert.IsNotEmpty(receiptPath, "Set MCP_MEMORY_RETAINED_PAIR_RECEIPT to the retained owned failed-pair receipt.");
            Assert.IsTrue(File.Exists(receiptPath), receiptPath);
            var receipt = JObject.Parse(File.ReadAllText(receiptPath));
            string retainedFolder = Path.GetDirectoryName(Path.GetFullPath(receiptPath));
            string retainedPrefix = receipt.Value<string>("name_prefix");
            Assert.IsTrue(retainedPrefix.StartsWith("McpMemory_", StringComparison.Ordinal));
            Assert.AreEqual(retainedPrefix, Path.GetFileName(retainedFolder));
            Assert.AreEqual(typeof(MemoryAnalysisTests).FullName, receipt.Value<string>("fixture"));
            Assert.AreEqual(retainedPrefix + "Added", receipt.Value<string>("expected_added_name"));
            Assert.AreEqual(retainedPrefix + "Removed", receipt.Value<string>("expected_removed_name"));
            Assert.AreEqual(retainedPrefix + "Stable", receipt.Value<string>("expected_stable_name"));
            Assert.AreEqual(2, receipt["files"].Count());
            var request = (JObject)receipt["request"].DeepClone();
            Assert.AreEqual("memory_analyze_snapshots", request.Value<string>("action"));
            Assert.AreEqual(retainedPrefix, request.Value<string>("name_prefix"));
            string retainedBefore = Path.Combine(retainedFolder, "before.snap");
            string retainedAfter = Path.Combine(retainedFolder, "after.snap");
            Assert.AreEqual(retainedBefore, Path.GetFullPath(request.Value<string>("snapshot_a")));
            Assert.AreEqual(retainedAfter, Path.GetFullPath(request.Value<string>("snapshot_b")));
            foreach (string path in new[] { retainedBefore, retainedAfter })
            {
                var file = receipt["files"].Single(row => Path.GetFullPath(row.Value<string>("path")) == path);
                long bytes = new FileInfo(path).Length;
                Assert.Greater(bytes, 0);
                Assert.LessOrEqual(bytes, 512L * 1024 * 1024);
                Assert.AreEqual(file.Value<long>("container_bytes"), bytes);
                using (var input = File.OpenRead(path))
                using (var sha = SHA256.Create())
                    Assert.AreEqual(file.Value<string>("sha256"), BitConverter.ToString(sha.ComputeHash(input)).Replace("-", ""));
                using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                // A later native run has different permitted temp roots. Transfer identical bytes
                // into this fixture's current owned scope; never relax product path confinement.
                string copy = Path.Combine(folder, Path.GetFileName(path));
                Assert.AreNotEqual(Path.GetFullPath(path), Path.GetFullPath(copy));
                File.Copy(path, copy, false);
                Assert.AreEqual(bytes, new FileInfo(copy).Length);
                using (var input = File.OpenRead(copy))
                using (var sha = SHA256.Create())
                    Assert.AreEqual(file.Value<string>("sha256"), BitConverter.ToString(sha.ComputeHash(input)).Replace("-", ""));
                using (File.Open(copy, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            }
            string before = Path.Combine(folder, "before.snap");
            string after = Path.Combine(folder, "after.snap");
            request["snapshot_a"] = before;
            request["snapshot_b"] = after;
            for (int repeat = 0; repeat < 2; repeat++)
            {
                var response = Call(request);
                Success(response);
                var data = response["data"];
                Assert.AreEqual("native_object_size", data.Value<string>("byte_semantics"));
                var texture = data["types"].Single(row => row.Value<string>("type") == "Texture2D");
                Assert.AreEqual(2, texture.Value<int>("count_a"));
                Assert.AreEqual(2, texture.Value<int>("count_b"));
                Assert.AreEqual(0, texture.Value<int>("count_delta"));
                Assert.Greater(texture.Value<long>("bytes_delta"), 0);
                Assert.AreEqual("same_capture_session", data["object_delta"].Value<string>("status"));
                Assert.IsTrue(data["object_delta"]["added"].Any(row => row.Value<string>("name") == receipt.Value<string>("expected_added_name")));
                Assert.IsTrue(data["object_delta"]["removed"].Any(row => row.Value<string>("name") == receipt.Value<string>("expected_removed_name")));
                Assert.IsFalse(data["object_delta"]["added"].Any(row => row.Value<string>("name") == receipt.Value<string>("expected_stable_name")));
                Assert.AreEqual(JTokenType.Null, data["managed_heap_bytes_delta"].Type);
                Assert.AreEqual(JTokenType.Null, data["retained_bytes_delta"].Type);
                using (File.Open(before, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                using (File.Open(after, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            }
            var refusalRequest = (JObject)request.DeepClone();
            refusalRequest["max_objects"] = 1;
            var refusal = Call(refusalRequest);
            Assert.IsFalse(refusal.Value<bool>("success"));
            Assert.AreEqual("memory_work_limit", refusal.Value<string>("code"), refusal.ToString());
            using (File.Open(before, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            using (File.Open(after, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            foreach (string path in new[] { retainedBefore, retainedAfter })
            {
                var file = receipt["files"].Single(row => Path.GetFullPath(row.Value<string>("path")) == path);
                Assert.AreEqual(file.Value<long>("container_bytes"), new FileInfo(path).Length);
                using (var input = File.OpenRead(path))
                using (var sha = SHA256.Create())
                    Assert.AreEqual(file.Value<string>("sha256"), BitConverter.ToString(sha.ComputeHash(input)).Replace("-", ""));
            }
            File.Delete(before);
            File.Delete(after);
            Assert.IsFalse(File.Exists(before));
            Assert.IsFalse(File.Exists(after));
        }
    }
}
