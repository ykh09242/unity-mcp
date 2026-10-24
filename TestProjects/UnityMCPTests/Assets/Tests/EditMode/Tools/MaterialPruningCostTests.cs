using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class MaterialPruningCostTests
    {
        private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
        private readonly List<GameObject> objects = new List<GameObject>();
        private readonly List<Renderer> owners = new List<Renderer>();
        private readonly List<FrozenEntry> baseline = new List<FrozenEntry>();
        private readonly List<FrozenEntry> originalBaseline = new List<FrozenEntry>();
        private readonly List<object> originalEntries = new List<object>();
        private readonly List<object> savedEntries = new List<object>();
        private ScriptableObject store;
        private IList entries;
        private Action prune;
        private Material material;
        private Material replacement;
        private Type entryType;

        [SetUp]
        public void SetUp()
        {
            Type type = typeof(ManageMaterial).Assembly.GetType("MCPForUnity.Editor.Helpers.MaterialLifetimeStore", true);
            store = (ScriptableObject)type.GetProperty("instance", BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy).GetValue(null);
            entries = (IList)type.GetField("entries", PrivateInstance).GetValue(store);
            foreach (object entry in entries)
                savedEntries.Add(entry);
            entries.Clear();
            entryType = type.GetNestedType("Entry", BindingFlags.NonPublic);
            prune = (Action)Delegate.CreateDelegate(typeof(Action), store, type.GetMethod("Prune", PrivateInstance));
            material = new Material(Shader.Find("Standard"));
            replacement = new Material(Shader.Find("Standard"));
        }

        [TearDown]
        public void TearDown()
        {
            entries.Clear();
            foreach (object entry in savedEntries)
                entries.Add(entry);
            savedEntries.Clear();
            store.GetType().GetMethod("OnUndoRedo", PrivateInstance).Invoke(store, null);
            foreach (var item in objects)
                if (item != null)
                    UnityEngine.Object.DestroyImmediate(item);
            UnityEngine.Object.DestroyImmediate(material);
            UnityEngine.Object.DestroyImmediate(replacement);
            objects.Clear();
            owners.Clear();
            baseline.Clear();
            originalEntries.Clear();
            originalBaseline.Clear();
        }

        private void Populate(int rendererCount, int slotCount)
        {
            for (int rendererIndex = 0; rendererIndex < rendererCount; rendererIndex++)
            {
                var item = new GameObject("MaterialPruningFixture");
                objects.Add(item);
                var owner = item.AddComponent<MeshRenderer>();
                owners.Add(owner);
                var slots = new Material[slotCount];
                for (int slot = 0; slot < slotCount; slot++)
                {
                    slots[slot] = material;
                    object entry = Activator.CreateInstance(entryType, true);
                    entryType.GetField("Owner").SetValue(entry, owner);
                    entryType.GetField("Slot").SetValue(entry, slot);
                    entryType.GetField("Material").SetValue(entry, material);
                    originalEntries.Add(entry);
                    originalBaseline.Add(
                        new FrozenEntry
                        {
                            Owner = owner,
                            Slot = slot,
                            Material = material,
                        }
                    );
                }
                owner.sharedMaterials = slots;
            }
            RestoreEntries();
        }

        private void RestoreEntries()
        {
            entries.Clear();
            foreach (object entry in originalEntries)
                entries.Add(entry);
            baseline.Clear();
            baseline.AddRange(originalBaseline);
        }

        [Test]
        public void PruneDropsOnlyInvalidBindingsAndNeverDestroysHistoricalMaterials()
        {
            Populate(4, 4);
            owners[0].sharedMaterials = new[] { material, replacement, material, material };
            owners[1].sharedMaterials = new[] { material };
            UnityEngine.Object.DestroyImmediate(objects[2]);
            FrozenPrune();
            prune();
            AssertBindingsEqual();
            Assert.AreEqual(8, entries.Count);
            Assert.IsTrue(material != null, "Pruning metadata cannot destroy materials retained by Undo or another renderer.");
            Assert.IsTrue(replacement != null);
            owners[0].sharedMaterials = new[] { material, material, material, material };
            RestoreEntries();
            FrozenPrune();
            prune();
            AssertBindingsEqual();
        }

        [Test]
        public void WarmStablePruneAllocatesAtMostOneSlotArrayPerRenderer()
        {
            Populate(128, 8);
            prune();
            long allocations;
            try
            {
                Assert.Greater(ManagedAllocation.Count(() => GC.KeepAlive(new byte[65536])), 0, "The recorder must detect a positive control.");
                allocations = ManagedAllocation.Count(prune);
            }
            catch (InvalidOperationException error)
            {
                Assert.Ignore(error.Message);
                return;
            }
            Assert.LessOrEqual(allocations, 128, "Pruning must read at most one copied slot array per renderer.");
            Assert.AreEqual(1024, entries.Count);
        }

        [TestCase(128, 1, false)]
        [TestCase(1024, 8, false)]
        [TestCase(1024, 8, true)]
        [Explicit("Serialized native material pruning measurements.")]
        public void PairedPruningMeasurements(int rendererCount, int slotCount, bool churn)
        {
            Populate(rendererCount, slotCount);
            if (churn)
                for (int index = 0; index < owners.Count; index += 3)
                    owners[index].sharedMaterials = new[] { replacement };
            FrozenPrune();
            prune();
            for (int sample = 0; sample < 40; sample++)
            {
                RestoreEntries();
                for (int pass = 0; pass < 2; pass++)
                {
                    bool candidate = (sample % 2 == 0 ? pass : 1 - pass) == 1;
                    Action action = candidate ? prune : FrozenPrune;
                    var watch = new Stopwatch();
                    using var process = Process.GetCurrentProcess();
                    TimeSpan cpuBefore = process.TotalProcessorTime;
                    watch.Start();
                    action();
                    watch.Stop();
                    double cpu = (process.TotalProcessorTime - cpuBefore).TotalMilliseconds;
                    long allocations = -1;
                    // Restore outside the allocation measurement, including the churn case.
                    RestoreEntriesFor(candidate);
                    try
                    {
                        allocations = ManagedAllocation.Count(action);
                    }
                    catch (InvalidOperationException) { /* Report an unavailable counter without substituting retained memory. */ }
                    TestContext.WriteLine(
                        $"BPA14 renderers={rendererCount} slots={slotCount} churn={churn} sample={sample} candidate={candidate} main_thread_ms={watch.Elapsed.TotalMilliseconds:F6} cpu_ms={cpu:F6} gc_events={allocations} observed_native_bytes={Profiler.GetTotalAllocatedMemoryLong()} observed_managed_bytes={GC.GetTotalMemory(false)} process_peak_working_set_bytes={process.PeakWorkingSet64}"
                    );
                }
                AssertBindingsEqual();
            }
        }

        private Func<Material, Renderer, int, bool> ProductionExclusive()
        {
            return (Func<Material, Renderer, int, bool>)
                Delegate.CreateDelegate(
                    typeof(Func<Material, Renderer, int, bool>),
                    store.GetType().GetMethod("IsExclusive", BindingFlags.Static | BindingFlags.NonPublic)
                );
        }

        [Test]
        public void ExclusivityScanSeesExternalAssignmentsAndOtherSlots()
        {
            Populate(128, 8);
            owners[0].sharedMaterials = new[] { replacement };
            var candidate = ProductionExclusive();
            Assert.IsTrue(candidate(replacement, owners[0], 0));
            owners[1].sharedMaterials = new[] { replacement };
            Assert.IsFalse(candidate(replacement, owners[0], 0), "External assignment must be visible immediately.");
            owners[1].sharedMaterials = new[] { material };
            owners[0].sharedMaterials = new[] { replacement, replacement };
            Assert.IsFalse(candidate(replacement, owners[0], 0), "Another slot on the same owner is also shared.");
            owners[0].sharedMaterials = new[] { replacement };
            Assert.IsTrue(candidate(replacement, owners[0], 0));
        }

        private void RestoreEntriesFor(bool candidate)
        {
            if (candidate)
            {
                entries.Clear();
                foreach (object entry in originalEntries)
                    entries.Add(entry);
            }
            else
            {
                baseline.Clear();
                baseline.AddRange(originalBaseline);
            }
        }

        private void AssertBindingsEqual()
        {
            Assert.AreEqual(baseline.Count, entries.Count);
            for (int index = 0; index < entries.Count; index++)
            {
                Assert.AreSame(baseline[index].Owner, entryType.GetField("Owner").GetValue(entries[index]));
                Assert.AreEqual(baseline[index].Slot, entryType.GetField("Slot").GetValue(entries[index]));
                Assert.AreSame(baseline[index].Material, entryType.GetField("Material").GetValue(entries[index]));
            }
        }

        // Frozen beta predicate. The baseline list is populated from the identical renderer bindings.
        private sealed class FrozenEntry
        {
            internal Renderer Owner;
            internal int Slot;
            internal Material Material;
        }

        private void FrozenPrune()
        {
            baseline.RemoveAll(entry =>
                entry.Owner == null
                || entry.Material == null
                || EditorUtility.IsPersistent(entry.Material)
                || entry.Slot >= entry.Owner.sharedMaterials.Length
                || entry.Owner.sharedMaterials[entry.Slot] != entry.Material
            );
        }
    }
}
