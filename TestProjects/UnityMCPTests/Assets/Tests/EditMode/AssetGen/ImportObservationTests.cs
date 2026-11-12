using System;
using System.Collections;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ImportObservationTests
    {
        private string folder;
        private static Type Observer => typeof(GenerateImage).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Import.ImportObservation", true);

        private static JObject Observe(string method, params object[] arguments) =>
            (JObject)Observer.GetMethod(method, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, arguments);

        [SetUp]
        public void Setup()
        {
            folder = "Assets/__import_observation_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
        }

        [TearDown]
        public void Cleanup() => AssetDatabase.DeleteAsset(folder);

        private string Image()
        {
            var texture = new Texture2D(4, 8);
            string path = folder + "/fixture.png";
            try
            {
                File.WriteAllBytes(path, texture.EncodeToPNG());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        [Test]
        public void CompletedImageStatusReadsActualMetadataAndRetainsJobIdentity()
        {
            string path = Image();
            var job = new AssetGenJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = "image",
                State = AssetGenJobState.Done,
                AssetPath = path,
                AssetGuid = AssetDatabase.AssetPathToGUID(path),
                ProviderTaskId = "original-paid-task",
            };
            var jobs = (IDictionary)typeof(AssetGenJobManager).GetField("Jobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            jobs.Add(job.JobId, job);
            try
            {
                JObject response = JObject.FromObject(GenerateImage.HandleCommand(new JObject { ["action"] = "status", ["job_id"] = job.JobId }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(job.JobId, (string)response["data"]["job_id"]);
                Assert.AreEqual(job.ProviderTaskId, (string)response["data"]["provider_task_id"]);
                Assert.AreEqual("passed", (string)response["data"]["verification"]["status"]);
                Assert.AreEqual(4, (int)response["data"]["verification"]["observed_values"]["width"]);
                Assert.AreEqual(8, (int)response["data"]["verification"]["observed_values"]["height"]);
                Assert.AreSame(job, AssetGenJobManager.GetJob(job.JobId));
            }
            finally
            {
                jobs.Remove(job.JobId);
            }
        }

        [Test]
        public void ChangedImportedGuidCannotPassOrSelectReplacement()
        {
            JObject report = Observe("Read", Image(), new string('0', 32), "image");
            Assert.IsFalse(report.Value<bool>("complete"));
            Assert.AreEqual("unavailable", report.Value<string>("status"));
        }

        [Test]
        public void ModelMissingMaterialReportsExactRendererWithoutInstantiating()
        {
            var root = new GameObject("owned model");
            try
            {
                var renderer = root.AddComponent<MeshRenderer>();
                renderer.sharedMaterials = new Material[] { null };
                JObject report = Observe("Model", root);
                Assert.AreEqual("issues_found", report.Value<string>("status"));
                Assert.AreEqual("missing_material", (string)report["findings"][0]["code"]);
                Assert.AreEqual(1, (int)report["observed_values"]["renderers_inspected"]);
                Assert.AreEqual(1, ((JArray)report["observed_values"]["nodes"]).Count);
                Assert.AreEqual(0, root.transform.childCount);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void ModelHierarchyBudgetReportsIncomplete()
        {
            var root = new GameObject("bounded model");
            try
            {
                for (int i = 0; i < 40; i++)
                    new GameObject("child" + i).transform.SetParent(root.transform);
                JObject report = Observe("Model", root);
                Assert.IsFalse(report.Value<bool>("complete"));
                Assert.AreNotEqual("passed", report.Value<string>("status"));
                Assert.LessOrEqual(((JArray)report["observed_values"]["nodes"]).Count, 32);
                Assert.AreEqual(40, root.transform.childCount);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
        }
    }
}
