using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    // No FilePath: ownership survives domain reloads without creating project files.
    // Undo owns historical objects. Pruning drops our references; it never destroys a material
    // that a scene, another renderer or an Undo record could still use.
    internal sealed class MaterialLifetimeStore : ScriptableSingleton<MaterialLifetimeStore>
    {
        [Serializable]
        private sealed class Entry
        {
            public Renderer Owner;
            public int Slot;
            public Material Material;
        }

        [SerializeField]
        private List<Entry> entries = new List<Entry>();

        [NonSerialized]
        private double nextPrune;

        [NonSerialized]
        private bool scheduled;
        internal int OwnedCount => entries.Count;

        private void OnEnable()
        {
            Undo.undoRedoPerformed -= OnUndoRedo;
            Undo.undoRedoPerformed += OnUndoRedo;
            StopUpdating();
            if (entries.Count > 0)
                Schedule();
        }

        private void OnDisable()
        {
            Undo.undoRedoPerformed -= OnUndoRedo;
            StopUpdating();
        }

        private void OnUndoRedo()
        {
            // Undo can restore serialized ownership after an empty registry stopped
            // polling; its nonserialized scheduling flag is not part of Undo state.
            if (entries.Count > 0)
                Schedule();
            else
                StopUpdating();
        }

        private void StopUpdating()
        {
            EditorApplication.update -= Update;
            scheduled = false;
        }

        private void Schedule()
        {
            if (scheduled)
                return;
            nextPrune = EditorApplication.timeSinceStartup + 10;
            EditorApplication.update += Update;
            scheduled = true;
        }

        private void Update()
        {
            if (EditorApplication.timeSinceStartup < nextPrune)
                return;
            Prune();
            nextPrune = EditorApplication.timeSinceStartup + 10;
        }

        private void Prune()
        {
            entries.RemoveAll(entry =>
                entry.Owner == null
                || entry.Material == null
                || EditorUtility.IsPersistent(entry.Material)
                || entry.Slot >= entry.Owner.sharedMaterials.Length
                || entry.Owner.sharedMaterials[entry.Slot] != entry.Material
            );
            if (entries.Count == 0)
                StopUpdating();
        }

        // The caller owns the Undo group and rolls it back if a later edit/readback fails.
        internal Material PrepareInstance(Renderer renderer, int slot, Material source, string undoLabel)
        {
            Entry owned = entries.Find(entry => entry.Owner == renderer && entry.Slot == slot);
            Material current = renderer.sharedMaterials[slot];
            if (
                owned != null
                && owned.Material != null
                && owned.Material == current
                && !EditorUtility.IsPersistent(current)
                && IsExclusive(current, renderer, slot)
            )
            {
                Undo.RegisterCompleteObjectUndo(current, undoLabel);
                if (source != current)
                {
                    current.shader = source.shader;
                    current.CopyPropertiesFromMaterial(source);
                }
                return current;
            }

            var material = new Material(source) { name = source.name + " (MCP Instance)" };
            Undo.RegisterCreatedObjectUndo(material, undoLabel);
            // Redo must recreate the material before restoring ownership references.
            Undo.RegisterCompleteObjectUndo(this, undoLabel);
            Undo.RegisterCompleteObjectUndo(renderer, undoLabel);
            var slots = renderer.sharedMaterials;
            slots[slot] = material;
            renderer.sharedMaterials = slots;
            entries.RemoveAll(entry => entry.Owner == renderer && entry.Slot == slot);
            entries.Add(
                new Entry
                {
                    Owner = renderer,
                    Slot = slot,
                    Material = material,
                }
            );
            EditorUtility.SetDirty(this);
            EditorUtility.SetDirty(renderer);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            Schedule();
            return material;
        }

        private static bool IsExclusive(Material material, Renderer owner, int slot)
        {
            // Recheck on an explicit edit, never per frame: copying a renderer or assigning
            // its material through another editor/script can create an unobserved shared use.
            foreach (Renderer renderer in UnityEngine.Resources.FindObjectsOfTypeAll<Renderer>())
            {
                var materials = renderer.sharedMaterials;
                for (int index = 0; index < materials.Length; index++)
                    if (materials[index] == material && (renderer != owner || index != slot))
                        return false;
            }
            return true;
        }
    }
}
