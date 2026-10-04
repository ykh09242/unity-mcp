using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Fixtures
{
    [System.Serializable]
    public struct ScriptableObjectContractNested
    {
        public int[] numbers;
        public string text;
    }

    [System.Serializable]
    public class ScriptableObjectContractManaged
    {
        public int[] numbers = { 1, 2 };
        public string text = "managed";
    }

    public class ScriptableObjectContractDefinition : ScriptableObject
    {
        public int intValue = 7;
        public long longValue = 7;
        public double doubleValue = 7;
        public float floatValue = 7;
        public int[] items = { 7, 8 };
        public bool enabledValue = true;
        public string textValue = "fixture";
        public ScriptableObjectContractNested nested = new() { numbers = new[] { 1, 2 }, text = "nested" };
        public ScriptableObjectContractNested[] groups = { new() { numbers = new[] { 1, 2 }, text = "group" } };
        [SerializeReference] public ScriptableObjectContractManaged[] managed = { new() };
    }

    public abstract class AbstractScriptableObjectContractDefinition : ScriptableObject { }
}
