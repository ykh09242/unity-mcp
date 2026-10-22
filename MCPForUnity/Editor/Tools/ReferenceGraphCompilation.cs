using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Existing Editor compiler metadata; no compiler installation or source execution.</summary>
    internal static class ReferenceGraphCompilation
    {
        private static Dictionary<string, JObject> sources;
        private static string revision;
        private static string target;
        private static bool subscribed;
        private static double lastUsed;
        internal static bool Retained => subscribed;

        internal static void ReleaseExpired(double now)
        {
            if (subscribed && now - lastUsed > 600)
                Dispose();
        }

        internal static void Invalidate(object context = null)
        {
            sources = null;
            revision = null;
        }

        private static void Dispose()
        {
            Invalidate();
            CompilationPipeline.compilationStarted -= Invalidate;
            CompilationPipeline.compilationFinished -= Invalidate;
            AssemblyReloadEvents.beforeAssemblyReload -= Dispose;
            EditorApplication.quitting -= Dispose;
            subscribed = false;
        }

        private static string Normalize(string path) => path.Replace('\\', '/');

        private static void Ensure()
        {
            lastUsed = EditorApplication.timeSinceStartup;
            if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorUtility.scriptCompilationFailed)
                throw new InvalidOperationException("Compilation context is not ready.");
            string currentTarget = EditorUserBuildSettings.activeBuildTarget.ToString();
            if (sources != null && target == currentTarget)
                return;
            if (!subscribed)
            {
                CompilationPipeline.compilationStarted += Invalidate;
                CompilationPipeline.compilationFinished += Invalidate;
                AssemblyReloadEvents.beforeAssemblyReload += Dispose;
                EditorApplication.quitting += Dispose;
                subscribed = true;
            }
            var next = new Dictionary<string, JObject>(Path.DirectorySeparatorChar == '\\' ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);
            var signatures = new JArray();
            foreach (var assembly in CompilationPipeline.GetAssemblies(AssembliesType.Editor).OrderBy(value => value.name, StringComparer.Ordinal))
            {
                string[] files = assembly.sourceFiles.Select(Normalize).OrderBy(value => value, StringComparer.Ordinal).ToArray();
                string[] references = assembly
                    .assemblyReferences.Select(value => value.name)
                    .Concat(assembly.compiledAssemblyReferences.Select(Path.GetFileNameWithoutExtension))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(value => value, StringComparer.Ordinal)
                    .ToArray();
                var metadata = new JObject
                {
                    ["state"] = "available",
                    ["assembly"] = assembly.name,
                    ["references"] = new JArray(references),
                    ["defines"] = new JArray(assembly.defines.OrderBy(value => value, StringComparer.Ordinal)),
                    ["target"] = currentTarget,
                    ["assemblies_type"] = "Editor",
                };
                metadata["binding_revision"] = Hash(metadata);
                signatures.Add(new JObject { ["context"] = metadata.DeepClone(), ["files"] = new JArray(files) });
                foreach (string file in files)
                {
                    if (next.ContainsKey(file))
                        throw new InvalidOperationException("Source belongs to multiple compiler assemblies.");
                    next.Add(file, metadata);
                }
            }
            revision = Hash(signatures);
            foreach (var metadata in next.Values.Distinct())
                metadata["revision"] = revision;
            sources = next;
            target = currentTarget;
        }

        private static string Hash(JToken value)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value.ToString(Formatting.None)))).Replace("-", "").ToLowerInvariant();
        }

        internal static JObject Status()
        {
            try
            {
                Ensure();
                return new JObject
                {
                    ["state"] = "available",
                    ["revision"] = revision,
                    ["target"] = target,
                    ["assemblies_type"] = "Editor",
                };
            }
            catch (Exception error)
            {
                return new JObject
                {
                    ["state"] = "unavailable",
                    ["error"] = error.GetType().Name,
                    ["assemblies_type"] = "Editor",
                };
            }
        }

        internal static JObject ForSource(string path, string resolved)
        {
            JObject status = Status();
            if (status.Value<string>("state") != "available")
                return status;
            if (sources.TryGetValue(Normalize(path), out var metadata) || sources.TryGetValue(Normalize(resolved), out metadata))
                return (JObject)metadata.DeepClone();
            return new JObject
            {
                ["state"] = "not_in_compilation",
                ["revision"] = revision,
                ["target"] = target,
                ["assemblies_type"] = "Editor",
                ["binding_revision"] = "inactive:" + target,
            };
        }

        internal static string Binding(string path, string resolved)
        {
            Ensure();
            return sources.TryGetValue(Normalize(path), out var metadata) || sources.TryGetValue(Normalize(resolved), out metadata)
                ? metadata.Value<string>("binding_revision")
                : "inactive:" + target;
        }
    }
}
