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
        [TestCase("max_objects", "4097")]
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

        [Test]
        public void PackedConstantHeaders_UseOnlyTheReaderUIntCount()
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
            using (var input = File.OpenRead(path))
                Assert.DoesNotThrow(() => preflight.Invoke(null, new object[] { input, entryType }));
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        private IEnumerator Capture(string path)
        {
            Type profilerType =
                Type.GetType("Unity.Profiling.Memory.MemoryProfiler, UnityEngine.CoreModule")
                ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.MemoryProfiler, UnityEngine.CoreModule");
            Type flagsType =
                Type.GetType("Unity.Profiling.Memory.CaptureFlags, UnityEngine.CoreModule")
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
