using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class OpenShaderGraphInspectionTests
    {
        private const BindingFlags Members = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

        private static JObject Inspect(string path, string action = "inspect_open_graph") =>
            JObject.FromObject(ManageShader.HandleCommand(new JObject { ["action"] = action, ["path"] = path }));

        [TestCase("Packages/example/Graph.shadergraph")]
        [TestCase("Assets/../Graph.shadergraph")]
        [TestCase("Assets/Graph.shader")]
        public void InvalidGraphPathIsRejected(string path) => Assert.IsFalse(Inspect(path).Value<bool>("success"));

        [Test]
        public void ExistingWindowReportsOnlyItsOwnUnsavedModel()
        {
            Type windowType = Type.GetType("UnityEditor.ShaderGraph.Drawing.MaterialGraphEditWindow, Unity.ShaderGraph.Editor");
            if (windowType == null)
                Assert.Ignore("The optional Shader Graph Editor package is not installed.");
            var assembly = windowType.Assembly;
            var graphType = assembly.GetType("UnityEditor.ShaderGraph.GraphData", true);
            var serializer = assembly.GetType("UnityEditor.ShaderGraph.Serialization.MultiJson", true);
            string folder = "Assets/__OpenGraphFixture_" + Guid.NewGuid().ToString("N");
            string path = folder + "/Main.shadergraph";
            string other = folder + "/Other/Main.shadergraph";
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            EditorWindow window = null;
            try
            {
                // This test owns both assets and opens only its own window.
                var authored = Activator.CreateInstance(graphType);
                graphType.GetMethod("AddContexts").Invoke(authored, null);
                string json = (string)serializer.GetMethod("Serialize", BindingFlags.Static | BindingFlags.Public).Invoke(null, new[] { authored });
                AssetDatabase.CreateFolder(folder, "Other");
                foreach (var asset in new[] { path, other })
                {
                    File.WriteAllText(asset, json);
                    AssetDatabase.ImportAsset(asset, ImportAssetOptions.ForceSynchronousImport);
                }
                string guid = AssetDatabase.AssetPathToGUID(path);
                byte[] saved = File.ReadAllBytes(path);
                var timestamp = File.GetLastWriteTimeUtc(path);
                var disk = Inspect(path, "inspect_graph");
                Assert.IsTrue(disk.Value<bool>("success"), disk.ToString());
                Assert.AreEqual("no_matching_window", (string)Inspect(path)["data"]["status"]);

                window = (EditorWindow)ScriptableObject.CreateInstance(windowType);
                window.ShowUtility();
                windowType.GetMethod("Initialize", new[] { typeof(string) }).Invoke(window, new object[] { guid });
                var ownerField = windowType.GetField("m_GraphObject", Members);
                var owner = ownerField.GetValue(window);
                Assert.NotNull(owner, "The real Shader Graph window must load the fixture model.");
                var graph = owner.GetType().GetProperty("graph").GetValue(owner);
                var initial = Inspect(path)["data"];
                Assert.AreEqual("open_model", (string)initial["status"], initial.ToString());
                Assert.AreEqual((int)disk["data"]["nodeCount"], (int)initial["nodeCount"]);
                Assert.AreEqual((int)disk["data"]["edgeCount"], (int)initial["edgeCount"]);
                Assert.IsFalse((bool)initial["hasUnsavedChanges"]);

                var value = Activator.CreateInstance(assembly.GetType("UnityEditor.ShaderGraph.Vector1Node", true));
                var multiply = Activator.CreateInstance(assembly.GetType("UnityEditor.ShaderGraph.MultiplyNode", true));
                foreach (var node in new[] { value, multiply })
                    graphType.GetMethod("AddNode").Invoke(graph, new[] { node });
                var slot = assembly.GetType("UnityEditor.Graphing.SlotReference", true);
                var edge = graphType
                    .GetMethod("Connect")
                    .Invoke(graph, new[] { Activator.CreateInstance(slot, value, 0), Activator.CreateInstance(slot, multiply, 0) });
                Assert.NotNull(edge);
                windowType.GetMethod("UpdateTitle").Invoke(window, new object[] { false });
                Assert.IsTrue(window.hasUnsavedChanges);
                var selection = Selection.objects;
                var focus = EditorWindow.focusedWindow;
                var live = Inspect(path)["data"];
                Assert.AreEqual("open_model", (string)live["status"], live.ToString());
                Assert.AreEqual((int)initial["nodeCount"] + 2, (int)live["nodeCount"]);
                Assert.AreEqual((int)initial["edgeCount"] + 1, (int)live["edgeCount"]);
                Assert.IsTrue((bool)live["hasUnsavedChanges"]);
                Assert.AreEqual("no_matching_window", (string)Inspect(other)["data"]["status"]);

                // During a window transition the selected GUID and the resident model can disagree.
                var modelGuid = graphType.GetProperty("assetGuid");
                modelGuid.SetValue(graph, AssetDatabase.AssetPathToGUID(other));
                try
                {
                    Assert.AreEqual("open_model_unavailable", (string)Inspect(path)["data"]["status"]);
                }
                finally
                {
                    modelGuid.SetValue(graph, guid);
                }
                ownerField.SetValue(window, null);
                try
                {
                    var unavailable = Inspect(path)["data"];
                    Assert.AreEqual("open_model_unavailable", (string)unavailable["status"]);
                    Assert.IsNull(unavailable["nodeCount"]);
                    Assert.IsNull(unavailable["edgeCount"]);
                }
                finally
                {
                    ownerField.SetValue(window, owner);
                }
                Assert.AreSame(focus, EditorWindow.focusedWindow);
                CollectionAssert.AreEqual(selection, Selection.objects);
                Assert.IsTrue(window.hasUnsavedChanges);
                CollectionAssert.AreEqual(saved, File.ReadAllBytes(path));
                Assert.AreEqual(timestamp, File.GetLastWriteTimeUtc(path));
                Assert.IsTrue(JToken.DeepEquals(disk, Inspect(path, "inspect_graph")));

                var secondWindow = (EditorWindow)ScriptableObject.CreateInstance(windowType);
                try
                {
                    windowType.GetField("m_Selected", Members).SetValue(secondWindow, guid);
                    Assert.AreEqual("open_model", (string)Inspect(path)["data"]["status"], "An unattached window is not open.");
                    secondWindow.ShowUtility();
                    var ambiguous = Inspect(path)["data"];
                    Assert.AreEqual("open_model_unavailable", (string)ambiguous["status"]);
                    Assert.IsNull(ambiguous["nodeCount"]);
                }
                finally
                {
                    secondWindow.Close();
                }
                TestContext.WriteLine($"Saved nodes/edges {initial["nodeCount"]}/{initial["edgeCount"]}, unsaved {live["nodeCount"]}/{live["edgeCount"]}.");
            }
            finally
            {
                if (window != null)
                {
                    windowType.GetMethod("UpdateTitle").Invoke(window, new object[] { true });
                    window.Close();
                }
                AssetDatabase.DeleteAsset(folder);
            }
        }
    }
}
