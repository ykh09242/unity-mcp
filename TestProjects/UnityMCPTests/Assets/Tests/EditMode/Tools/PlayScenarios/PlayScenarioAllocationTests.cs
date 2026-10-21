using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioAllocationTests
    {
        [Test]
        public void CancellationPollingHasBoundedLatencyAndLatchesTheRequest()
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-cancel-poll-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string cancel = Path.Combine(directory, "cancel");
            try
            {
                var probe = new PlayScenarioPlayerCancellation(directory);
                Assert.IsFalse(probe.Poll(0));
                File.WriteAllText(cancel, "");
                Assert.IsFalse(probe.Poll(99), "No filesystem revalidation is due before the next 100 ms poll.");
                Assert.IsTrue(probe.Poll(100));
                File.Delete(cancel);
                Assert.IsTrue(probe.Poll(101), "An observed cancellation cannot be withdrawn by deleting the file.");
            }
            finally
            {
                if (File.Exists(cancel))
                    File.Delete(cancel);
                Directory.Delete(directory);
            }
        }

        [Test]
        public void LargeHierarchyQueryBurstsReuseStorageAndReleaseReferences()
        {
            Scene scene = EditorSceneManager.NewPreviewScene();
            var query = new PlayScenarioHierarchyQuery();
            try
            {
                for (int i = 0; i < 5000; i++)
                    SceneManager.MoveGameObjectToScene(new GameObject("root"), scene);
                QueryLargeScene(query, scene);
                using var recorder = ProfilerRecorder.StartNew(
                    ProfilerCategory.Memory,
                    "GC.Alloc",
                    8192,
                    ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                );
                for (int i = 0; i < 20; i++)
                    QueryLargeScene(query, scene);
                recorder.Stop();
                Assert.IsTrue(recorder.Valid);
                Assert.AreEqual(0, query.Roots.Count, "Every query releases live root references.");
                Assert.AreEqual(0, query.Count, "Every query releases live traversal references.");
                Assert.AreEqual(0, recorder.Count, "Large active bursts must not repeatedly grow and trim backing arrays.");
            }
            finally
            {
                query.Clear();
                if (scene.IsValid())
                    EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        [Test]
        public void LargeHierarchyStorageSurvivesBurstsButTrimsAfterIdleAndRelease()
        {
            long now = 0;
            var query = new PlayScenarioHierarchyQuery(() => now);
            query.Roots.Capacity = 10000;
            query.Clear();
            now = System.Diagnostics.Stopwatch.Frequency * 29L;
            query.Maintain();
            Assert.AreEqual(10000, query.Roots.Capacity);
            now = System.Diagnostics.Stopwatch.Frequency * 30L;
            query.Maintain();
            Assert.AreEqual(4096, query.Roots.Capacity);
            query.Roots.Capacity = 10000;
            query.Release();
            Assert.AreEqual(0, query.Roots.Capacity);
            Assert.AreEqual(0, query.Count);
        }

        [Test]
        public void IdQueriesInspectMarkersAndObserveInactiveCopiesAndChanges()
        {
            Scene scene = EditorSceneManager.NewPreviewScene();
            var scratch = new PlayScenarioHierarchyQuery();
            try
            {
                for (int i = 0; i < 5000; i++)
                    SceneManager.MoveGameObjectToScene(new GameObject("unmarked"), scene);
                var go = new GameObject("marked");
                SceneManager.MoveGameObjectToScene(go, scene);
                go.AddComponent<PlayScenarioTarget>().TargetId = "owned-id";
                PlayScenarioQueryCounts counts = default;
                Assert.AreSame(go, PlayScenarioObjectCondition.ResolveId(scene, "owned-id", ref counts, scratch).Target);
                Assert.Less(counts.HierarchyVisits, 10, "Unmarked hierarchy objects must not require managed per-object visits for ID lookup.");
                go.SetActive(false);
                var duplicate = UnityEngine.Object.Instantiate(go);
                SceneManager.MoveGameObjectToScene(duplicate, scene);
                Assert.Throws<PlayScenarioException>(() => PlayScenarioObjectCondition.ResolveId(scene, "owned-id"));
                duplicate.GetComponent<PlayScenarioTarget>().TargetId = "changed-id";
                Assert.AreEqual(1, PlayScenarioObjectCondition.ResolveId(scene, "owned-id").Count);
                UnityEngine.Object.DestroyImmediate(go);
                Assert.AreEqual(0, PlayScenarioObjectCondition.ResolveId(scene, "owned-id").Count);
                Assert.AreSame(duplicate, PlayScenarioObjectCondition.ResolveId(scene, "changed-id").Target);
            }
            finally
            {
                scratch.Release();
                EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        private static void QueryLargeScene(PlayScenarioHierarchyQuery query, Scene scene)
        {
            query.Begin(scene);
            for (int i = 0; i < query.Roots.Count; i++)
                query.Push(query.Roots[i].transform, 0);
            while (query.Count != 0)
                query.Pop();
            query.Clear();
        }

        [Test]
        public void WarmHierarchyQueriesAllocateNoRootArraysAndObserveFreshObjects()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewPreviewScene();
            var query = new PlayScenarioHierarchyQuery();
            try
            {
                var first = new GameObject("first");
                SceneManager.MoveGameObjectToScene(first, scene);
                query.Begin(scene);
                Assert.Contains(first, query.Roots);
                query.Clear();
                using var recorder = ProfilerRecorder.StartNew(
                    ProfilerCategory.Memory,
                    "GC.Alloc",
                    8192,
                    ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                );
                for (int i = 0; i < 100; i++)
                {
                    query.Begin(scene);
                    query.Clear();
                }
                recorder.Stop();
                Assert.IsTrue(recorder.Valid);
                Assert.AreEqual(0, recorder.Count, "Warm list-based root queries must not allocate arrays per poll.");
                UnityEngine.Object.DestroyImmediate(first);
                var replacement = new GameObject("replacement");
                SceneManager.MoveGameObjectToScene(replacement, scene);
                query.Begin(scene);
                Assert.Contains(replacement, query.Roots);
                Assert.IsFalse(query.Roots.Contains(first));
                query.Clear();
                Assert.AreEqual(0, query.Roots.Count);
            }
            finally
            {
                query.Clear();
                if (scene.IsValid())
                    EditorSceneManager.ClosePreviewScene(scene);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
            }
        }
    }
}
