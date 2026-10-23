using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Windows
{
    public partial class MCPForUnityEditorWindow
    {
        private void ShowDiagnostics()
        {
            var owner = new WeakReference<MCPForUnityEditorWindow>(this);
            McpDiagnosticWindow.Open(categories => owner.TryGetTarget(out var window) && window != null ? window.CaptureDiagnosticInput(categories) : null);
        }

        private JObject CaptureDiagnosticInput(int categories)
        {
            if (this == null || !guiCreated)
                return null;
            var input = new JObject();
            if ((categories & McpDiagnosticBundle.Versions) != 0)
                input["versions"] = new JObject { ["unity"] = Application.unityVersion, ["package"] = AssetPathUtility.GetPackageVersion() };
            if ((categories & McpDiagnosticBundle.Connection) != 0)
            {
                var configuration = EditorConfigurationCache.Instance;
                var mode = configuration.UseHttpTransport ? TransportMode.Http : TransportMode.Stdio;
                var client = MCPServiceLocator.ExistingTransportManager?.GetClient(mode);
                string scope = mode == TransportMode.Http ? configuration.HttpTransportScope : "unspecified";
                input["connection"] = new JObject
                {
                    ["selected_transport"] = configuration.UseHttpTransport ? "http" : "stdio",
                    ["selected_scope"] = string.IsNullOrEmpty(scope) ? "unspecified" : scope,
                    ["existing_client_connected"] = client?.State?.IsConnected,
                };
            }
            if ((categories & McpDiagnosticBundle.Capabilities) != 0)
                input["capabilities"] = new JObject
                {
                    ["tools"] = toolsLoaded ? toolsSection?.CaptureDiagnosticState() : null,
                    ["resources"] = resourcesLoaded ? resourcesSection?.CaptureDiagnosticState() : null,
                };
            if ((categories & McpDiagnosticBundle.Graph) != 0)
                input["graph"] = graphSection?.CaptureDiagnostics();
            if ((categories & McpDiagnosticBundle.Operation) != 0)
                input["operation"] = activitySection?.CaptureDiagnosticSelection();
            return input;
        }
    }
}
