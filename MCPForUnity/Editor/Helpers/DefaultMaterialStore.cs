using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    // Serialized Editor-session ownership survives domain reload. No FilePath or project assets.
    internal sealed class DefaultMaterialStore : ScriptableSingleton<DefaultMaterialStore>
    {
        [Serializable]
        private sealed class Entry
        {
            public string Key;
            public Material Material;
            public bool Owned;
        }

        private const int MaximumEntries = 32;

        [SerializeField]
        private List<Entry> entries = new List<Entry>();

        private void OnEnable()
        {
            EditorApplication.quitting -= DestroyOwnedMaterials;
            EditorApplication.quitting += DestroyOwnedMaterials;
        }

        private void OnDisable() => EditorApplication.quitting -= DestroyOwnedMaterials;

        internal bool TryGet(string key, out Material material)
        {
            material = entries.Find(entry => entry.Key == key)?.Material;
            return material != null;
        }

        internal void Store(string key, Material material, bool owned)
        {
            entries.RemoveAll(entry => entry.Material == null);
            Entry existing = entries.Find(entry => entry.Key == key);
            if (existing != null)
            {
                if (existing.Material != material)
                    throw new InvalidOperationException("A live default material cannot be replaced in its cache slot.");
                return;
            }
            if (entries.Count >= MaximumEntries)
            {
                if (owned && material != null && !EditorUtility.IsPersistent(material))
                    UnityEngine.Object.DestroyImmediate(material);
                throw new InvalidOperationException("Generated default material capacity reached.");
            }
            entries.Add(
                new Entry
                {
                    Key = key,
                    Material = material,
                    Owned = owned,
                }
            );
        }

        private void DestroyOwnedMaterials()
        {
            foreach (Entry entry in entries)
                if (entry.Owned && entry.Material != null && !EditorUtility.IsPersistent(entry.Material))
                    UnityEngine.Object.DestroyImmediate(entry.Material);
            entries.Clear();
        }
    }
}
