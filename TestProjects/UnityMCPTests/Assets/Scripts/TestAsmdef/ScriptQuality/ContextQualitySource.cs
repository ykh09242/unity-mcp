using UnityEngine;

namespace MCPQualityContext
{
    internal partial class SelectedQualitySource
    {
        private const int ContextValue = 42;

        internal string ContextReadName(GameObject value) => value?.name;
    }
}
