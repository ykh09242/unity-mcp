using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManagedReferenceMigrationTests
    {
        private SerializedInspectionAsset _asset;
        private string folder;
        private string folderGuid;

        [SetUp]
        public void SetUp()
        {
            string name = "__McpManagedMigration_" + Guid.NewGuid().ToString("N");
            folder = "Assets/" + name;
            Assert.IsFalse(AssetDatabase.IsValidFolder(folder));
            folderGuid = AssetDatabase.CreateFolder("Assets", name);
            Assert.IsNotEmpty(folderGuid);
            _asset = ScriptableObject.CreateInstance<SerializedInspectionAsset>();
            AssetDatabase.CreateAsset(_asset, folder + "/Record.asset");
            AssetDatabase.SaveAssetIfDirty(_asset);
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(folderGuid) && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
                Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
        }

        [Test]
        public void ManagedTypeMigration_DistinguishesNullAndMissingAndRecoversOwnedPayloadAfterReopen()
        {
            const string missingType = "class: UnavailableNode, ns: Removed.Fixture, asm: Removed.Fixture.Assembly";
            const string legacyType = "class: PriorNode, ns: Prior.Fixture, asm: Prior.Fixture.Assembly";
            string path = AssetDatabase.GetAssetPath(_asset);
            string guid = AssetDatabase.AssetPathToGUID(path);
            JToken Graph()
            {
                var response = JObject.FromObject(
                    InspectSerializedObject.HandleCommand(
                        new JObject
                        {
                            ["instance_id"] = _asset.GetInstanceIDCompat(),
                            ["mode"] = "value",
                            ["property_path"] = "graph",
                        }
                    )
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                return response["data"]["records"][0];
            }
            void Reopen(string previous, string replacement)
            {
                Resources.UnloadAsset(_asset);
                // Unity may wrap a long type tuple across YAML lines. Only rewrite this owned type identity.
                string yaml = File.ReadAllText(path);
                var identity = new Regex(Regex.Escape(previous).Replace(@"\ ", @"\s+"));
                Assert.AreEqual(1, identity.Matches(yaml).Count);
                File.WriteAllText(path, identity.Replace(yaml, replacement, 1));
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                _asset = AssetDatabase.LoadAssetAtPath<SerializedInspectionAsset>(path);
                Assert.IsNotNull(_asset);
                Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            }

            Assert.AreEqual("null", (string)Graph()["managed_reference_status"]);
            _asset.graph = new SerializedInspectionNode { value = 98231 };
            EditorUtility.SetDirty(_asset);
            AssetDatabase.SaveAssetIfDirty(_asset);
            string id = (string)Graph()["managed_reference_id"];
            Type node = typeof(SerializedInspectionNode);
            Reopen($"class: {node.Name}, ns: {node.Namespace}, asm: {node.Assembly.GetName().Name}", missingType);
            byte[] before = File.ReadAllBytes(path);
            JToken report = Graph();
            JToken missing;
            if ((string)report["managed_reference_id"] == id)
            {
                Assert.AreEqual("missing_type", (string)report["managed_reference_status"]);
                missing = report["missing_type"];
            }
            else
            {
                Assert.AreEqual("unknown", (string)report["managed_reference_status"]);
                Assert.AreEqual(1, (int)report["host_missing_type_count"]);
                missing = report["host_missing_types_sample"].Single();
            }
            Assert.AreEqual("UnavailableNode", (string)missing["class"]);
            Assert.AreEqual("Removed.Fixture", (string)missing["namespace"]);
            Assert.AreEqual("Removed.Fixture.Assembly", (string)missing["assembly"]);
            Assert.AreEqual(id, (string)missing["reference_id"]);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.IsNull(_asset.graph);
            // Saving an unrelated owned field must retain Unity's recoverable payload.
            _asset.after = 923;
            EditorUtility.SetDirty(_asset);
            AssetDatabase.SaveAssetIfDirty(_asset);
            Reopen(missingType, legacyType);
            Assert.AreEqual("resolved", (string)Graph()["managed_reference_status"]);
            Assert.AreEqual(id, (string)Graph()["managed_reference_id"]);
            Assert.AreEqual(98231, _asset.graph.value);
            Assert.AreEqual(923, _asset.after);
            TestContext.Out.WriteLine(
                new JObject
                {
                    ["asset_guid"] = guid,
                    ["missing_observation"] = report,
                    ["restored_observation"] = Graph(),
                    ["restored_payload"] = _asset.graph.value,
                }
            );
        }
    }
}
