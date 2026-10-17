using System;
using System.Collections.Generic;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Fixtures
{
    public enum SerializedInspectionMode
    {
        Off = 0,
        Warm = 10,
        Hot = 42,
    }

    public enum SerializedInspectionMany
    {
        A,
        B,
        C,
        D,
        E,
        F,
        G,
        H,
        I,
        J,
        K,
        L,
        M,
        N,
        O,
        P,
        Last,
    }

    [Serializable]
    public sealed class SerializedInspectionGroup
    {
        public int marker = 23;
        public List<int> values = new List<int> { 31, 32, 33 };
    }

    [Serializable]
    public sealed class SerializedInspectionNode
    {
        public int value = 41;

        [SerializeReference]
        public SerializedInspectionNode next;
    }

    public class SerializedInspectionAssetBase : ScriptableObject
    {
        [SerializeField]
        private int inherited = 37;
        public int InheritedValue => inherited;
    }

    [Serializable]
    public class SerializedInspectionWide
    {
        public string f00 = "";
        public string f01 = "";
        public string f02 = "";
        public string f03 = "";
        public string f04 = "";
        public string f05 = "";
        public string f06 = "";
        public string f07 = "";
        public string f08 = "";
        public string f09 = "";
        public string f10 = "";
        public string f11 = "";
        public string f12 = "";
        public string f13 = "";
        public string f14 = "";
        public string f15 = "";
        public string f16 = "";
        public string f17 = "";
        public string f18 = "";
        public string f19 = "";
        public string f20 = "";
        public string f21 = "";
        public string f22 = "";
        public string f23 = "";
        public string f24 = "";
        public string f25 = "";
        public string f26 = "";
        public string f27 = "";
        public string f28 = "";
        public string f29 = "";
        public string f30 = "";
        public string f31 = "";
        public string f32 = "";
        public string f33 = "";
        public string f34 = "";
        public string f35 = "";
        public string f36 = "";
        public string f37 = "";
        public string f38 = "";
        public string f39 = "";
    }

    public class SerializedInspectionAsset : SerializedInspectionAssetBase
    {
        public static int OnEnableCalls;

        [NonSerialized]
        public int GetterCalls;

        [SerializeField]
        private int secret = 7;

        [SerializeField, HideInInspector]
        private int hidden = 19;
        public int after = 29;
        public long wide = 9007199254740993L;
        public double precise = 1.0000000000000002;
        public string text = "sample";
        public string[] texts = Array.Empty<string>();
        public SerializedInspectionMany[] manyEnums = Array.Empty<SerializedInspectionMany>();
        public SerializedInspectionMode mode = SerializedInspectionMode.Warm;
        public Material reference;
        public int[] items = { 101, 102, 103 };
        public SerializedInspectionGroup nested = new SerializedInspectionGroup();
        public List<SerializedInspectionGroup> groups = new List<SerializedInspectionGroup> { new SerializedInspectionGroup() };

        [SerializeReference]
        public SerializedInspectionNode graph;
        public SerializedInspectionWide wideFields = new SerializedInspectionWide();
        public AnimationCurve curve = AnimationCurve.Linear(0, 0, 1, 1);
        public int SecretValue => secret;
        public int HiddenValue => hidden;
        public int DangerousGetter
        {
            get
            {
                GetterCalls++;
                throw new InvalidOperationException("The inspector must not call this getter.");
            }
        }

        public void ConfigureSecret(int value) => secret = value;

        private void OnEnable() => OnEnableCalls++;
    }
}
