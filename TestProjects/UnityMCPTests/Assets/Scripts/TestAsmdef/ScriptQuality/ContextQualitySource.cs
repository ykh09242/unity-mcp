using UnityEngine;

namespace MCPQualityContext
{
    internal partial class SelectedQualitySource
    {
        private const int ContextValue = 42;

#if MCP_QUALITY_DIAGNOSTIC_FIXTURE
        internal string ContextReadName(GameObject value) => value?.name;
#else
        internal string ContextReadName(GameObject value) => value != null ? value.name : null;
#endif
    }
}
