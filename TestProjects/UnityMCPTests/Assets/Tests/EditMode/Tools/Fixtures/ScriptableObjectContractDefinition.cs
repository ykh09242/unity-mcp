using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Fixtures
{
    public class ScriptableObjectContractDefinition : ScriptableObject
    {
        public int intValue = 7;
        public long longValue = 7;
        public int[] items = { 7, 8 };
        public bool enabledValue = true;
        public string textValue = "fixture";
    }

    public abstract class AbstractScriptableObjectContractDefinition : ScriptableObject { }
}
