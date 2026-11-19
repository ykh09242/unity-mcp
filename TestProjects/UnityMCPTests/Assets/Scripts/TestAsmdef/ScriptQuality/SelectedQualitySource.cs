using UnityEngine;

namespace MCPQualityContext
{
    internal partial class SelectedQualitySource
    {
#if MCP_QUALITY_DIAGNOSTIC_FIXTURE
        internal string ReadName(GameObject value) => value?.name;
#else
        internal string ReadName(GameObject value) => value != null ? value.name : null;
#endif

        internal int PartialValue() => ContextValue;

#if MCP_QUALITY_INACTIVE
        private MissingInactiveType inactive;
#endif
    }
}
