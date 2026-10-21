using System;
using System.Linq;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using NativeObject = MCPForUnity.Editor.Tools.Profiler.MemorySnapshotComparison.NativeObject;
using Snapshot = MCPForUnity.Editor.Tools.Profiler.MemorySnapshotComparison.Snapshot;

namespace MCPForUnityTests.Editor.Tools
{
    public sealed class MemorySnapshotComparisonTests
    {
        private static void Add(Snapshot snapshot, string key, long bytes, string type = "Texture", string name = null) =>
            snapshot.Objects.Add(
                key,
                new NativeObject
                {
                    Key = key,
                    Id = key,
                    Type = type,
                    Name = name ?? key,
                    Bytes = bytes,
                }
            );

        [Test]
        public void CountsAndBytesAreCompleteWhileRowsUseOrdinalBoundedSelection()
        {
            var a = new Snapshot { Session = 1, Format = 18 };
            var b = new Snapshot { Session = 1, Format = 18 };
            foreach (string key in new[] { "z", "a", "Z", "m" })
                Add(a, key, 2);
            foreach (string key in new[] { "x", "b", "A" })
                Add(b, key, 3);
            Add(a, "changed-size", 4, "Mesh");
            Add(b, "changed-size", 5, "Mesh");
            Add(a, "changed-name", 6, "Mesh", "old");
            Add(b, "changed-name", 6, "Mesh", "new");
            Add(a, "same", 7);
            Add(b, "same", 7);
            var result = MemorySnapshotComparison.Compare(a, b, 2);
            Assert.AreEqual(25, result.BytesA);
            Assert.AreEqual(27, result.BytesB);
            CollectionAssert.AreEqual(new[] { "Mesh", "Texture" }, result.Types.Select(row => (string)row["type"]));
            Assert.AreEqual(10, (long)result.Types[0]["bytes_a"]);
            Assert.AreEqual(11, (long)result.Types[0]["bytes_b"]);
            Assert.AreEqual(-1, (int)result.Types[1]["count_delta"]);
            Assert.AreEqual(3, (int)result.Delta["added_count"]);
            Assert.AreEqual(4, (int)result.Delta["removed_count"]);
            Assert.AreEqual(2, (int)result.Delta["changed_count"]);
            CollectionAssert.AreEqual(new[] { "A", "b" }, result.Delta["added"].Select(row => (string)row["snapshot_identity"]));
            CollectionAssert.AreEqual(new[] { "Z", "a" }, result.Delta["removed"].Select(row => (string)row["snapshot_identity"]));
            CollectionAssert.AreEqual(new[] { "changed-name", "changed-size" }, result.Delta["changed"].Select(row => (string)row["snapshot_identity"]));
            Assert.IsTrue((bool)result.Delta["objects_truncated"]);
            Assert.AreEqual("new", (string)result.Delta["changed"][0]["name"]);
        }

        [TestCase(0u, 18u)]
        [TestCase(2u, 18u)]
        [TestCase(1u, 17u)]
        public void UnknownIdentityKeepsTypeTotalsButDoesNotInventObjectDeltas(uint session, uint format)
        {
            var a = new Snapshot { Session = 1, Format = 18 };
            var b = new Snapshot { Session = session, Format = format };
            Add(a, "same-key", 4);
            Add(b, "same-key", 9);
            var result = MemorySnapshotComparison.Compare(a, b, 1);
            Assert.AreEqual(5, (long)result.Types[0]["bytes_delta"]);
            Assert.AreEqual(JTokenType.Null, result.Delta["added"].Type);
            Assert.AreEqual(JTokenType.Null, result.Delta["changed_count"].Type);
            Assert.IsNull(result.Delta["objects_truncated"]);
        }

        [Test]
        public void EmptyAndBoundedNamesRetainExistingWireSemantics()
        {
            var a = new Snapshot { Session = 1, Format = 18 };
            var b = new Snapshot { Session = 1, Format = 18 };
            var empty = MemorySnapshotComparison.Compare(a, b, 1);
            Assert.AreEqual(0, empty.Types.Count);
            Assert.IsFalse((bool)empty.Delta["objects_truncated"]);
            Add(b, "a", 1, name: new string('x', 600));
            var result = MemorySnapshotComparison.Compare(a, b, 1);
            Assert.AreEqual(512, ((string)result.Delta["added"][0]["name"]).Length);
            Assert.IsFalse((bool)result.Delta["objects_truncated"]);
        }

        [Test]
        public void HundredsOfTypesKeepCompleteTotalsAndSmallObjectPreviews()
        {
            var a = new Snapshot { Session = 1, Format = 18 };
            var b = new Snapshot { Session = 1, Format = 18 };
            for (int i = 0; i < 513; i++)
                Add(b, i.ToString(), 1, "Type" + i);
            var result = MemorySnapshotComparison.Compare(a, b, 1);
            Assert.AreEqual(513, result.Types.Count);
            Assert.AreEqual(513, result.BytesB);
            Assert.AreEqual(513, (int)result.Delta["added_count"]);
            Assert.AreEqual(1, ((JArray)result.Delta["added"]).Count);
        }

        [Test]
        public void TypeAndByteWorkLimitsAreStillEnforced()
        {
            var a = new Snapshot();
            var b = new Snapshot();
            for (int i = 0; i < 4097; i++)
                Add(a, i.ToString(), 1, "Type" + i);
            Assert.Throws<MemorySnapshotComparison.LimitException>(() => MemorySnapshotComparison.Compare(a, b, 1));
            a.Objects.Clear();
            Add(a, "a", long.MaxValue, "First");
            Add(a, "b", 1, "Second");
            Assert.Throws<OverflowException>(() => MemorySnapshotComparison.Compare(a, b, 1));
        }
    }
}
