using System.IO;
using UnityEditor.AssetImporters;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    // A genuine owned ScriptedImporter. This is not a glTF importer or an optional package.
    [ScriptedImporter(1, "mcpnormalizationfixture")]
    public sealed class NormalizationFixtureImporter : ScriptedImporter
    {
        public override void OnImportAsset(AssetImportContext context)
        {
            bool degenerate = File.ReadAllText(context.assetPath).Trim() == "degenerate";
            var root = new GameObject("FixtureModel");
            Mesh mesh = CreateCube(degenerate);
            mesh.name = "OwnedCube";
            var material = new Material(Shader.Find("Hidden/InternalErrorShader")) { name = "OwnedMaterial" };
            for (int i = 0; i < 2; i++)
            {
                var child = new GameObject(i == 0 ? "ChildA" : "ChildB");
                child.transform.SetParent(root.transform, false);
                child.transform.localPosition = degenerate ? Vector3.zero : new Vector3(i == 0 ? -3f : 3f, 0f, 0f);
                child.transform.localScale = i == 0 ? new Vector3(2f, 1f, 1f) : new Vector3(1f, 2f, 1f);
                child.transform.localRotation = i == 0 ? Quaternion.identity : Quaternion.Euler(0f, 0f, 90f);
                child.AddComponent<MeshFilter>().sharedMesh = mesh;
                child.AddComponent<MeshRenderer>().sharedMaterial = material;
            }
            var clip = new AnimationClip { name = "OwnedEmbeddedClip" };
            clip.SetCurve("ChildA", typeof(Transform), "m_LocalPosition.y", AnimationCurve.Linear(0f, 0f, 1f, 1f));
            context.AddObjectToAsset("mesh", mesh);
            context.AddObjectToAsset("material", material);
            context.AddObjectToAsset("clip", clip);
            context.AddObjectToAsset("root", root);
            context.SetMainObject(root);
        }

        internal static Mesh CreateCube(bool degenerate = false)
        {
            var mesh = new Mesh();
            mesh.vertices = new[]
            {
                new Vector3(-.5f, -.5f, -.5f),
                new Vector3(.5f, -.5f, -.5f),
                new Vector3(.5f, .5f, -.5f),
                new Vector3(-.5f, .5f, -.5f),
                new Vector3(-.5f, -.5f, .5f),
                new Vector3(.5f, -.5f, .5f),
                new Vector3(.5f, .5f, .5f),
                new Vector3(-.5f, .5f, .5f),
            };
            if (degenerate)
                mesh.vertices = new Vector3[8];
            mesh.triangles = new[] { 0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5 };
            mesh.RecalculateBounds();
            return mesh;
        }
    }
}
