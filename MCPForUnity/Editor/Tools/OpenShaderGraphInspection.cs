using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    internal static class OpenShaderGraphInspection
    {
        private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        private const int MaxWindows = 64;

        internal static object Inspect(JObject parameters)
        {
            if (parameters["path"]?.Type != JTokenType.String)
                return new ErrorResponse("inspect_open_graph requires a project-owned .shadergraph path.");
            string path;
            try
            {
                path = AssetPathUtility.GetContainedAssetPath(parameters.Value<string>("path"));
            }
            catch (Exception e) when (e is ArgumentException || e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException)
            {
                return new ErrorResponse(e.Message);
            }
            if (!string.Equals(Path.GetExtension(path), ".shadergraph", StringComparison.OrdinalIgnoreCase))
                return new ErrorResponse("inspect_open_graph requires a .shadergraph path inside Assets.");

            string guid = AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets);
            object Status(string status, string reason) =>
                new SuccessResponse(
                    reason,
                    new
                    {
                        path,
                        status,
                        reason,
                    }
                );
            if (string.IsNullOrEmpty(guid))
                return Status("no_matching_window", "The path has no existing asset GUID in this Editor.");
            var assembly = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == "Unity.ShaderGraph.Editor");
            var type = assembly?.GetType("UnityEditor.ShaderGraph.Drawing.MaterialGraphEditWindow");
            if (type == null || !typeof(EditorWindow).IsAssignableFrom(type))
                return Status("window_lookup_unavailable", "The Shader Graph Editor window type is unavailable.");

            var windows = UnityEngine.Resources.FindObjectsOfTypeAll(type);
            if (windows.Length > MaxWindows)
                return Status("window_lookup_unavailable", "The Shader Graph window search exceeds its 64-window limit.");
            var selected = type.GetField("m_Selected", Fields);
            var host = typeof(EditorWindow).GetField("m_Parent", Fields);
            EditorWindow match = null;
            foreach (EditorWindow window in windows)
            {
                // A loaded but unattached EditorWindow can also be returned by FindObjectsOfTypeAll.
                if (host == null || !typeof(UnityEngine.Object).IsAssignableFrom(host.FieldType) || selected?.FieldType != typeof(string))
                    return Status("window_lookup_unavailable", "This Editor cannot verify open window identity.");
                if (!(host.GetValue(window) is UnityEngine.Object parent) || !parent)
                    continue;
                if (!string.Equals((string)selected.GetValue(window), guid, StringComparison.OrdinalIgnoreCase))
                    continue;
                if (match != null)
                    return Status("open_model_unavailable", "Multiple open Shader Graph windows match this asset.");
                match = window;
            }
            if (match == null)
                return Status("no_matching_window", "No open Shader Graph window matches this asset.");

            // Only read fields on the package's concrete model. Never serialize the model or call node callbacks.
            var ownerType = assembly.GetType("UnityEditor.Graphing.GraphObject");
            var graphType = assembly.GetType("UnityEditor.ShaderGraph.GraphData");
            var owner = type.GetField("m_GraphObject", Fields)?.GetValue(match) as ScriptableObject;
            var graph = owner != null && owner.GetType() == ownerType ? ownerType.GetField("m_Graph", Fields)?.GetValue(owner) : null;
            if (graph == null || graph.GetType() != graphType)
                return Status("open_model_unavailable", "The matching window has no readable live GraphData.");
            var modelGuid = graphType.GetField("<assetGuid>k__BackingField", Fields)?.GetValue(graph) as string;
            if (!string.Equals(modelGuid, guid, StringComparison.OrdinalIgnoreCase))
                return Status("open_model_unavailable", "The live model's asset identity could not be verified.");
            if (!TryListCount(graph, "m_Nodes", out int nodeCount) || !TryListCount(graph, "m_Edges", out int edgeCount))
                return Status("open_model_unavailable", "The matching model's node or edge collection is unreadable.");
            return new SuccessResponse(
                "Read counts from the existing Shader Graph window.",
                new
                {
                    path,
                    status = "open_model",
                    nodeCount,
                    edgeCount,
                    hasUnsavedChanges = match.hasUnsavedChanges,
                }
            );
        }

        private static bool TryListCount(object graph, string name, out int count)
        {
            var list = graph.GetType().GetField(name, Fields)?.GetValue(graph) as ICollection;
            var type = list?.GetType();
            if (type != null && type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>))
            {
                count = list.Count;
                return true;
            }
            count = 0;
            return false;
        }
    }
}
