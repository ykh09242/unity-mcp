using System.Reflection;
using System.Xml;
using MCPForUnity.Editor.Services.PlayScenarios;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public sealed class PlayScenarioPlayerLinkerTests
    {
        [Test]
        public void ScenarioDescriptor_RootsOptionalRegistrationTypesWithoutPreservingWholeAssemblies()
        {
            MethodInfo method = typeof(PlayScenarioPlayerBuild).GetMethod("CreateLinkerDescriptor", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method, "Scenario builds must explicitly retain optional runtime backend registration types for IL2CPP.");
            var document = new XmlDocument();
            document.LoadXml((string)method.Invoke(null, null));
            XmlElement root = document.DocumentElement;
            Assert.AreEqual("linker", root.Name);
            Assert.AreEqual(3, root.ChildNodes.Count);
            var core = (XmlElement)root.SelectSingleNode("assembly[@fullname='MCPForUnity.Runtime']");
            Assert.IsNotNull(core);
            Assert.AreEqual("all", core.GetAttribute("preserve"));
            AssertOptionalType(root, "MCPForUnity.Input.UGUI.Runtime", "MCPForUnity.Editor.Tools.Input.UguiScenarioDispatch");
            AssertOptionalType(root, "MCPForUnity.Input.InputSystem.Runtime", "MCPForUnity.Runtime.PlayScenarios.PlayScenarioInputSystemRegistration");
        }

        private static void AssertOptionalType(XmlElement root, string assemblyName, string typeName)
        {
            var assembly = (XmlElement)root.SelectSingleNode("assembly[@fullname='" + assemblyName + "']");
            Assert.IsNotNull(assembly, "Missing optional backend assembly: " + assemblyName);
            Assert.AreEqual("1", assembly.GetAttribute("ignoreIfMissing"), "Package absence must remain supported.");
            Assert.AreEqual("", assembly.GetAttribute("preserve"), "Do not preserve the entire optional assembly.");
            Assert.AreEqual(1, assembly.ChildNodes.Count, "Only the required registration/dispatch type should be rooted.");
            var type = assembly.FirstChild as XmlElement;
            Assert.IsNotNull(type);
            Assert.AreEqual("type", type.Name);
            Assert.AreEqual(typeName, type.GetAttribute("fullname"));
            Assert.AreEqual("all", type.GetAttribute("preserve"), "Keep registration, constructor and backend dispatch reachable.");
        }
    }
}
