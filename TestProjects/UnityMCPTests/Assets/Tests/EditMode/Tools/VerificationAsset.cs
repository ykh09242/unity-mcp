using System;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    // A persisted ScriptableObject needs a matching MonoScript file to survive import/reload.
    public class VerificationAsset : ScriptableObject
    {
        public UnityEngine.Object reference;
        public int[] values = Array.Empty<int>();
        public int number;
        public string text;
        public VerificationChoice choice;
    }
}
