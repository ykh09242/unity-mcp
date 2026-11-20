using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ReferenceGraphCommitInputsTests
    {
        private static JObject Command(JObject request)
        {
            var status = JObject.FromObject(ManageReferenceGraph.HandleCommand(new JObject { ["action"] = "status" }))["data"];
            request["expected_instance_id"] = status["instance_id"];
            request["expected_project_id"] = status["project_id"];
            var response = JObject.FromObject(ManageReferenceGraph.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private static JObject Read(string path, bool closing = false) =>
            Command(
                new JObject
                {
                    ["action"] = "commit_inputs",
                    ["paths"] = new JArray(path),
                    ["closing"] = closing,
                }
            );

        private static void WitnessImport(string path)
        {
            Read(path);
            JObject result = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "import", ["path"] = path }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        private static string DiskHash(string path)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
        }

        private static string GitBlobHash(string commit, string path)
        {
            var start = new ProcessStartInfo("git", "cat-file blob " + commit + ":./" + path)
            {
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(Application.dataPath),
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var process = Process.Start(start);
            using var sha = SHA256.Create();
            var digest = sha.ComputeHash(process.StandardOutput.BaseStream);
            Assert.IsTrue(process.WaitForExit(5000));
            Assert.AreEqual(0, process.ExitCode, "Expected the committed EditMode fixture in the open project's checkout.");
            return BitConverter.ToString(digest).Replace("-", "").ToLowerInvariant();
        }

        [Test]
        public void TrackedSourceAndMetadataMatchGitBeforeAndAfterActualDependencyCollection()
        {
            const string path = "Assets/TestMat.mat";
            WitnessImport(path);
            JObject before = Read(path);
            string commit = before.Value<string>("git_head");
            if (string.IsNullOrEmpty(commit))
                Assert.Inconclusive("The Editor project has no accessible Git HEAD.");
            if (
                before.Value<int>("dirty_scenes") != 0
                || before.Value<bool>("compiling")
                || before.Value<bool>("importing")
                || before.Value<bool>("compilation_failed")
            )
                Assert.Inconclusive("The Editor is not at a clean capture boundary.");
            JObject dependencies = Command(new JObject { ["action"] = "dependencies", ["guid"] = AssetDatabase.AssetPathToGUID(path) });
            JObject after = Read(path, true);
            Assert.IsTrue(JToken.DeepEquals(before, after), "Native state changed during dependency collection.");
            var asset = before["assets"][0];
            Assert.IsInstanceOf<JObject>(asset["import_witness"]);
            Assert.IsTrue(JToken.DeepEquals(asset["import_witness"], dependencies["import_witness"]));
            Assert.AreEqual(asset.Value<string>("asset_sha256"), asset["import_witness"].Value<string>("asset_sha256"));
            Assert.AreEqual(asset.Value<string>("meta_sha256"), asset["import_witness"].Value<string>("meta_sha256"));
            Assert.AreEqual(dependencies.Value<string>("fingerprint"), asset.Value<string>("import_hash"));
            Assert.AreEqual(dependencies.Value<string>("file_stamp"), asset.Value<string>("stamp"));
            Assert.AreEqual(dependencies.Value<string>("meta_stamp"), asset.Value<string>("meta_stamp"));
            Assert.AreEqual(GitBlobHash(commit, path), asset.Value<string>("asset_sha256"));
            Assert.AreEqual(GitBlobHash(commit, path + ".meta"), asset.Value<string>("meta_sha256"));
            TestContext.WriteLine("commit=" + commit + " observed=" + asset.ToString(Newtonsoft.Json.Formatting.None));
        }

        [Test]
        public void NativeCommandObservesChangedPrefabWithSameGuidAndAnUnsavedScene()
        {
            string path = "Assets/McpGraphCommit-" + Guid.NewGuid().ToString("N") + ".prefab";
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                Assert.IsTrue(SceneManager.SetActiveScene(scene));
                var source = new GameObject("Commit evidence");
                const string material = "Assets/TestMat.mat";
                var renderer = source.AddComponent<MeshRenderer>();
                renderer.sharedMaterial = AssetDatabase.LoadAssetAtPath<Material>(material);
                Assert.IsNotNull(renderer.sharedMaterial);
                Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(source, path));
                WitnessImport(path);
                EditorSceneManager.MarkSceneDirty(scene);
                JObject first = Read(path);
                var original = first["assets"][0];
                JObject observed = Command(new JObject { ["action"] = "commit_links", ["paths"] = new JArray(path, material) });
                Assert.AreEqual(material, observed["links"][path][0].Value<string>());
                Assert.Greater(first.Value<int>("dirty_scenes"), 0);
                Assert.AreEqual(DiskHash(path), original.Value<string>("asset_sha256"));
                Assert.AreEqual(DiskHash(path + ".meta"), original.Value<string>("meta_sha256"));
                Assert.AreEqual(AssetDatabase.GetAssetDependencyHash(path).ToString(), original.Value<string>("import_hash"));
                byte[] saved = File.ReadAllBytes(path);
                var replacement = (byte[])saved.Clone();
                replacement[replacement.Length / 2] ^= 1;
                DateTime stamp = File.GetLastWriteTimeUtc(path);
                AssetDatabase.DisallowAutoRefresh();
                try
                {
                    File.WriteAllBytes(path, replacement);
                    File.SetLastWriteTimeUtc(path, stamp);
                    var stale = Read(path, true)["assets"][0];
                    Assert.AreEqual(original.Value<string>("stamp"), stale.Value<string>("stamp"));
                    Assert.AreEqual(original.Value<string>("guid"), stale.Value<string>("guid"));
                    Assert.AreEqual(original.Value<string>("import_hash"), stale.Value<string>("import_hash"));
                    Assert.AreEqual(original.Value<string>("asset_sha256"), stale["import_witness"].Value<string>("asset_sha256"));
                    Assert.AreNotEqual(stale.Value<string>("asset_sha256"), stale["import_witness"].Value<string>("asset_sha256"));
                }
                finally
                {
                    File.WriteAllBytes(path, saved);
                    File.SetLastWriteTimeUtc(path, stamp);
                    AssetDatabase.AllowAutoRefresh();
                }

                source.transform.localScale = new Vector3(2, 3, 4);
                Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(source, path));
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                var changed = Read(path, true)["assets"][0];
                Assert.AreEqual(original.Value<string>("guid"), changed.Value<string>("guid"));
                Assert.AreNotEqual(original.Value<string>("asset_sha256"), changed.Value<string>("asset_sha256"));
                Assert.AreEqual(DiskHash(path), changed.Value<string>("asset_sha256"));
            }
            finally
            {
                EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                AssetDatabase.DeleteAsset(path);
            }
        }
    }
}
