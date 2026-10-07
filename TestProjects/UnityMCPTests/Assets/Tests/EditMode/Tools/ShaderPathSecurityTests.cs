using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ShaderPathSecurityTests
    {
        [TestCase("../Outside")]
        [TestCase("Assets/../../Outside")]
        [TestCase("..\\Outside")]
        [TestCase("C:/Outside")]
        [TestCase("/tmp/outside")]
        [TestCase("//host/share")]
        public void AllShaderActionsRejectEscapingPaths(string path)
        {
            foreach (string action in new[] { "create", "read", "update", "delete" })
            {
                var response = JObject.FromObject(
                    ManageShader.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["name"] = "SecurityProbe",
                            ["path"] = path,
                            ["contents"] = "sentinel",
                        }
                    )
                );
                Assert.IsFalse(response.Value<bool>("success"), action);
                StringAssert.Contains("path", response.ToString().ToLowerInvariant());
            }
        }

        [Test]
        public void CanonicalBoundaryDoesNotAcceptSiblingPrefix()
        {
            Assert.Throws<InvalidOperationException>(() => SafePathUtility.ResolveWithinRoot(Application.dataPath, "../AssetsOutside/probe.shader"));
            Assert.AreEqual(
                Path.Combine(Application.dataPath, "Shaders", "Valid.shader").Replace('/', Path.DirectorySeparatorChar),
                SafePathUtility.ResolveWithinRoot(Application.dataPath, "Shaders/Valid.shader").Replace('/', Path.DirectorySeparatorChar)
            );
        }
    }
}
