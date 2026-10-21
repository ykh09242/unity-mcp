using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Uses Unity's component type lookup without retaining live scene objects between observations.</summary>
    internal static class PlayScenarioTargetLookup
    {
        internal struct Matches
        {
            internal GameObject Target;
            internal int Count;
            internal int ActiveCount;
        }

        internal static Matches Resolve(Scene scene, string identifier, ref PlayScenarioQueryCounts counts)
        {
            var matches = new Matches();
            if (!scene.IsValid() || !scene.isLoaded)
                return matches;
            if (!PlayScenarioTarget.IsValidTargetId(identifier))
                throw new ArgumentException("target_id must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.");
            int inspected = 0;
            // Include inactive, hidden and preview-scene markers; filter prefab assets and other scenes explicitly.
            // Enable/disable callbacks alone cannot detect a newly instantiated inactive duplicate in a Player.
            foreach (PlayScenarioTarget marker in Resources.FindObjectsOfTypeAll<PlayScenarioTarget>())
            {
                if (marker == null || marker.gameObject.scene != scene)
                    continue;
                counts.HierarchyVisits++;
                if (++inspected > PlayScenarioHierarchyQuery.MaximumInspectedObjects)
                    throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
                if (!string.Equals(marker.TargetId, identifier, StringComparison.Ordinal))
                    continue;
                matches.Count++;
                if (marker.gameObject.activeInHierarchy)
                    matches.ActiveCount++;
                if (matches.Target == null)
                    matches.Target = marker.gameObject;
                if (matches.Count > 1)
                    throw new PlayScenarioException(
                        new PlayScenarioFailure
                        {
                            Code = "target_ambiguous",
                            Target = identifier,
                            Expected = "one unique ID in the active scene",
                            Actual = "at least two matching markers, including inactive objects",
                            Message = "Target ID is ambiguous in the active scene, including inactive objects.",
                        }
                    );
            }
            return matches;
        }
    }
}
