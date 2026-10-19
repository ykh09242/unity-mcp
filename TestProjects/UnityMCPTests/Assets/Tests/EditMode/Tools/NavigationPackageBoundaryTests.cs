using System;
using System.Linq;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationPackageBoundaryTests
    {
        private static JValue SceneHandle(Scene selected)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(selected.handle.GetRawData());
#else
            return new JValue((int)selected.handle);
#endif
        }

        [Test]
        public void BasePackageReportsDetectionAndRefusesMissingSurfaceDependency()
        {
            Type handler = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.ManageNavigation");
            Assert.IsNotNull(handler, "Dedicated navigation handler must compile with and without AI Navigation installed.");
            JObject result = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { new JObject { ["action"] = "capabilities" } }));
            Assert.IsTrue((bool)result["success"], result.ToString());
            bool present = AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetType("Unity.AI.Navigation.NavMeshSurface") != null);
            Assert.AreEqual(present, (bool)result["data"]["package_available"]);
            if (present)
                return;
            var fixture = new PrefabTestSceneFixture();
            fixture.PrepareRunnerBootstrap();
            Scene scene = fixture.Create("__McpNavigationAbsence_", Guid.NewGuid().ToString("N"));
            GameObject go = new GameObject("OwnedSurfaceRefusal");
            try
            {
                JObject refusal = JObject.FromObject(
                    handler
                        .GetMethod("HandleCommand")
                        .Invoke(
                            null,
                            new object[]
                            {
                                new JObject
                                {
                                    ["action"] = "add",
                                    ["kind"] = "surface",
                                    ["scene_handle"] = SceneHandle(scene),
                                    ["target"] = go.GetInstanceIDCompat(),
                                },
                            }
                        )
                );
                Assert.IsFalse((bool)refusal["success"]);
                StringAssert.Contains(
                    (bool?)result["data"]["module_available"] == false ? "ai_module_unavailable" : "ai_navigation_package_unavailable",
                    refusal.ToString()
                );
                Assert.AreEqual(1, go.GetComponents<Component>().Length, "No component or package may be installed implicitly.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                fixture.Close();
                fixture.RestoreRunnerBootstrap();
            }
        }
    }
}
