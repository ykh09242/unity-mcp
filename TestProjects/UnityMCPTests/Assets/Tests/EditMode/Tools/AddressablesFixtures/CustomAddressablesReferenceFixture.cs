using System;
using UnityEngine;

namespace MCPForUnityTests.AddressablesFixtures
{
    [Serializable]
    public sealed class UserAssetReference : UnityEngine.AddressableAssets.AssetReference
    {
        public UserAssetReference(string guid)
            : base(guid) { }
    }

    [Serializable]
    public sealed class UnrelatedGuidValue
    {
        public string m_AssetGUID = new string('e', 32);
    }

    [Serializable]
    public sealed class AssetReference
    {
        public string m_AssetGUID = new string('f', 32);
    }

    public sealed class CustomAddressablesReferenceFixture : MonoBehaviour
    {
        public UserAssetReference CustomReference;
        public UnrelatedGuidValue Unrelated = new UnrelatedGuidValue();
        public AssetReference Colliding = new AssetReference();
        public string m_AssetGUID = new string('d', 32);
    }
}
