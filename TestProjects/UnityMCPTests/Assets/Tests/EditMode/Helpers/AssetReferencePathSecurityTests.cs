using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class AssetReferencePathSecurityTests
    {
        [Test]
        public void OrdinaryAssetsRootAndRelativeReferencesRetainTheirCanonicalPaths()
        {
            Assert.AreEqual("Assets", AssetPathUtility.SanitizeAssetPath("assets"));
            Assert.AreEqual("Assets/ReferenceProof/Sub.asset", AssetPathUtility.SanitizeAssetPath("ReferenceProof\\Sub.asset"));
            Assert.AreEqual("Assets/ReferenceProof/Sub.asset", UnityAssetPath.Resolve("assets/ReferenceProof/Sub.asset", allowPackages: true));
        }

        [TestCase("/Assets/Test.asset")]
        [TestCase("C:/Assets/Test.asset")]
        [TestCase("Assets/../Test.asset")]
        [TestCase("Assets//Test.asset")]
        [TestCase("Assets/./Test.asset")]
        public void LegacySanitizerRejectsInvalidPathsBeforeTheAssetDatabase(string path)
            => Assert.Throws<ArgumentException>(() => AssetPathUtility.SanitizeAssetPath(path));

        [TestCase("Resources/unity_builtin_extra")]
        [TestCase("Library/unity default resources")]
        public void BuiltInReferencesRequireAnExplicitExactPathPolicy(string path)
        {
            Assert.AreEqual(path, UnityAssetPath.Resolve(path, allowBuiltIn: true));
            Assert.Throws<ArgumentException>(() => UnityAssetPath.Resolve(path));
            Assert.AreNotEqual(path + "/Other.asset", UnityAssetPath.Resolve(path + "/Other.asset", allowBuiltIn: true));
        }

        [Test]
        public void UnregisteredPackagesAndMutationPackagePathsAreRejected()
        {
            const string path = "Packages/com.mcp.reference-proof-unregistered/Probe.asset";
            Assert.Throws<ArgumentException>(() => UnityAssetPath.Resolve(path));
            Assert.Throws<ArgumentException>(() => UnityAssetPath.Resolve(path, allowPackages: true));
        }

        [Test]
        public void VolumeRootsRetainTheirSeparatorAndAcceptContainedPaths()
        {
            string fullPath = Path.GetFullPath(Application.dataPath);
            string volumeRoot = Path.GetPathRoot(fullPath);
            Assert.AreEqual(fullPath, SafePathUtility.ResolveWithinRoot(volumeRoot, fullPath));
            Assert.AreEqual(volumeRoot, SafePathUtility.ResolveWithinRoot(volumeRoot, "."));
        }

        [Test]
        public void TransientObjectIdsRemainUsableWithoutAnAssetPath()
        {
            var texture = new Texture2D(1, 1);
            try
            {
                var serializer = JsonSerializer.Create();
                serializer.Converters.Add(new UnityEngineObjectConverter());
                Assert.AreSame(texture, new JObject { ["instanceID"] = texture.GetInstanceID() }.ToObject<Texture>(serializer));
            }
            finally { UnityEngine.Object.DestroyImmediate(texture); }
        }
    }
}
