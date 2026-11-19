#if MCP_INPUT_SYSTEM
using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.UnityLinker;

namespace MCPForUnity.Editor.Tools.Input
{
    // Preserve the public getters and setter selected at runtime, including their historical names.
    // State packets and queued generic state types are referenced directly by the Player assembly.
    internal sealed class InputSystemLinker : IUnityLinkerProcessor
    {
        public int callbackOrder => 0;

        public string GenerateAdditionalLinkXmlFile(BuildReport report, UnityLinkerBuildPipelineData data)
        {
            Type device = Type.GetType("UnityEngine.InputSystem.InputDevice, Unity.InputSystem")
                ?? Type.GetType("UnityEngine.Experimental.Input.InputDevice, Unity.InputSystem");
            if (device == null)
                return null;
            var assembly = new XElement("assembly", new XAttribute("fullname", device.Assembly.GetName().Name));
            void Preserve(Type type, params string[] names)
            {
                if (type == null)
                    return;
                var accessors = names.Select(type.GetProperty).Where(property => property != null).SelectMany(property => property.GetAccessors());
                assembly.Add(
                    new XElement(
                        "type",
                        new XAttribute("fullname", type.FullName),
                        accessors.Select(method => new XElement("method", new XAttribute("name", method.Name)))
                    )
                );
            }
            Preserve(device, "deviceId", "id");
            Preserve(device.Assembly.GetType(device.Namespace + ".LowLevel.InputState"), "currentUpdateType");
            Preserve(device.Assembly.GetType(device.Namespace + ".InputSettings"), "backgroundBehavior");
            string[] playerProperties =
            {
                "playerIndex",
                "inputIsActive",
                "active",
                "user",
                "actions",
                "currentControlScheme",
                "controlScheme",
                "currentActionMap",
                "neverAutoSwitchControlSchemes",
            };
            Preserve(InputSystemCompatibility.PlayerInputType, playerProperties);
            var path = Path.GetFullPath(Path.Combine("Temp", "MCPForUnity.InputSystem.link.xml"));
            new XDocument(new XElement("linker", assembly)).Save(path);
            return path;
        }
    }
}
#endif
