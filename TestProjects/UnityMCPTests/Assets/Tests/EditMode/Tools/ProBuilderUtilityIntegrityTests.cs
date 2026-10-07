using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ProBuilderUtilityIntegrityTests
    {
        private readonly List<GameObject> ownedObjects = new List<GameObject>();
        private readonly HashSet<Mesh> ownedMeshes = new HashSet<Mesh>();
        private readonly Dictionary<FieldInfo, object> savedResolutionFields = new Dictionary<FieldInfo, object>();
        private Scene originalScene;
        private Scene ownedScene;
        private Component mesh;
        private Type meshType;
        private bool capturedScene;

        [SetUp]
        public void SetUp()
        {
            capturedScene = false;
            ownedScene = default(Scene);
            ownedObjects.Clear();
            ownedMeshes.Clear();
            savedResolutionFields.Clear();
            mesh = null;
            meshType = Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder");
            if (meshType == null)
                Assert.Ignore("Optional ProBuilder package is not installed.");
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned prefab stage is open.");

            // Use real package types without invoking the unrelated default-material patch.
            foreach (FieldInfo field in typeof(ManageProBuilder).GetFields(BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                if (field.FieldType == typeof(Type) || field.FieldType == typeof(bool))
                    savedResolutionFields.Add(field, field.GetValue(null));
            SetResolutionField("_typesResolved", true);
            SetResolutionField("_proBuilderAvailable", true);
            SetResolutionField("_proBuilderMeshType", meshType);
            SetResolutionField("_faceType", Type.GetType("UnityEngine.ProBuilder.Face, Unity.ProBuilder", true));
            SetResolutionField("_smoothingType", Type.GetType("UnityEngine.ProBuilder.Smoothing, Unity.ProBuilder", true));
            SetResolutionField("_editorMeshUtilityType", Type.GetType("UnityEditor.ProBuilder.EditorMeshUtility, Unity.ProBuilder.Editor"));

            originalScene = SceneManager.GetActiveScene();
            capturedScene = true;
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));

            Type faceType = Type.GetType("UnityEngine.ProBuilder.Face, Unity.ProBuilder", true);
            Array faces = Array.CreateInstance(faceType, 2);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 0, 1, 2 } }), 0);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 3, 4, 5 } }), 1);
            var positions = new[]
            {
                new Vector3(1, 2, 3),
                new Vector3(-2, 1, 0),
                new Vector3(0, -1, 2),
                new Vector3(3, 0, 1),
                new Vector3(0, 4, -1),
                new Vector3(-1, 0, 3),
            };
            MethodInfo create = meshType.GetMethod(
                "Create",
                BindingFlags.Static | BindingFlags.Public,
                null,
                new[] { typeof(IEnumerable<Vector3>), typeof(IEnumerable<>).MakeGenericType(faceType) },
                null
            );
            Assert.IsNotNull(create, "Expected the documented ProBuilder Create overload.");
            mesh = (Component)create.Invoke(null, new object[] { positions, faces });
            Assert.IsNotNull(mesh);
            ownedObjects.Add(mesh.gameObject);
            mesh.gameObject.name = "__McpProBuilderUtility_" + Guid.NewGuid().ToString("N");
            CaptureMesh();
            JObject available = JObject.FromObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = "ping" }));
            Assert.IsTrue(available.Value<bool>("success"), available.ToString());
            Assert.AreEqual(meshType, typeof(ManageProBuilder).GetField("_proBuilderMeshType", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (mesh != null)
                    CaptureMesh();
                foreach (GameObject go in ownedObjects)
                {
                    if (go == null)
                        continue;
                    foreach (Component component in go.GetComponents<Component>())
                        if (component != null)
                            Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                }
                for (int i = ownedObjects.Count - 1; i >= 0; i--)
                    if (ownedObjects[i] != null)
                        UnityEngine.Object.DestroyImmediate(ownedObjects[i]);
                foreach (Mesh value in ownedMeshes)
                    if (value != null && !AssetDatabase.Contains(value))
                        UnityEngine.Object.DestroyImmediate(value);
            }
            finally
            {
                foreach (var saved in savedResolutionFields)
                    saved.Key.SetValue(null, saved.Value);
                savedResolutionFields.Clear();
                if (capturedScene && originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                    EditorSceneManager.CloseScene(ownedScene, true);
                ownedObjects.Clear();
                ownedMeshes.Clear();
                capturedScene = false;
            }
        }

        private static void SetResolutionField(string name, object value)
        {
            typeof(ManageProBuilder).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, value);
        }

        private void CaptureMesh()
        {
            MeshFilter filter = mesh.GetComponent<MeshFilter>();
            if (filter != null && filter.sharedMesh != null && !AssetDatabase.Contains(filter.sharedMesh))
                ownedMeshes.Add(filter.sharedMesh);
        }

        private GameObject Parent(string name)
        {
            var go = new GameObject(name + Guid.NewGuid().ToString("N"));
            ownedObjects.Add(go);
            return go;
        }

        private Vector3[] WorldVertices()
        {
            return ((IEnumerable<Vector3>)meshType.GetProperty("positions").GetValue(mesh)).Select(mesh.transform.TransformPoint).ToArray();
        }

        private JObject Send(string action, JObject properties = null)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["target"] = mesh.gameObject.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
            };
            if (properties != null)
                request["properties"] = properties;
            JObject result = JObject.FromObject(ManageProBuilder.HandleCommand(request));
            CaptureMesh();
            return result;
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        [TestCase(4)]
        public void FreezeKeepsEveryWorldVertexAndExistingResetSemantics(int hierarchy)
        {
            if (hierarchy > 0)
            {
                GameObject parent = Parent("Parent_");
                parent.transform.position = new Vector3(4, -2, 3);
                parent.transform.rotation = Quaternion.Euler(20, 37, -11);
                parent.transform.localScale = hierarchy == 1 ? Vector3.one : new Vector3(2, 3, .5f);
                mesh.transform.SetParent(parent.transform, false);
                if (hierarchy >= 3)
                {
                    GameObject ancestor = Parent("Ancestor_");
                    ancestor.transform.position = new Vector3(-3, 5, 1);
                    ancestor.transform.rotation = Quaternion.Euler(-13, 29, 41);
                    ancestor.transform.localScale = hierarchy == 4 ? new Vector3(-1.5f, 2, .75f) : new Vector3(1.5f, 2, .75f);
                    parent.transform.SetParent(ancestor.transform, false);
                }
            }
            mesh.transform.localPosition = new Vector3(3, 2, -4);
            mesh.transform.localRotation = Quaternion.Euler(31, -17, 23);
            mesh.transform.localScale = new Vector3(1.25f, .7f, 2);
            Transform parentBefore = mesh.transform.parent;
            Vector3[] before = WorldVertices();
            JObject result = Send("freeze_transform");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Vector3[] after = WorldVertices();
            Assert.AreEqual(before.Length, after.Length);
            for (int i = 0; i < before.Length; i++)
                Assert.That(Vector3.Distance(before[i], after[i]), Is.LessThan(.001f), "Vertex " + i);
            Assert.AreSame(parentBefore, mesh.transform.parent);
            Assert.That(mesh.transform.position.sqrMagnitude, Is.LessThan(.000001f));
            Assert.That(Quaternion.Angle(Quaternion.identity, mesh.transform.rotation), Is.LessThan(.001f));
            Assert.AreEqual(Vector3.one, mesh.transform.localScale);
        }

        [TestCase("[1]", 1)]
        [TestCase("[]", 0)]
        [TestCase(null, 2)]
        public void AutoSmoothReportsFacesSuppliedToSmoothing(string indices, int expected)
        {
            var properties = new JObject { ["angleThreshold"] = 0 };
            if (indices != null)
                properties["faceIndices"] = JToken.Parse(indices);
            JObject result = Send("auto_smooth", properties);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(expected, result["data"].Value<int>("faceCount"));
        }
    }
}
