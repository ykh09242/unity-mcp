using System;

namespace UnityEditor
{
    public static class Undo
    {
        public static void RecordObject(UnityEngine.Object value, string label) => throw new NotSupportedException("Editor mutation is excluded.");
    }

    public static class EditorUtility
    {
        public static void SetDirty(UnityEngine.Object value) => throw new NotSupportedException("Editor mutation is excluded.");
    }

    public static class AssetDatabase
    {
        public static UnityEngine.Object LoadAssetAtPath(string path, Type type) =>
            throw new NotSupportedException("Asset API is excluded from this managed fixture.");
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public static class RenderPipelineUtility
    {
        public enum VFXComponentType
        {
            ParticleSystem,
            LineRenderer,
            TrailRenderer,
        }

        public static bool IsMaterialInvalidForActivePipeline(UnityEngine.Material value, out string reason) =>
            throw new NotSupportedException("Material API is excluded.");

        public static UnityEngine.Material GetOrCreateDefaultVFXMaterial(VFXComponentType type) => throw new NotSupportedException("Material API is excluded.");

        public static UnityEngine.Material GetOrCreateDefaultSceneMaterial() => throw new NotSupportedException("Material API is excluded.");
    }

    public static class McpLog
    {
        public static void Warn(string message) { }

        public static void Error(string message) { }
    }

    public static class AssetPathUtility
    {
        public static string GetAssetReferencePath(string path, bool allowPackages, bool allowBuiltIn) =>
            throw new NotSupportedException("Asset API is excluded from this managed fixture.");
    }
}
