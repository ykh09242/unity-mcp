using System.Collections.Generic;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceDiagnosticFixture : ScriptableObject
    {
        public Object reference;
        public List<Object> references = new List<Object>();
    }
}
