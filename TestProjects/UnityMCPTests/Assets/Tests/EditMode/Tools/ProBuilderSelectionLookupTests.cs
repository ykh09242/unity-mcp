using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class ProBuilderSelectionLookupTests
    {
        private static readonly MethodInfo FindFace = typeof(ManageProBuilder).GetMethod("IndexOfFace", BindingFlags.NonPublic | BindingFlags.Static);

        [Test]
        public void LookupPreservesReferenceIdentityAndFirstOccurrence()
        {
            var first = new EqualFace();
            var second = new EqualFace();
            var faces = new CountingFaces(new object[] { first, second, first });
            var lookup = new Lookup();

            Assert.That(Find(faces, second, lookup), Is.EqualTo(1));
            Assert.That(Find(faces, first, lookup), Is.EqualTo(0));
            Assert.That(Find(faces, new EqualFace(), lookup), Is.EqualTo(-1));
        }

        [Test]
        public void NullFacesKeepTheirOriginalFirstIndex()
        {
            var face = new EqualFace();
            var faces = new CountingFaces(new object[] { face, null, null });
            var lookup = new Lookup();

            Assert.That(Find(faces, null, lookup), Is.EqualTo(1));
            Assert.That(Find(faces, face, lookup), Is.EqualTo(0));
            Assert.That(Find(faces, null, lookup), Is.EqualTo(1));
        }

        [Test]
        public void EmptyMeshesAndMissingNullFacesReturnNoMatch()
        {
            var lookup = new Lookup();
            var empty = new CountingFaces(Array.Empty<object>());
            Assert.That(Find(empty, new EqualFace(), lookup), Is.EqualTo(-1));
            Assert.That(Find(empty, null, lookup), Is.EqualTo(-1));

            lookup = new Lookup();
            Assert.That(Find(new CountingFaces(new object[] { new EqualFace() }), null, lookup), Is.EqualTo(-1));
        }

        [TestCase(1)]
        [TestCase(64)]
        [TestCase(512)]
        public void SelectingEveryFaceReadsTheMeshListOnlyOnce(int size)
        {
            var items = new object[size];
            for (int i = 0; i < size; i++)
                items[i] = new EqualFace();
            var faces = new CountingFaces(items);
            var lookup = new Lookup();

            for (int i = size - 1; i >= 0; i--)
                Assert.That(Find(faces, items[i], lookup), Is.EqualTo(i));

            Assert.That(faces.Reads, Is.LessThanOrEqualTo(size), "Selecting all faces must not rescan the entire mesh for every result.");
        }

        [Test]
        public void SeparateRequestsDoNotShareFaceIndices()
        {
            var face = new EqualFace();
            var other = new EqualFace();
            var firstRequest = new Lookup();
            var secondRequest = new Lookup();

            Assert.That(Find(new CountingFaces(new object[] { face, other }), face, firstRequest), Is.EqualTo(0));
            Assert.That(Find(new CountingFaces(new object[] { other, face }), face, secondRequest), Is.EqualTo(1));
        }

        [Test]
        public void SmallSelectionsOnlyReadTheRequiredPrefix()
        {
            var first = new EqualFace();
            var second = new EqualFace();
            var faces = new CountingFaces(new object[] { first, second, new EqualFace() });
            var lookup = new Lookup();

            Assert.That(Find(faces, first, lookup), Is.EqualTo(0));
            Assert.That(faces.Reads, Is.EqualTo(1));
            Assert.That(Find(faces, second, lookup), Is.EqualTo(1));
            Assert.That(faces.Reads, Is.EqualTo(2));
            Assert.That(Find(faces, first, lookup), Is.EqualTo(0));
            Assert.That(faces.Reads, Is.EqualTo(2));
        }

        private static int Find(IList faces, object face, Lookup lookup)
        {
            Assert.That(FindFace, Is.Not.Null);
            var args = new object[] { faces, face, lookup.Indices, lookup.IndexedCount };
            int index = (int)FindFace.Invoke(null, args);
            lookup.Indices = (Dictionary<object, int>)args[2];
            lookup.IndexedCount = (int)args[3];
            return index;
        }

        private sealed class Lookup
        {
            public Dictionary<object, int> Indices;
            public int IndexedCount;
        }

        private sealed class EqualFace
        {
            public override bool Equals(object other) => other is EqualFace;

            public override int GetHashCode() => 7;
        }

        private sealed class CountingFaces : IList
        {
            private readonly object[] items;
            public int Reads { get; private set; }

            public CountingFaces(object[] items) => this.items = items;

            public object this[int index]
            {
                get
                {
                    Reads++;
                    return items[index];
                }
                set => throw new NotSupportedException();
            }

            public int Count => items.Length;
            public bool IsReadOnly => true;
            public bool IsFixedSize => true;
            public bool IsSynchronized => false;
            public object SyncRoot => this;

            public IEnumerator GetEnumerator() => items.GetEnumerator();

            public void CopyTo(Array array, int index) => items.CopyTo(array, index);

            public int Add(object value) => throw new NotSupportedException();

            public void Clear() => throw new NotSupportedException();

            public bool Contains(object value) => throw new NotSupportedException();

            public int IndexOf(object value) => throw new NotSupportedException();

            public void Insert(int index, object value) => throw new NotSupportedException();

            public void Remove(object value) => throw new NotSupportedException();

            public void RemoveAt(int index) => throw new NotSupportedException();
        }
    }
}
