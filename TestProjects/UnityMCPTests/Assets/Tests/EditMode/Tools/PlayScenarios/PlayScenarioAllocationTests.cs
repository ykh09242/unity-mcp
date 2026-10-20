using System;
using System.IO;
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
