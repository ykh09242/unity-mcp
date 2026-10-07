using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ShaderGraphInspectionTests
    {
        private string folder;
        private string path;
        private string fullPath;

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/__McpGraphInspection_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            path = folder + "/Graph with spaces.shadergraph";
            fullPath = Path.Combine(Application.dataPath, path.Substring(7));
        }

        [TearDown]
        public void TearDown() => AssetDatabase.DeleteAsset(folder);

        [Test]
        public void InspectsMultiJsonNodesEdgesPropertiesKeywordsAndPreservesSource()
        {
            List<JObject> fixture = Fixture();
            fixture[1]["m_Type"] = "Untrusted.Custom.NodeType";
            fixture[1]["m_Name"] = "Text { with } quotes \" λ";
            Seed(fixture);
            byte[] original = File.ReadAllBytes(fullPath);
            DateTime modified = File.GetLastWriteTimeUtc(fullPath);
            JObject response = Inspect(1, 1);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JToken data = response["data"];
            Assert.AreEqual("multi_json", data.Value<string>("format"));
            Assert.AreEqual(2, data.Value<int>("nodeCount"));
            Assert.AreEqual(1, data.Value<int>("edgeCount"));
            Assert.AreEqual(1, data.Value<int>("propertyCount"));
            Assert.AreEqual(1, data.Value<int>("keywordCount"));
            Assert.AreEqual("Untrusted.Custom.NodeType", data["nodes"][0].Value<string>("type"));
            Assert.AreEqual("Text { with } quotes \" λ", data["nodes"][0].Value<string>("name"));
            Assert.AreEqual("_Strength", data["properties"][0].Value<string>("referenceName"));
            Assert.AreEqual(Id(1), data["edges"][0]["output"].Value<string>("nodeId"));
            Assert.IsTrue(data.Value<bool>("hasMore"));
            Assert.AreEqual(Id(2), Inspect(1, 2)["data"]["nodes"][0].Value<string>("id"));
            Assert.IsEmpty(Inspect(100, int.MaxValue)["data"]["nodes"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(fullPath));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(fullPath));
            Assert.IsFalse(File.Exists(fullPath + ".meta"), "Inspection must not import the graph.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InspectsEdgeBudgetWithoutSkippingValidationOutsideRequestedPage(bool malformedLastEdge)
        {
            List<JObject> fixture = Fixture();
            JToken edge = fixture[0]["m_Edges"][0];
            var edges = new JArray(Enumerable.Range(0, 8192).Select(_ => edge.DeepClone()));
            if (malformedLastEdge)
                edges[edges.Count - 1]["m_InputSlot"]["m_SlotId"] = "invalid";
            fixture[0]["m_Edges"] = edges;
            Seed(fixture);

            JObject response = Inspect(1, 1);

            Assert.AreEqual(!malformedLastEdge, response.Value<bool>("success"), response.ToString());
            if (!malformedLastEdge)
            {
                Assert.AreEqual(8192, response["data"].Value<int>("edgeCount"));
                Assert.AreEqual(1, ((JArray)response["data"]["edges"]).Count);
                Assert.AreEqual(Id(1), response["data"]["edges"][0]["output"].Value<string>("nodeId"));
                Assert.AreEqual(1, response["data"]["edges"][0]["input"].Value<int>("slotId"));
                Assert.IsTrue(response["data"].Value<bool>("hasMore"));
            }
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void SupportsOnlyEvidencedGraphDataVersions(int version)
        {
            List<JObject> fixture = Fixture();
            fixture[0]["m_SGVersion"] = version;
            Seed(fixture);
            Assert.IsTrue(Inspect().Value<bool>("success"));
        }

        [TestCase("legacy")]
        [TestCase("object_type")]
        [TestCase("future")]
        [TestCase("missing_version")]
        [TestCase("duplicate_id")]
        [TestCase("missing_node")]
        [TestCase("missing_edge_node")]
        [TestCase("invalid_slot")]
        [TestCase("scalar_edge")]
        [TestCase("null_edge")]
        [TestCase("null_endpoint")]
        [TestCase("newline_id")]
        [TestCase("oversized_label")]
        public void RejectsUnsupportedOrMalformedStructures(string scenario)
        {
            List<JObject> fixture = Fixture();
            switch (scenario)
            {
                case "legacy":
                    fixture[0].Remove("m_Type");
                    break;
                case "object_type":
                    fixture[0]["m_Type"] = new JObject();
                    break;
                case "future":
                    fixture[0]["m_SGVersion"] = 999;
                    break;
                case "missing_version":
                    fixture[0].Remove("m_SGVersion");
                    break;
                case "duplicate_id":
                    fixture[2]["m_ObjectId"] = Id(1);
                    break;
                case "missing_node":
                    fixture.RemoveAt(1);
                    break;
                case "missing_edge_node":
                    fixture[0]["m_Edges"][0]["m_InputSlot"]["m_Node"]["m_Id"] = Id(999);
                    break;
                case "invalid_slot":
                    fixture[0]["m_Edges"][0]["m_InputSlot"]["m_SlotId"] = "1";
                    break;
                case "scalar_edge":
                    fixture[0]["m_Edges"][0] = 1;
                    break;
                case "null_edge":
                    fixture[0]["m_Edges"][0] = null;
                    break;
                case "null_endpoint":
                    fixture[0]["m_Edges"][0]["m_InputSlot"] = null;
                    break;
                case "newline_id":
                    fixture[0]["m_ObjectId"] = Id(0) + "\n";
                    break;
                case "oversized_label":
                    fixture[1]["m_Name"] = new string('x', 513);
                    break;
            }
            Seed(fixture);
            JObject response = Inspect();
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            if (scenario == "legacy" || scenario == "future" || scenario == "missing_version")
                StringAssert.Contains("Unsupported Shader Graph format", response.Value<string>("error"));
        }

        [TestCase("../outside.shadergraph")]
        [TestCase("Packages/Example/Graph.shadergraph")]
        [TestCase("Assets/Graph.shader")]
        [TestCase("Assets/../Graph.shadergraph")]
        public void RejectsUnsafeOrWrongExtensionPaths(string input)
        {
            JObject response = JObject.FromObject(ManageShader.HandleCommand(new JObject { ["action"] = "inspect_graph", ["path"] = input }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
        }

        [TestCase("file")]
        [TestCase("objects")]
        [TestCase("nodes")]
        [TestCase("edges")]
        [TestCase("tokens")]
        [TestCase("depth")]
        [TestCase("duplicate_field")]
        [TestCase("invalid_utf8")]
        public void EnforcesReadAndParsingBudgets(string budget)
        {
            List<JObject> fixture = Fixture();
            switch (budget)
            {
                case "file":
                    File.WriteAllBytes(fullPath, new byte[4 * 1024 * 1024 + 1]);
                    break;
                case "objects":
                    for (int index = 5; index <= 16384; index++)
                        fixture.Add(new JObject { ["m_ObjectId"] = Id(index), ["m_Type"] = "Example" });
                    Seed(fixture);
                    break;
                case "nodes":
                    fixture[0]["m_Nodes"] = new JArray(Enumerable.Range(1, 4097).Select(index => new JObject { ["m_Id"] = Id(index) }));
                    Seed(fixture);
                    break;
                case "edges":
                    fixture[0]["m_Edges"] = new JArray(Enumerable.Range(0, 8193).Select(_ => fixture[0]["m_Edges"][0].DeepClone()));
                    Seed(fixture);
                    break;
                case "tokens":
                    fixture[0]["unknown"] = new JArray(Enumerable.Repeat(0, 262144));
                    Seed(fixture);
                    break;
                case "depth":
                    File.WriteAllText(fullPath, "{\"nested\":" + new string('[', 65) + "0" + new string(']', 65) + "}");
                    break;
                case "duplicate_field":
                    File.WriteAllText(fullPath, "{\"m_Type\":\"UnityEditor.ShaderGraph.GraphData\",\"m_Type\":\"Other\"}");
                    break;
                case "invalid_utf8":
                    File.WriteAllBytes(fullPath, new byte[] { 0xff });
                    break;
            }
            JObject response = Inspect();
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
        }

        [TestCase(0, 1)]
        [TestCase(101, 1)]
        [TestCase(1, 0)]
        public void RejectsInvalidResultPage(int size, int page)
        {
            Seed(Fixture());
            Assert.IsFalse(Inspect(size, page).Value<bool>("success"));
        }

        private JObject Inspect(int size = 50, int page = 1) =>
            JObject.FromObject(
                ManageShader.HandleCommand(
                    new JObject
                    {
                        ["action"] = "inspect_graph",
                        ["path"] = path,
                        ["pageSize"] = size,
                        ["pageNumber"] = page,
                    }
                )
            );

        private void Seed(IEnumerable<JObject> fixture) =>
            File.WriteAllText(fullPath, string.Join("\n\n", fixture.Select(value => value.ToString(Formatting.None))), new UTF8Encoding(false));

        private static string Id(int value) => value.ToString("x32");

        // Minimal authored structural fixture, based on Unity Graphics GraphData/MultiJson serialization.
        private static List<JObject> Fixture() =>
            new List<JObject>
            {
                new JObject
                {
                    ["m_SGVersion"] = 3,
                    ["m_Type"] = "UnityEditor.ShaderGraph.GraphData",
                    ["m_ObjectId"] = Id(0),
                    ["m_Nodes"] = new JArray(new JObject { ["m_Id"] = Id(1) }, new JObject { ["m_Id"] = Id(2) }),
                    ["m_Properties"] = new JArray(new JObject { ["m_Id"] = Id(3) }),
                    ["m_Keywords"] = new JArray(new JObject { ["m_Id"] = Id(4) }),
                    ["m_Edges"] = new JArray(
                        new JObject
                        {
                            ["m_OutputSlot"] = new JObject
                            {
                                ["m_Node"] = new JObject { ["m_Id"] = Id(1) },
                                ["m_SlotId"] = 0,
                            },
                            ["m_InputSlot"] = new JObject
                            {
                                ["m_Node"] = new JObject { ["m_Id"] = Id(2) },
                                ["m_SlotId"] = 1,
                            },
                        }
                    ),
                },
                new JObject
                {
                    ["m_ObjectId"] = Id(1),
                    ["m_Type"] = "UnityEditor.ShaderGraph.Vector1Node",
                    ["m_Name"] = "Float",
                },
                new JObject
                {
                    ["m_ObjectId"] = Id(2),
                    ["m_Type"] = "UnityEditor.ShaderGraph.MultiplyNode",
                    ["m_Name"] = "Multiply",
                },
                new JObject
                {
                    ["m_ObjectId"] = Id(3),
                    ["m_Type"] = "UnityEditor.ShaderGraph.Internal.Vector1ShaderProperty",
                    ["m_Name"] = "Strength",
                    ["m_DefaultReferenceName"] = "_Strength",
                },
                new JObject
                {
                    ["m_ObjectId"] = Id(4),
                    ["m_Type"] = "UnityEditor.ShaderGraph.ShaderKeyword",
                    ["m_Name"] = "Enabled",
                    ["m_DefaultReferenceName"] = "_ENABLED",
                },
            };
    }
}
