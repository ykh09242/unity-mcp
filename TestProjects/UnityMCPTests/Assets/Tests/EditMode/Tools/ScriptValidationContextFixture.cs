namespace MCPForUnityTests.Editor.Tools
{
    internal partial class ScriptValidationContextFixture
    {
#if UNITY_EDITOR
        internal int ReadFromOtherPartial() => PartialValue;
#else
#error Validation must use the owning assembly's defines.
#endif
    }
}
