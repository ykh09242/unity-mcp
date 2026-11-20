using System;
using System.IO;
using System.Security.Cryptography;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace VerificationFixtures
{
    // Install in an owned disposable project's EditMode test assembly to opt in.
    public sealed class NavigationMaterialChecks
    {
        public const string Folder = "Assets/__McpNavigationCheckFixture";
        public const string Map = Folder + "/MapMarkers.mat";
        public const string Route = Folder + "/RouteArrows.mat";
        private Guid executionId;

        [OneTimeSetUp]
        public void OpenFixture()
        {
            if (Environment.GetEnvironmentVariable("UNITY_MCP_UNMAPPED_CHECK_FIXTURE") != "1")
                Assert.Ignore("Enable the navigation material fixture in an owned disposable project.");
            executionId = Guid.NewGuid();
            if (!AssetDatabase.IsValidFolder(Folder))
            {
                string guid = AssetDatabase.CreateFolder("Assets", "__McpNavigationCheckFixture");
                Assert.AreEqual(Folder, AssetDatabase.GUIDToAssetPath(guid));
                var shader = Shader.Find("Hidden/InternalErrorShader");
                Assert.IsNotNull(shader);
                AssetDatabase.CreateAsset(new Material(shader) { enableInstancing = true }, Map);
                // The unmapped asset deliberately violates the same project-owned requirement.
                AssetDatabase.CreateAsset(new Material(shader) { enableInstancing = false }, Route);
            }
        }

        [SetUp]
        public void IdentifyInputs()
        {
            foreach (string path in new[] { Map, Route })
            {
                using var hash = SHA256.Create();
                string digest = BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
                TestContext.WriteLine(path + " " + AssetDatabase.AssetPathToGUID(path) + " " + digest);
            }
            string fixture = typeof(NavigationMaterialChecks).FullName;
            TestContext.WriteLine($"MCP_VERIFICATION_EXECUTION {executionId:N}");
            TestContext.WriteLine($"MCP_VERIFICATION_EXPECTED_CASE {fixture}.{nameof(MapMarkersSupportInstancing)}");
            TestContext.WriteLine($"MCP_VERIFICATION_EXPECTED_CASE {fixture}.{nameof(RouteArrowsSupportInstancing)}");
            TestContext.WriteLine(
                $"MCP_VERIFICATION_CONTEXT {Application.unityVersion}|{EditorUserBuildSettings.activeBuildTarget}|EditMode|{SystemInfo.operatingSystemFamily}"
            );
        }

        [Test]
        public void MapMarkersSupportInstancing() => RequireInstancing(Map);

        [Test]
        public void RouteArrowsSupportInstancing() => RequireInstancing(Route);

        private static void RequireInstancing(string path)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            Assert.IsNotNull(material, path);
            Assert.IsTrue(material.enableInstancing, path + " must enable GPU instancing.");
        }
    }
}
