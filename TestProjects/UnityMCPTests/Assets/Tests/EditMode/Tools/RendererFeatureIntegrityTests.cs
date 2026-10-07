using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.Graphics;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class RendererFeatureIntegritySlots : ScriptableObject
    {
        public Object[] features;
    }

    [Parallelizable(ParallelScope.None)]
    public class RendererFeatureIntegrityTests
    {
        private readonly List<Object> _owned = new List<Object>();

        private RendererFeatureIntegritySlots NewSlots(params Object[] values)
        {
            var slots = ScriptableObject.CreateInstance<RendererFeatureIntegritySlots>();
            _owned.Add(slots);
            slots.features = values;
            return slots;
        }

        private Object NewIdentity()
        {
            var identity = ScriptableObject.CreateInstance<RendererFeatureIntegritySlots>();
            _owned.Add(identity);
            return identity;
        }

        private static void RemoveSlot(RendererFeatureIntegritySlots slots, int index)
        {
            var method = typeof(RendererFeatureOps).GetMethod("RemoveSerializedFeatureSlot", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method, "The production deletion helper must be available.");
            using (var serialized = new SerializedObject(slots))
            {
                var property = serialized.FindProperty("features");
                Assert.IsNotNull(property);
                Assert.IsTrue(property.isArray);
                method.Invoke(null, new object[] { property, index });
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
        }

        [TearDown]
        public void TearDown()
        {
            for (int i = _owned.Count - 1; i >= 0; i--)
            {
                if (_owned[i] != null)
                    Object.DestroyImmediate(_owned[i]);
            }
            _owned.Clear();
        }

        [TestCase(true, true)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(false, false)]
        public void RemovingMiddleSlotPreservesAdjacentIdentity(bool emptyTarget, bool emptyNext)
        {
            var first = NewIdentity();
            var target = emptyTarget ? null : NewIdentity();
            var next = emptyNext ? null : NewIdentity();
            var last = NewIdentity();
            var slots = NewSlots(first, target, next, last);

            RemoveSlot(slots, 1);

            CollectionAssert.AreEqual(new Object[] { first, next, last }, slots.features);
            Assert.IsFalse(EditorUtility.IsPersistent(slots));
            if (!emptyTarget)
                Assert.IsTrue(target != null, "Deleting a list slot must not destroy its borrowed object.");
        }

        [TestCase(true)]
        [TestCase(false)]
        public void RemovingLastSlotShrinksExactlyOnce(bool emptyTarget)
        {
            var first = NewIdentity();
            var slots = NewSlots(first, emptyTarget ? null : NewIdentity());

            RemoveSlot(slots, 1);

            CollectionAssert.AreEqual(new Object[] { first }, slots.features);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void RemovingOnlySlotLeavesEmptyArray(bool emptyTarget)
        {
            var slots = NewSlots(emptyTarget ? null : NewIdentity());

            RemoveSlot(slots, 0);

            Assert.AreEqual(0, slots.features.Length);
        }
    }
}
