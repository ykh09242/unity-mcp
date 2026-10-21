using System;
using System.Collections.Generic;
using System.Diagnostics;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Host-owned scratch storage; queries clear references and idle hosts trim large buffers.</summary>
    internal sealed class PlayScenarioHierarchyQuery
    {
        internal const int MaximumInspectedObjects = 250000;
        private const int RetainedSmallCapacity = 4096;
        internal readonly List<GameObject> Roots = new();
        private readonly Stack<(Transform Node, int Segment)> pending = new();
        private readonly Func<long> clock;
        private long lastUsed;
        private bool largePending;
        internal int Count => pending.Count;

        internal PlayScenarioHierarchyQuery(Func<long> clock = null)
        {
            this.clock = clock ?? Stopwatch.GetTimestamp;
        }

        internal void Begin(Scene scene)
        {
            Maintain();
            Clear();
            lastUsed = clock();
            if (scene.rootCount > MaximumInspectedObjects)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
            if (Roots.Capacity <= scene.rootCount)
                Roots.Capacity = scene.rootCount + 1;
            scene.GetRootGameObjects(Roots);
        }

        internal void Push(Transform node, int segment)
        {
            if (pending.Count >= RetainedSmallCapacity)
                largePending = true;
            pending.Push((node, segment));
        }

        internal (Transform Node, int Segment) Pop() => pending.Pop();

        internal void Clear()
        {
            Roots.Clear();
            pending.Clear();
        }

        internal void Maintain()
        {
            if ((!largePending && Roots.Capacity <= RetainedSmallCapacity) || Roots.Count != 0 || pending.Count != 0)
                return;
            if (clock() - lastUsed < Stopwatch.Frequency * 30L)
                return;
            if (Roots.Capacity > RetainedSmallCapacity)
                Roots.Capacity = RetainedSmallCapacity;
            if (largePending)
            {
                pending.TrimExcess();
                largePending = false;
            }
        }

        internal void Release()
        {
            Clear();
            Roots.Capacity = 0;
            pending.TrimExcess();
            largePending = false;
        }
    }
}
