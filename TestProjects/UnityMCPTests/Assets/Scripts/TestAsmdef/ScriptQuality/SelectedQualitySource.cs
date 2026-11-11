using UnityEngine;

namespace MCPQualityContext
{
    internal partial class SelectedQualitySource
    {
        internal string ReadName(GameObject value) => value?.name;

        internal int PartialValue() => ContextValue;

#if MCP_QUALITY_INACTIVE
        private MissingInactiveType inactive;
#endif
    }
}
