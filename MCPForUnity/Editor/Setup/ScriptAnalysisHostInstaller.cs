using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;
using UnityEditor;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Setup
{
    internal static class ScriptAnalysisHostInstaller
    {
        private static bool _installing;
        internal static bool Installing => _installing;

        internal static JObject Inspect(string root, out string reason)
        {
            try
            {
                return AnalysisHostInstallation.Inspect(root, out reason, ExpectedSourceSignature());
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                reason = error.Message;
                return null;
            }
        }

        internal static string ExpectedSourceSignature() => AnalysisHostInstallation.SourceSignature(FindSource());

        internal static string ResolveVerifiedHost(string root, string sourceSignature) => AnalysisHostInstallation.ResolveVerifiedHost(root, sourceSignature);

        private static string FindSource()
        {
            string package = PackageInfo.FindForAssembly(typeof(ScriptAnalysisHostInstaller).Assembly)?.resolvedPath;
            string source = package != null ? Path.Combine(package, "Editor", "ScriptDiagnosticsHost~") : null;
            if (source == null)
            {
                string script = AssetDatabase
                    .FindAssets("ScriptAnalysisHostInstaller t:MonoScript", new[] { "Assets" })
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .SingleOrDefault(path => Path.GetFileName(path) == "ScriptAnalysisHostInstaller.cs");
                if (script != null)
                    source = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(script), "..", "ScriptDiagnosticsHost~"));
            }
            if (source == null || !Directory.Exists(source))
                throw new IOException("shipped_host_source_unavailable");
            return source;
        }

        internal static async Task<JObject> InstallAsync(CancellationToken cancellation)
        {
            if (_installing)
                throw new InvalidOperationException("host_setup_already_running");
            _installing = true;
            try
            {
                string source = FindSource();
                string root = ScriptAnalysisHost.CacheRoot;
                return await AnalysisHostInstallation.InstallAsync(source, root, ScriptAnalysisHost.Owner, cancellation);
            }
            finally
            {
                _installing = false;
            }
        }
    }
}
