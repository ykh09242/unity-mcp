using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Host-owned scratch storage, cleared after every query.</summary>
    internal sealed class PlayScenarioHierarchyQuery
    {
        internal const int MaximumInspectedObjects = 250000;
        internal readonly List<GameObject> Roots = new();
        private readonly Stack<(Transform Node, int Segment)> pending = new();
        private bool trimPending;
        internal int Count => pending.Count;

        internal void Begin(Scene scene)
        {
            Clear();
            if (scene.rootCount > MaximumInspectedObjects)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
            if (Roots.Capacity <= scene.rootCount)
                Roots.Capacity = scene.rootCount + 1;
            scene.GetRootGameObjects(Roots);
        }

        internal void Push(Transform node, int segment)
        {
            if (pending.Count >= 4096)
                trimPending = true;
            pending.Push((node, segment));
        }

        internal (Transform Node, int Segment) Pop() => pending.Pop();

        internal void Clear()
        {
            Roots.Clear();
            pending.Clear();
            if (Roots.Capacity > 4096)
                Roots.Capacity = 4096;
            if (trimPending)
            {
                pending.TrimExcess();
                trimPending = false;
            }
        }
    }
}
