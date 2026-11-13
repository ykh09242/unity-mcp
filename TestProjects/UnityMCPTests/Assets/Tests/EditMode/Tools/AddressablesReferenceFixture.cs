using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public sealed class AddressablesReferenceFixture : ScriptableObject
    {
        // The installed package's real serializable AssetReference is assigned through reflection.
        [SerializeReference]
        public object Reference;

        [SerializeReference]
        public object[] References = new object[0];
    }
}
