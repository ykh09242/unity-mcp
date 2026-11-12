using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class PostEditVerificationTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();

        [SetUp]
        public void SetUp()
        {
            sceneFixture.PrepareRunnerBootstrap();
            sceneFixture.Create("Verification_", Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            sceneFixture.Close();
            sceneFixture.RestoreRunnerBootstrap();
        }

        [Test]
        public void Readback_ReportsRetainedNativeValue()
        {
            var target = new GameObject("Verification");
            try
            {
                target.transform.localPosition = new Vector3(2, 3, 4);

                JObject report = PostEditVerification.Readback(target.transform, new[] { "localPosition" });

                Assert.AreEqual("passed", (string)report["status"]);
                Assert.AreEqual(2, (float)report["observed_values"][0]["value"][0]);
                Assert.IsFalse((bool)report["further_fixes_required"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void Readback_UnsupportedPropertyCannotPass()
        {
            var target = new GameObject("Verification");
            try
            {
                JObject report = PostEditVerification.Readback(target.transform, new[] { "position" });

                Assert.AreEqual("incomplete", (string)report["status"]);
                Assert.IsFalse((bool)report["complete"]);
                Assert.AreEqual(JTokenType.Null, report["further_fixes_required"].Type);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void Readback_DestroyedOriginalTargetIsUnavailable()
        {
            var target = new GameObject("Verification");
            Transform original = target.transform;
            UnityEngine.Object.DestroyImmediate(target);

            JObject report = PostEditVerification.Readback(original, new[] { "localPosition" });

            Assert.AreEqual("unavailable", (string)report["status"]);
            Assert.AreEqual(JTokenType.Null, report["further_fixes_required"].Type);
        }

        [Test]
        public void Readback_BoundsRequestedFields()
        {
            var target = new GameObject("Verification");
            try
            {
                var paths = new string[100];
                for (int i = 0; i < paths.Length; i++)
                    paths[i] = "m_LocalPosition";

                JObject report = PostEditVerification.Readback(target.transform, paths);

                Assert.IsFalse((bool)report["complete"]);
                Assert.LessOrEqual(((JArray)report["observed_values"]).Count, 64);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void Summary_FindingsSurviveIncompleteObservation()
        {
            var findings = new JArray(new JObject { ["code"] = "missing_reference", ["severity"] = "warning" });

            JObject report = PostEditVerification.Summary("selected_properties", new JArray(), findings, false);

            Assert.AreEqual("issues_found", (string)report["status"]);
            Assert.IsTrue((bool)report["further_fixes_required"]);
            Assert.IsFalse((bool)report["complete"]);
        }

        [Test]
        public void Properties_DetectsCallbackChangingPreviouslySetValue()
        {
            var target = new GameObject("Verification");
            try
            {
                var probe = target.AddComponent<VerificationProbe>();
                probe.number = 8;
                JObject report = PostEditVerification.Properties(probe, new JObject { ["number"] = 7 });
                Assert.AreEqual("issues_found", (string)report["status"]);
                Assert.AreEqual("retained_value_differs", (string)report["findings"][0]["code"]);
                Assert.AreEqual(8, (int)report["observed_values"][0]["value"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void ComponentPartialSetterFailureRetainsAppliedReceiptAndReadback()
        {
            var target = new GameObject("Verification");
            try
            {
                var probe = target.AddComponent<VerificationProbe>();
                JObject response = JObject.FromObject(
                    ManageComponents.HandleCommand(
                        new JObject
                        {
                            ["action"] = "set_property",
                            ["target"] = target.GetInstanceIDCompat(),
                            ["searchMethod"] = "by_id",
                            ["componentType"] = typeof(VerificationProbe).FullName,
                            ["properties"] = new JObject { ["number"] = 7, ["Throwing"] = 1 },
                        }
                    )
                );
                Assert.IsFalse((bool)response["success"], response.ToString());
                Assert.AreEqual(7, probe.number);
                Assert.AreEqual("applied", (string)response["data"]["results"][0]["status"]);
                Assert.AreEqual("failed", (string)response["data"]["results"][1]["status"]);
                Assert.IsFalse((bool)response["data"]["verification"]["complete"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void GameObjectComponentReadbackKeepsOriginalWhenCallbackAddsSameType()
        {
            var target = new GameObject("Verification");
            try
            {
                var probe = target.AddComponent<VerificationProbe>();
                int originalId = probe.GetInstanceIDCompat();
                JObject response = JObject.FromObject(
                    ManageGameObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = target.GetInstanceIDCompat(),
                            ["searchMethod"] = "by_id",
                            ["componentProperties"] = new JObject
                            {
                                [typeof(VerificationProbe).FullName] = new JObject { ["number"] = 7, ["AddSibling"] = true },
                            },
                        }
                    )
                );
                Assert.IsTrue((bool)response["success"], response.ToString());
                Assert.AreEqual(2, target.GetComponents<VerificationProbe>().Length);
                JObject receipt = (JObject)response["data"]["verification"]["observed_values"][0];
                Assert.AreEqual(originalId, (int)receipt["original_instance_id"]);
                Assert.AreEqual(7, (int)receipt["observed_values"][0]["value"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void ComponentAddAndRemoveReportExactCreatedIdentity()
        {
            var target = new GameObject("Verification");
            try
            {
                JObject add = JObject.FromObject(
                    ManageComponents.HandleCommand(
                        new JObject
                        {
                            ["action"] = "add",
                            ["target"] = target.GetInstanceIDCompat(),
                            ["searchMethod"] = "by_id",
                            ["componentType"] = typeof(VerificationProbe).FullName,
                            ["properties"] = new JObject { ["number"] = 9 },
                        }
                    )
                );
                Assert.IsTrue((bool)add["success"], add.ToString());
                Assert.AreEqual("passed", (string)add["data"]["verification"]["status"]);
                int id = target.GetComponent<VerificationProbe>().GetInstanceIDCompat();
                JObject remove = JObject.FromObject(
                    ManageComponents.HandleCommand(
                        new JObject
                        {
                            ["action"] = "remove",
                            ["target"] = target.GetInstanceIDCompat(),
                            ["searchMethod"] = "by_id",
                            ["componentType"] = typeof(VerificationProbe).FullName,
                        }
                    )
                );
                Assert.IsTrue((bool)remove["success"], remove.ToString());
                Assert.AreEqual(id, (int)remove["data"]["verification"]["observed_values"]["component_instance_id"]);
                Assert.IsTrue((bool)remove["data"]["verification"]["observed_values"]["removed"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void ReferenceScanBoundsLargeArraysAndFindsMissingReference()
        {
            string folder = "VerificationMissing_" + Guid.NewGuid().ToString("N");
            string path = "Assets/" + folder;
            AssetDatabase.CreateFolder("Assets", folder);
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            var target = ScriptableObject.CreateInstance<VerificationAsset>();
            try
            {
                Assert.IsNotEmpty(
                    AssetDatabase.GetAssetPath(MonoScript.FromScriptableObject(target)),
                    "The persisted fixture requires its own MonoScript asset."
                );
                AssetDatabase.CreateAsset(material, path + "/Reference.mat");
                target.reference = material;
                string assetPath = path + "/Definition.asset";
                AssetDatabase.CreateAsset(target, assetPath);
                AssetDatabase.SaveAssetIfDirty(target);
                string originalGuid = AssetDatabase.AssetPathToGUID(path + "/Reference.mat");
                string yaml = File.ReadAllText(assetPath);
                StringAssert.Contains(originalGuid, yaml);
                File.WriteAllText(assetPath, yaml.Replace(originalGuid, Guid.NewGuid().ToString("N")));
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                target = AssetDatabase.LoadAssetAtPath<VerificationAsset>(assetPath);
                Assert.IsNotNull(target);
                JObject missing = PostEditVerification.Readback(target, new[] { "reference" });
                Assert.IsTrue(((JArray)missing["findings"]).OfType<JObject>().Any(item => (string)item["code"] == "missing_reference"), missing.ToString());
                target.values = new int[500];
                JObject bounded = PostEditVerification.Readback(target, Array.Empty<string>());
                Assert.IsFalse((bool)bounded["complete"]);
                Assert.LessOrEqual((int)bounded["work"]["used"], 2048);
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        [Test]
        public void TruncatedStringAndOmittedEnumNameStayIncompleteWithoutFalseMismatch()
        {
            var target = ScriptableObject.CreateInstance<VerificationAsset>();
            try
            {
                target.text = new string('x', 1024);
                target.choice = VerificationChoice.Value16;
                JObject report = PostEditVerification.Properties(target, new JObject { ["text"] = target.text, ["choice"] = "Value16" });
                Assert.AreEqual("incomplete", (string)report["status"], report.ToString());
                Assert.IsEmpty((JArray)report["findings"]);
                Assert.IsFalse((bool)report["complete"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void ReferencePatchPreservesReferenceSelectorWithoutInventingNullExpectation()
        {
            string folder = "Verification_" + Guid.NewGuid().ToString("N");
            string path = "Assets/" + folder;
            AssetDatabase.CreateFolder("Assets", folder);
            var asset = ScriptableObject.CreateInstance<Fixtures.ManageScriptableObjectTestDefinition>();
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            try
            {
                AssetDatabase.CreateAsset(asset, path + "/Definition.asset");
                AssetDatabase.CreateAsset(material, path + "/Material.mat");
                JObject response = JObject.FromObject(
                    ManageScriptableObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(path + "/Definition.asset") },
                            ["patches"] = new JArray(
                                new JObject
                                {
                                    ["propertyPath"] = "materials.Array.data[0]",
                                    ["ref"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(path + "/Material.mat") },
                                }
                            ),
                        }
                    )
                );
                Assert.IsTrue((bool)response["success"], response.ToString());
                Assert.AreEqual(
                    AssetDatabase.AssetPathToGUID(path + "/Material.mat"),
                    AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(asset.Materials[0]))
                );
                Assert.IsFalse(
                    ((JArray)response["data"]["verification"]["findings"]).OfType<JObject>().Any(row => (string)row["code"] == "retained_value_differs"),
                    response.ToString()
                );
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        [Test]
        public void ScriptableObjectEnumIndexAndComponentRawValueUseTheirWriterSemantics()
        {
            string path = "Assets/VerificationEnum_" + Guid.NewGuid().ToString("N") + ".asset";
            var asset = ScriptableObject.CreateInstance<VerificationAsset>();
            try
            {
                AssetDatabase.CreateAsset(asset, path);
                JObject response = JObject.FromObject(
                    ManageScriptableObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(path) },
                            ["patches"] = new JArray(new JObject { ["propertyPath"] = "choice", ["value"] = 1 }),
                        }
                    )
                );
                Assert.IsTrue((bool)response["success"], response.ToString());
                Assert.AreEqual(VerificationChoice.Value1, asset.choice);
                Assert.AreEqual("passed", (string)response["data"]["verification"]["status"], response.ToString());
                JObject raw = PostEditVerification.Properties(asset, new JObject { ["choice"] = 20 });
                Assert.AreEqual("passed", (string)raw["status"], raw.ToString());
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ScriptableObjectPartialPatchesSummarizeRetainedValues(bool malformedPath)
        {
            string path = "Assets/Verification_" + Guid.NewGuid().ToString("N") + ".asset";
            var asset = ScriptableObject.CreateInstance<Fixtures.ManageScriptableObjectTestDefinition>();
            try
            {
                AssetDatabase.CreateAsset(asset, path);
                JObject response = JObject.FromObject(
                    ManageScriptableObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(path) },
                            ["patches"] = new JArray(
                                new JObject { ["propertyPath"] = "displayName", ["value"] = "retained" },
                                new JObject { ["propertyPath"] = malformedPath ? new JArray() : new JValue("missingField"), ["value"] = 3 }
                            ),
                        }
                    )
                );
                Assert.IsTrue((bool)response["success"], response.ToString());
                Assert.AreEqual("retained", asset.DisplayName);
                Assert.AreEqual(1, (int)response["data"]["verification"]["patch_counts"]["applied"]);
                Assert.AreEqual(1, (int)response["data"]["verification"]["patch_counts"]["failed"]);
                Assert.AreEqual("issues_found", (string)response["data"]["verification"]["status"]);
                Assert.IsFalse((bool)response["data"]["verification"]["complete"]);
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
                if (asset != null && !EditorUtility.IsPersistent(asset))
                    UnityEngine.Object.DestroyImmediate(asset);
            }
        }
    }

    public class VerificationProbe : MonoBehaviour
    {
        public int number;
        public int Throwing
        {
            set => throw new InvalidOperationException("fixture setter failure");
        }
        public bool AddSibling
        {
            set
            {
                if (value)
                    gameObject.AddComponent<VerificationProbe>().number = 999;
            }
        }
    }

    public enum VerificationChoice
    {
        Value0 = 10,
        Value1 = 20,
        Value2,
        Value3,
        Value4,
        Value5,
        Value6,
        Value7,
        Value8,
        Value9,
        Value10,
        Value11,
        Value12,
        Value13,
        Value14,
        Value15,
        Value16,
    }
}
