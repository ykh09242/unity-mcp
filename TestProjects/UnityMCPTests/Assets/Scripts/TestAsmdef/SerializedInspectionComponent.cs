using System;
using System.Collections.Generic;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Fixtures
{
    public class SerializedInspectionComponentBase : MonoBehaviour
    {
        [SerializeField]
        private int inherited = 31;
        public int InheritedValue => inherited;
    }

    public class SerializedInspectionComponent : SerializedInspectionComponentBase
    {
        [NonSerialized]
        public int GetterCalls;

        [SerializeField]
        private int secret = 11;

        [SerializeField, HideInInspector]
        private int hidden = 17;
        public SerializedInspectionMode mode = SerializedInspectionMode.Warm;
        public Material reference;
        public int[] items = { 201, 202, 203 };
        public SerializedInspectionGroup nested = new SerializedInspectionGroup();
        public List<SerializedInspectionGroup> groups = new List<SerializedInspectionGroup> { new SerializedInspectionGroup() };

        [SerializeReference]
        public SerializedInspectionNode graph;
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
    }
}
