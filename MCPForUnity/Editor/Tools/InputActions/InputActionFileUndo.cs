using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.InputActions
{
    internal sealed class InputActionUndoState : ScriptableObject
    {
        [SerializeField]
        internal string Bytes;
    }

    [InitializeOnLoad]
    internal static class InputActionFileUndo
    {
        private sealed class Record
        {
            internal string Path,
                Guid,
                Before,
                After,
                Applied;
            internal bool Conflict;
            internal InputActionUndoState State;
        }

        private static readonly List<Record> Records = new();

        static InputActionFileUndo()
        {
            Undo.undoRedoPerformed += Restore;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
        }

        internal static string Status(string path) => Records.Any(record => record.Path == path && record.Conflict) ? "conflict" : "ready";

        internal static void Apply(string path, string guid, byte[] before, byte[] after)
        {
            foreach (Record expired in Records.Where(record => !File.Exists(AssetPathUtility.GetFullAssetPath(record.Path))).ToArray())
            {
                Release(expired);
                Records.Remove(expired);
            }
            if (Records.Count >= 32)
                throw new InvalidOperationException("Current-domain InputAction Undo budget reached (32 edits); no source write was attempted.");
            var state = ScriptableObject.CreateInstance<InputActionUndoState>();
            state.hideFlags = HideFlags.HideAndDontSave;
            state.name = "InputAction source edit";
            var record = new Record
            {
                Path = path,
                Guid = guid,
                Before = Convert.ToBase64String(before),
                After = Convert.ToBase64String(after),
                State = state,
            };
            state.Bytes = record.Before;
            Undo.RegisterCompleteObjectUndo(state, "Edit InputAction source");
            try
            {
                Write(path, guid, before, after);
                record.Applied = record.After;
                state.Bytes = record.After;
                Records.Add(record);
                Undo.FlushUndoRecordObjects();
                Import(path, guid);
            }
            catch
            {
                if (!Records.Contains(record))
                    Release(record);
                throw;
            }
        }

        private static void Write(string path, string guid, byte[] expected, byte[] replacement)
        {
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (AssetDatabase.AssetPathToGUID(path) != guid)
                throw new InvalidOperationException("InputAction asset identity changed; no file restoration was attempted.");
            var meta = new FileInfo(full + ".meta");
            if (!meta.Exists || meta.Length > 16384 || !File.ReadAllLines(meta.FullName).Contains("guid: " + guid))
                throw new InvalidOperationException("InputAction metadata identity changed; no source write was attempted.");
            using var stream = File.Open(full, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            byte[] current = InputActionDocument.ReadStream(stream);
            if (!current.SequenceEqual(expected))
                throw new InvalidOperationException("InputAction source changed externally; conditional restoration was refused.");
            stream.Position = 0;
            stream.Write(replacement, 0, replacement.Length);
            stream.SetLength(replacement.Length);
            stream.Flush(true);
        }

        private static void Import(string path, string guid)
        {
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            if (AssetDatabase.AssetPathToGUID(path) != guid || AssetDatabase.LoadAssetAtPath(path, ManageInputActions.AssetType) == null)
                throw new InvalidOperationException(
                    "InputAction source was written, but native import or identity verification failed. Inspect before retrying."
                );
        }

        private static void Restore()
        {
            // A native Undo group may contain several writes: undo newest first, redo oldest first.
            var pending = Records.Where(record => record.State != null && record.State.Bytes != record.Applied && !record.Conflict).ToArray();
            foreach (Record record in pending.Where(record => record.State.Bytes == record.Before).Reverse())
                Restore(record);
            foreach (Record record in pending.Where(record => record.State.Bytes == record.After))
                Restore(record);
        }

        private static void Restore(Record record)
        {
            try
            {
                Write(record.Path, record.Guid, Convert.FromBase64String(record.Applied), Convert.FromBase64String(record.State.Bytes));
                record.Applied = record.State.Bytes;
                Import(record.Path, record.Guid);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is InvalidOperationException || ex is ArgumentException)
            {
                record.Conflict = true;
                Debug.LogWarning(
                    "InputAction file Undo/Redo could not restore its recorded source; drift never authorizes overwriting external bytes: " + ex.Message
                );
            }
        }

        private static void Release(Record record)
        {
            if (record.State == null)
                return;
            Undo.ClearUndo(record.State);
            UnityEngine.Object.DestroyImmediate(record.State);
        }

        private static void Clear()
        {
            foreach (Record record in Records)
                Release(record);
            Records.Clear();
        }
    }
}
