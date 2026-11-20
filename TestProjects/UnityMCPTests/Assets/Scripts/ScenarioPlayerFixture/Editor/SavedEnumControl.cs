using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class SavedEnumControl
    {
        public static void CreateAndCapture()
        {
            string[] args = Environment.GetCommandLineArgs();
            int flag = Array.IndexOf(args, "--enum-owner");
            if (
                !Application.isBatchMode
                || flag < 0
                || flag + 1 >= args.Length
                || !Guid.TryParseExact(args[flag + 1], "N", out _)
                || File.ReadAllText(".enum-value-owner") != args[flag + 1]
                || Directory.Exists("Assets/SavedEnumControl")
            )
                throw new InvalidOperationException("Select a fresh owned disposable project");
            EditorSettings.serializationMode = SerializationMode.ForceText;
            if (string.IsNullOrEmpty(AssetDatabase.CreateFolder("Assets", "SavedEnumControl")))
                throw new IOException("Cannot create the prefab fixture folder");
            var root = new GameObject("Encounter");
            try
            {
                root.AddComponent<SavedEnumProbe>().State = EncounterState.Combat;
                PrefabUtility.SaveAsPrefabAsset(root, "Assets/SavedEnumControl/Encounter.prefab", out bool saved);
                if (!saved)
                    throw new IOException("Failed to save the original prefab");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
            AssetDatabase.SaveAssets();
            VerificationEnumValue.CaptureFromCommandLine();
        }
    }
}
