using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// Tests for Prefab CRUD operations: create_from_gameobject, get_info, get_hierarchy, modify_contents.
    /// </summary>
    public class ManagePrefabsCrudTests
    {
        private const string TempDirectory = "Assets/Temp/ManagePrefabsCrudTests";

        [SetUp]
        public void SetUp()
        {
            StageUtility.GoToMainStage();
            EnsureFolder(TempDirectory);
        }

        [TearDown]
        public void TearDown()
        {
            StageUtility.GoToMainStage();

            if (AssetDatabase.IsValidFolder(TempDirectory))
            {
                AssetDatabase.DeleteAsset(TempDirectory);
            }

            CleanupEmptyParentFolders(TempDirectory);
        }

        #region CREATE Tests

        [TestCase("integer_id")]
        [TestCase("string_id")]
        [TestCase("name")]
        [TestCase("path")]
        [TestCase("absolute_path")]
        public void CreateFromGameObject_TargetFormsReturnSavedRootForSubsequentEdit(string targetForm)
        {
            string prefabPath = TempDirectory + "/ProbeCube.prefab";
            GameObject parent = new GameObject("PrefabTargetParent");
            GameObject source = new GameObject("McpProbeCube");
            source.transform.SetParent(parent.transform);
            GameObject child = new GameObject("PreservedChild");
            child.transform.SetParent(source.transform);
            JToken target = source.name;
            if (targetForm == "integer_id")
                target = source.GetInstanceIDCompat();
            if (targetForm == "string_id")
                target = source.GetInstanceIDCompat().ToString();
            if (targetForm == "path")
                target = "PrefabTargetParent/McpProbeCube";
            if (targetForm == "absolute_path")
                target = "/PrefabTargetParent/McpProbeCube";
            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = target,
                            ["prefabPath"] = prefabPath,
                        }
                    )
                );
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                GameObject saved = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(saved);
                Assert.AreEqual("ProbeCube", saved.name);
                Assert.AreEqual(saved.name, result["data"].Value<string>("rootObjectName"));
                Assert.AreEqual(saved.name, result["data"].Value<string>("rootObjectPath"));
                Assert.AreEqual("McpProbeCube", source.name);
                Assert.AreEqual(source.name, result["data"].Value<string>("instanceName"));
                Assert.AreEqual(parent.transform, source.transform.parent);
                Assert.IsNotNull(saved.transform.Find("PreservedChild"));
                var modified = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = result["data"]["rootObjectPath"],
                            ["scale"] = new JArray(0.75f, 0.75f, 0.75f),
                        }
                    )
                );
                Assert.IsTrue(modified.Value<bool>("success"), modified.ToString());
                Assert.AreEqual(Vector3.one * 0.75f, AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath).transform.localScale);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
                UnityEngine.Object.DestroyImmediate(parent);
            }
        }

        [Test]
        public void CreateFromGameObject_RejectsAmbiguousNameBeforeSaving()
        {
            string prefabPath = TempDirectory + "/Ambiguous.prefab";
            GameObject first = new GameObject("DuplicatePrefabTarget");
            GameObject second = new GameObject("DuplicatePrefabTarget");
            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = first.name,
                            ["prefabPath"] = prefabPath,
                        }
                    )
                );
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("ambiguous", result.Value<string>("error"));
                Assert.IsNull(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath));
                Assert.IsFalse(PrefabUtility.IsPartOfPrefabInstance(first));
                Assert.IsFalse(PrefabUtility.IsPartOfPrefabInstance(second));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
                UnityEngine.Object.DestroyImmediate(first);
                UnityEngine.Object.DestroyImmediate(second);
            }
        }

        [Test]
        public void CreateFromGameObject_CreatesNewPrefab()
        {
            string prefabPath = Path.Combine(TempDirectory, "NewPrefab.prefab").Replace('\\', '/');
            GameObject sceneObject = new GameObject("TestObject");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = sceneObject.name,
                            ["prefabPath"] = prefabPath,
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));
                Assert.AreEqual(prefabPath, result["data"].Value<string>("prefabPath"));
                Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
                if (sceneObject != null)
                    UnityEngine.Object.DestroyImmediate(sceneObject, true);
            }
        }

        [Test]
        public void CreateFromGameObject_HandlesExistingPrefabsAndLinks()
        {
            // Tests: unlinkIfInstance, allowOverwrite, unique path generation
            string prefabPath = Path.Combine(TempDirectory, "Existing.prefab").Replace('\\', '/');
            GameObject sourceObject = new GameObject("SourceObject");

            try
            {
                // Create initial prefab and link source object
                PrefabUtility.SaveAsPrefabAssetAndConnect(sourceObject, prefabPath, InteractionMode.AutomatedAction);
                Assert.IsTrue(PrefabUtility.IsAnyPrefabInstanceRoot(sourceObject));

                // Without unlink - should fail (already linked)
                string newPath = Path.Combine(TempDirectory, "New.prefab").Replace('\\', '/');
                var failResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = sourceObject.name,
                            ["prefabPath"] = newPath,
                        }
                    )
                );
                Assert.IsFalse(failResult.Value<bool>("success"));
                Assert.IsTrue(failResult.Value<string>("error").Contains("already linked"));

                // With unlinkIfInstance - should succeed
                var unlinkResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = sourceObject.name,
                            ["prefabPath"] = newPath,
                            ["unlinkIfInstance"] = true,
                        }
                    )
                );
                Assert.IsTrue(unlinkResult.Value<bool>("success"));
                Assert.IsTrue(unlinkResult["data"].Value<bool>("wasUnlinked"));

                // With allowOverwrite - should replace
                GameObject anotherObject = new GameObject("AnotherObject");
                var overwriteResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = anotherObject.name,
                            ["prefabPath"] = newPath,
                            ["allowOverwrite"] = true,
                        }
                    )
                );
                Assert.IsTrue(overwriteResult.Value<bool>("success"));
                Assert.IsTrue(overwriteResult["data"].Value<bool>("wasReplaced"));
                UnityEngine.Object.DestroyImmediate(anotherObject, true);

                // Without overwrite on existing - should generate unique path
                GameObject thirdObject = new GameObject("ThirdObject");
                var uniqueResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = thirdObject.name,
                            ["prefabPath"] = newPath,
                        }
                    )
                );
                Assert.IsTrue(uniqueResult.Value<bool>("success"));
                Assert.AreNotEqual(newPath, uniqueResult["data"].Value<string>("prefabPath"));
                SafeDeleteAsset(uniqueResult["data"].Value<string>("prefabPath"));
                UnityEngine.Object.DestroyImmediate(thirdObject, true);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
                SafeDeleteAsset(Path.Combine(TempDirectory, "New.prefab").Replace('\\', '/'));
                if (sourceObject != null)
                    UnityEngine.Object.DestroyImmediate(sourceObject, true);
            }
        }

        [Test]
        public void CreateFromGameObject_FindsInactiveObject_WhenSearchInactiveIsTrue()
        {
            string prefabPath = Path.Combine(TempDirectory, "InactiveTest.prefab").Replace('\\', '/');
            GameObject inactiveObject = new GameObject("InactiveObject");
            inactiveObject.SetActive(false);

            try
            {
                // Without searchInactive - should fail to find inactive object
                var failResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = inactiveObject.name,
                            ["prefabPath"] = prefabPath,
                        }
                    )
                );
                Assert.IsFalse(failResult.Value<bool>("success"));

                // With searchInactive - should succeed
                var successResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = inactiveObject.name,
                            ["prefabPath"] = prefabPath,
                            ["searchInactive"] = true,
                        }
                    )
                );
                Assert.IsTrue(successResult.Value<bool>("success"));
                Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
                if (inactiveObject != null)
                    UnityEngine.Object.DestroyImmediate(inactiveObject, true);
            }
        }

        #endregion

        #region READ Tests

        [Test]
        public void GetInfo_ReturnsMetadata()
        {
            string prefabPath = CreateTestPrefab("InfoTest");

            try
            {
                var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "get_info", ["prefabPath"] = prefabPath }));

                Assert.IsTrue(result.Value<bool>("success"));
                var data = result["data"] as JObject;
                Assert.AreEqual(prefabPath, data.Value<string>("assetPath"));
                Assert.IsNotNull(data.Value<string>("guid"));
                Assert.AreEqual("Regular", data.Value<string>("prefabType"));
                Assert.AreEqual("InfoTest", data.Value<string>("rootObjectName"));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void GetHierarchy_ReturnsHierarchyWithNestingInfo()
        {
            // Create a prefab with nested prefab instance
            string childPrefabPath = CreateTestPrefab("ChildPrefab");
            string containerPath = null;

            try
            {
                GameObject container = new GameObject("Container");
                GameObject child1 = new GameObject("Child1");
                child1.transform.parent = container.transform;

                // Add nested prefab instance
                GameObject nestedInstance = PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(childPrefabPath)) as GameObject;
                nestedInstance.transform.parent = container.transform;

                containerPath = Path.Combine(TempDirectory, "Container.prefab").Replace('\\', '/');
                PrefabUtility.SaveAsPrefabAsset(container, containerPath, out bool _);
                UnityEngine.Object.DestroyImmediate(container);
                AssetDatabase.Refresh();

                var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "get_hierarchy", ["prefabPath"] = containerPath }));

                Assert.IsTrue(result.Value<bool>("success"));
                var data = result["data"] as JObject;
                var items = data["items"] as JArray;
                Assert.IsTrue(data.Value<int>("total") >= 3); // Container, Child1, nested prefab

                // Verify root and nested prefab info
                var root = items.Cast<JObject>().FirstOrDefault(j => j["prefab"]["isRoot"].Value<bool>());
                Assert.IsNotNull(root);
                Assert.AreEqual("Container", root.Value<string>("name"));

                var nested = items.Cast<JObject>().FirstOrDefault(j => j["prefab"]["isNestedRoot"].Value<bool>());
                Assert.IsNotNull(nested);
                Assert.AreEqual(1, nested["prefab"]["nestingDepth"].Value<int>());
            }
            finally
            {
                if (containerPath != null)
                    SafeDeleteAsset(containerPath);
                SafeDeleteAsset(childPrefabPath);
            }
        }

        #endregion

        #region UPDATE Tests (ModifyContents)

        private string CreatePagingPrefab(int width, int depth = 0, int nameLength = 9)
        {
            string path = TempDirectory + "/Paging.prefab";
            var root = new GameObject("PagingRoot");
            try
            {
                for (int i = 0; i < width; i++)
                    new GameObject(new string('x', nameLength)).transform.SetParent(root.transform, false);
                Transform parent = root.transform;
                for (int i = 0; i < depth; i++)
                {
                    var child = new GameObject("Depth" + i);
                    child.transform.SetParent(parent, false);
                    parent = child.transform;
                }
                PrefabUtility.SaveAsPrefabAsset(root, path);
                return path;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        private static JObject ReadHierarchyPage(string path, int size = 50, string cursor = null, int depth = 64)
        {
            return ToJObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_hierarchy",
                        ["prefabPath"] = path,
                        ["page_size"] = size,
                        ["max_depth"] = depth,
                        ["cursor"] = cursor,
                    }
                )
            );
        }

        [Test]
        public void HierarchyPaging_WideStablePrefabHasOrderedUniqueContinuationAndUnloads()
        {
            string path = CreatePagingPrefab(603);
            int scenesBefore = UnityEngine.SceneManagement.SceneManager.sceneCount;
            int previewsBefore = EditorSceneManager.previewSceneCount;
            var keys = new List<string>();
            string cursor = null;
            int pages = 0;
            do
            {
                var response = ReadHierarchyPage(path, 37, cursor);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = (JObject)response["data"];
                var items = (JArray)data["items"];
                Assert.LessOrEqual(items.Count, 37);
                Assert.LessOrEqual(data.Value<int>("work_used"), data.Value<int>("work_limit"));
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 262144);
                keys.AddRange(items.Select(item => item.Value<string>("hierarchyKey")));
                cursor = data.Value<string>("next_cursor");
                if (cursor != null)
                {
                    Assert.IsFalse(data.Value<bool>("total_known"));
                    Assert.AreEqual(JTokenType.Null, data["total"].Type);
                }
                else
                    Assert.AreEqual(604, data.Value<int>("total"));
                Assert.AreEqual(scenesBefore, UnityEngine.SceneManagement.SceneManager.sceneCount);
                Assert.AreEqual(previewsBefore, EditorSceneManager.previewSceneCount);
                Assert.Less(++pages, 30);
            } while (cursor != null);
            Assert.AreEqual(604, keys.Count);
            Assert.AreEqual(keys.Count, keys.Distinct().Count());
            CollectionAssert.AreEqual(new[] { "0", "0/0", "0/1" }, keys.Take(3));
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
        }

        [Test]
        public void HierarchyPaging_DeepScopeAndOutputAreExplicitlyBounded()
        {
            string path = CreatePagingPrefab(0, 150);
            var data = (JObject)ReadHierarchyPage(path, 500, depth: 12)["data"];
            Assert.AreEqual(13, ((JArray)data["items"]).Count);
            Assert.IsTrue(data.Value<bool>("depth_limited"));
            Assert.AreEqual(12, data.Value<int>("max_depth"));
            Assert.IsNull(data.Value<string>("next_cursor"));
            path = CreatePagingPrefab(200, nameLength: 4096);
            var response = ReadHierarchyPage(path, 500);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 262144);
            Assert.IsTrue(response["data"].Value<bool>("metadata_truncated"));
        }

        [TestCase("page_size", "0")]
        [TestCase("page_size", "501")]
        [TestCase("page_size", "1.5")]
        [TestCase("page_size", "true")]
        [TestCase("max_depth", "-1")]
        [TestCase("max_depth", "129")]
        [TestCase("cursor", "123")]
        public void HierarchyPaging_InvalidOptionsAreRejectedWithoutLoading(string key, string json)
        {
            string path = CreatePagingPrefab(1);
            int count = UnityEngine.SceneManagement.SceneManager.sceneCount;
            var response = ToJObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_hierarchy",
                        ["prefabPath"] = path,
                        [key] = JToken.Parse(json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(count, UnityEngine.SceneManagement.SceneManager.sceneCount);
        }

        [Test]
        public void HierarchyPaging_CursorRejectsChangedOptionsAndSavedSource()
        {
            string path = CreatePagingPrefab(8);
            string cursor = ReadHierarchyPage(path, 2)["data"].Value<string>("next_cursor");
            Assert.IsNotEmpty(cursor);
            Assert.IsFalse(ReadHierarchyPage(path, 3, cursor).Value<bool>("success"));
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.transform.GetChild(0).name = "Changed";
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            var response = ReadHierarchyPage(path, 2, cursor);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("stale_cursor", response.ToString());
        }

        [Test]
        public void HierarchyPaging_CancellationIsCheckedAfterWorkHasStarted()
        {
            var helper = typeof(ManagePrefabs).Assembly.GetType("MCPForUnity.Editor.Helpers.HierarchyPaging", true);
            var type = helper.GetNestedType("Budget", BindingFlags.NonPublic);
            using var cancellation = new CancellationTokenSource();
            var budget = Activator.CreateInstance(type, BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { cancellation.Token }, null);
            var spend = type.GetMethod("Spend", BindingFlags.Instance | BindingFlags.NonPublic);
            spend.Invoke(budget, new object[] { 1 });
            cancellation.Cancel();
            var exception = Assert.Throws<TargetInvocationException>(() => spend.Invoke(budget, new object[] { 1 }));
            Assert.IsInstanceOf<OperationCanceledException>(exception.InnerException);
            Assert.AreEqual(1, type.GetField("Used", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(budget));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void HierarchyPaging_LoadedScopeUnloadsAfterCancellationOrReadFailure(bool cancel)
        {
            string path = CreatePagingPrefab(603);
            var helper = typeof(ManagePrefabs).Assembly.GetType("MCPForUnity.Editor.Helpers.HierarchyPaging", true);
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            var options = helper.GetMethod("Parse", flags).Invoke(null, new object[] { new JObject(), false });
            var state = helper.GetMethod("Resume", flags).Invoke(null, new object[] { options, "prefab:" + path, "owned-revision" });
            var build = typeof(ManagePrefabs).GetMethod("BuildHierarchyPage", flags);
            var lifecycle = typeof(ManagePrefabs).GetMethod("ReadLoadedPrefab", flags);
            int scenes = UnityEngine.SceneManagement.SceneManager.sceneCount;
            int previews = EditorSceneManager.previewSceneCount;
            using var cancellation = new CancellationTokenSource();
            bool observedLoadedPreview = false;
            Func<GameObject, object> read = contents =>
            {
                observedLoadedPreview = EditorSceneManager.IsPreviewScene(contents.scene) && EditorSceneManager.previewSceneCount > previews;
                if (!cancel)
                    throw new InvalidOperationException("Owned read failure after native load.");
                cancellation.Cancel();
                try
                {
                    return build.Invoke(null, new object[] { contents.transform, path, options, state, cancellation.Token });
                }
                catch (TargetInvocationException exception)
                {
                    throw exception.InnerException ?? exception;
                }
            };
            var failure = Assert.Throws<TargetInvocationException>(() => lifecycle.Invoke(null, new object[] { path, read }));
            Assert.IsTrue(observedLoadedPreview, "The actual read scope must contain a loaded prefab preview before failure/cancellation.");
            if (cancel)
                Assert.IsInstanceOf<OperationCanceledException>(failure.InnerException);
            else
                Assert.IsInstanceOf<InvalidOperationException>(failure.InnerException);
            Assert.AreEqual(scenes, UnityEngine.SceneManagement.SceneManager.sceneCount);
            Assert.AreEqual(previews, EditorSceneManager.previewSceneCount);
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
        }

        [Test]
        public void HierarchyPaging_TokenOverloadPreservesSuccessAndRejectsPreCancelledWork()
        {
            string path = CreatePagingPrefab(1);
            var request = new JObject { ["action"] = "get_hierarchy", ["prefabPath"] = path };
            var response = ToJObject(ManagePrefabs.HandleCommand(request, CancellationToken.None).GetAwaiter().GetResult());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2, ((JArray)response["data"]["items"]).Count);
            int previews = EditorSceneManager.previewSceneCount;
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<OperationCanceledException>(() => ManagePrefabs.HandleCommand(request, cancellation.Token));
            Assert.AreEqual(previews, EditorSceneManager.previewSceneCount);
        }

        [Test]
        public void HierarchyPaging_DeepContinuationResumesAfterWorkLimitWithoutOmissions()
        {
            string path = CreatePagingPrefab(0, 150);
            string cursor = null;
            var keys = new List<string>();
            bool workLimited = false;
            int pages = 0;
            int previews = EditorSceneManager.previewSceneCount;
            do
            {
                var response = ReadHierarchyPage(path, 500, cursor, 128);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = (JObject)response["data"];
                var items = (JArray)data["items"];
                Assert.Greater(items.Count, 0, "Every continuation must make progress.");
                Assert.LessOrEqual(data.Value<int>("work_used"), 4096);
                Assert.AreEqual(1409, data.Value<int>("hierarchy_key_limit"));
                Assert.AreEqual(512, data.Value<int>("string_limit"));
                foreach (var item in items)
                    Assert.LessOrEqual(item.Value<string>("hierarchyKey").Length, data.Value<int>("hierarchy_key_limit"));
                workLimited |= data.Value<bool>("work_limited");
                keys.AddRange(items.Select(item => item.Value<string>("hierarchyKey")));
                cursor = data.Value<string>("next_cursor");
                if (cursor == null)
                {
                    Assert.AreEqual(129, data.Value<int>("total"));
                    Assert.IsTrue(data.Value<bool>("depth_limited"));
                }
                Assert.AreEqual(previews, EditorSceneManager.previewSceneCount);
                Assert.Less(++pages, 30);
            } while (cursor != null);
            Assert.IsTrue(workLimited);
            Assert.AreEqual(129, keys.Count);
            Assert.AreEqual(129, keys.Distinct().Count());
            Assert.AreEqual(1409, ("0" + string.Concat(Enumerable.Repeat("/" + int.MaxValue, 128))).Length);
            for (int i = 0; i < keys.Count; i++)
                Assert.AreEqual("0" + string.Concat(Enumerable.Repeat("/0", i)), keys[i]);
        }

        [Test]
        public void HierarchyPaging_OutputLimitedContinuationMakesProgressAndUnloads()
        {
            string path = CreatePagingPrefab(400, nameLength: 4096);
            string cursor = null;
            var keys = new List<string>();
            bool outputLimited = false;
            int previews = EditorSceneManager.previewSceneCount;
            int pages = 0;
            do
            {
                var response = ReadHierarchyPage(path, 500, cursor);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = (JObject)response["data"];
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 262144);
                Assert.Greater(((JArray)data["items"]).Count, 0);
                outputLimited |= data.Value<bool>("output_limited");
                keys.AddRange(((JArray)data["items"]).Select(item => item.Value<string>("hierarchyKey")));
                cursor = data.Value<string>("next_cursor");
                Assert.AreEqual(previews, EditorSceneManager.previewSceneCount);
                Assert.Less(++pages, 10);
            } while (cursor != null);
            Assert.IsTrue(outputLimited);
            Assert.AreEqual(401, keys.Count);
            Assert.AreEqual(401, keys.Distinct().Count());
        }

        [Test]
        public void ModifyContents_ModifiesTransformWithoutOpeningStage()
        {
            string prefabPath = CreateTestPrefab("ModifyTest");

            try
            {
                StageUtility.GoToMainStage();
                Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());

                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["position"] = new JArray(1f, 2f, 3f),
                            ["rotation"] = new JArray(45f, 0f, 0f),
                            ["scale"] = new JArray(2f, 2f, 2f),
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                // Verify no stage was opened (headless editing)
                Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());

                // Verify changes persisted
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.AreEqual(new Vector3(1f, 2f, 3f), reloaded.transform.localPosition);
                Assert.AreEqual(new Vector3(2f, 2f, 2f), reloaded.transform.localScale);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_TargetsChildrenByNameAndPath()
        {
            string prefabPath = CreateNestedTestPrefab("TargetTest");

            try
            {
                // Target by name
                var nameResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1",
                            ["position"] = new JArray(10f, 10f, 10f),
                        }
                    )
                );
                Assert.IsTrue(nameResult.Value<bool>("success"));

                // Target by path
                var pathResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1/Grandchild",
                            ["scale"] = new JArray(3f, 3f, 3f),
                        }
                    )
                );
                Assert.IsTrue(pathResult.Value<bool>("success"));

                // Verify changes
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.AreEqual(new Vector3(10f, 10f, 10f), reloaded.transform.Find("Child1").localPosition);
                Assert.AreEqual(new Vector3(3f, 3f, 3f), reloaded.transform.Find("Child1/Grandchild").localScale);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_AddsAndRemovesComponents()
        {
            string prefabPath = CreateTestPrefab("ComponentTest");
            // Cube primitive has BoxCollider by default

            try
            {
                // Add Rigidbody
                var addResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentsToAdd"] = new JArray("Rigidbody"),
                        }
                    )
                );
                Assert.IsTrue(addResult.Value<bool>("success"));

                // Remove BoxCollider
                var removeResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentsToRemove"] = new JArray("BoxCollider"),
                        }
                    )
                );
                Assert.IsTrue(removeResult.Value<bool>("success"));

                // Verify
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(reloaded.GetComponent<Rigidbody>());
                Assert.IsNull(reloaded.GetComponent<BoxCollider>());
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_SetsPropertiesAndRenames()
        {
            string prefabPath = CreateNestedTestPrefab("PropertiesTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1",
                            ["name"] = "RenamedChild",
                            ["tag"] = "MainCamera",
                            ["layer"] = "UI",
                            ["setActive"] = false,
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Transform renamed = reloaded.transform.Find("RenamedChild");
                Assert.IsNotNull(renamed);
                Assert.IsNull(reloaded.transform.Find("Child1")); // Old name gone
                Assert.AreEqual("MainCamera", renamed.gameObject.tag);
                Assert.AreEqual(LayerMask.NameToLayer("UI"), renamed.gameObject.layer);
                Assert.IsFalse(renamed.gameObject.activeSelf);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_WorksOnComplexMultiComponentPrefab()
        {
            // Create a complex prefab: Vehicle with multiple children, each with multiple components
            string prefabPath = CreateComplexTestPrefab("Vehicle");

            try
            {
                // Modify root - add Rigidbody
                var rootResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentsToAdd"] = new JArray("Rigidbody"),
                        }
                    )
                );
                Assert.IsTrue(rootResult.Value<bool>("success"));

                // Modify child by name - reposition FrontWheel, add SphereCollider
                var wheelResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "FrontWheel",
                            ["position"] = new JArray(0f, 0.5f, 2f),
                            ["componentsToAdd"] = new JArray("SphereCollider"),
                        }
                    )
                );
                Assert.IsTrue(wheelResult.Value<bool>("success"));

                // Modify nested child by path - scale Barrel inside Turret
                var barrelResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Turret/Barrel",
                            ["scale"] = new JArray(0.5f, 0.5f, 3f),
                            ["tag"] = "Player",
                        }
                    )
                );
                Assert.IsTrue(barrelResult.Value<bool>("success"));

                // Remove component from child
                var removeResult = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "BackWheel",
                            ["componentsToRemove"] = new JArray("BoxCollider"),
                        }
                    )
                );
                Assert.IsTrue(removeResult.Value<bool>("success"));

                // Verify all changes persisted
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);

                // Root has Rigidbody
                Assert.IsNotNull(reloaded.GetComponent<Rigidbody>(), "Root should have Rigidbody");

                // FrontWheel repositioned and has SphereCollider
                Transform frontWheel = reloaded.transform.Find("FrontWheel");
                Assert.AreEqual(new Vector3(0f, 0.5f, 2f), frontWheel.localPosition);
                Assert.IsNotNull(frontWheel.GetComponent<SphereCollider>(), "FrontWheel should have SphereCollider");

                // Turret/Barrel scaled and tagged
                Transform barrel = reloaded.transform.Find("Turret/Barrel");
                Assert.AreEqual(new Vector3(0.5f, 0.5f, 3f), barrel.localScale);
                Assert.AreEqual("Player", barrel.gameObject.tag);

                // BackWheel BoxCollider removed
                Transform backWheel = reloaded.transform.Find("BackWheel");
                Assert.IsNull(backWheel.GetComponent<BoxCollider>(), "BackWheel BoxCollider should be removed");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ReparentsChildWithinPrefab()
        {
            string prefabPath = CreateNestedTestPrefab("ReparentTest");

            try
            {
                // Reparent Child2 under Child1
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child2",
                            ["parent"] = "Child1",
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                // Verify Child2 is now under Child1
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNull(reloaded.transform.Find("Child2"), "Child2 should no longer be direct child of root");
                Assert.IsNotNull(reloaded.transform.Find("Child1/Child2"), "Child2 should now be under Child1");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_PreventsHierarchyLoops()
        {
            string prefabPath = CreateNestedTestPrefab("HierarchyLoopTest");

            try
            {
                // Attempt to parent Child1 under its own descendant (Grandchild)
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1",
                            ["parent"] = "Child1/Grandchild",
                        }
                    )
                );

                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsTrue(
                    result.Value<string>("error").Contains("hierarchy loop") || result.Value<string>("error").Contains("would create"),
                    "Error should mention hierarchy loop prevention"
                );
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [TestCase("", "Child1")]
        [TestCase(null, "Child1")]
        [TestCase("", "Child1/Grandchild")]
        public void ModifyContents_RejectsDetachingChildAndPreservesAsset(string parent, string target)
        {
            string prefabPath = CreateNestedTestPrefab("DetachChildTest");

            try
            {
                byte[] originalAsset = File.ReadAllBytes(prefabPath);
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = target,
                            ["name"] = "UnexpectedRename",
                            ["parent"] = parent == null ? JValue.CreateNull() : new JValue(parent),
                        }
                    )
                );

                Assert.IsFalse(result.Value<bool>("success"), $"Expected rejection but got: {result}");
                StringAssert.Contains("Cannot detach", result.Value<string>("error"));
                CollectionAssert.AreEqual(originalAsset, File.ReadAllBytes(prefabPath));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(reloaded.transform.Find("Child1/Grandchild"));
                Assert.IsNotNull(reloaded.transform.Find("Child2"));
                Assert.IsNull(reloaded.transform.Find("UnexpectedRename"));
                Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ReparentsNestedChildToNamedPrefabRoot()
        {
            string prefabPath = CreateNestedTestPrefab("NamedRootReparentTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1/Grandchild",
                            ["parent"] = "NamedRootReparentTest",
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(reloaded.transform.Find("Grandchild"));
                Assert.IsNull(reloaded.transform.Find("Child1/Grandchild"));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_CreateChild_AddsSingleChildWithPrimitive()
        {
            string prefabPath = CreateTestPrefab("CreateChildTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject
                            {
                                ["name"] = "NewSphere",
                                ["primitive_type"] = "Sphere",
                                ["position"] = new JArray(1f, 2f, 3f),
                                ["scale"] = new JArray(0.5f, 0.5f, 0.5f),
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Transform child = reloaded.transform.Find("NewSphere");
                Assert.IsNotNull(child, "Child should exist");
                Assert.AreEqual(new Vector3(1f, 2f, 3f), child.localPosition);
                Assert.AreEqual(new Vector3(0.5f, 0.5f, 0.5f), child.localScale);
                Assert.IsNotNull(child.GetComponent<SphereCollider>(), "Sphere primitive should have SphereCollider");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_CreateChild_AddsEmptyGameObject()
        {
            string prefabPath = CreateTestPrefab("EmptyChildTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject { ["name"] = "EmptyChild", ["position"] = new JArray(0f, 5f, 0f) },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Transform child = reloaded.transform.Find("EmptyChild");
                Assert.IsNotNull(child, "Empty child should exist");
                Assert.AreEqual(new Vector3(0f, 5f, 0f), child.localPosition);
                // Empty GO should only have Transform
                Assert.AreEqual(1, child.GetComponents<Component>().Length, "Empty child should only have Transform");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_CreateChild_AddsMultipleChildrenFromArray()
        {
            string prefabPath = CreateTestPrefab("MultiChildTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JArray
                            {
                                new JObject
                                {
                                    ["name"] = "Child1",
                                    ["primitive_type"] = "Cube",
                                    ["position"] = new JArray(1f, 0f, 0f),
                                },
                                new JObject
                                {
                                    ["name"] = "Child2",
                                    ["primitive_type"] = "Sphere",
                                    ["position"] = new JArray(-1f, 0f, 0f),
                                },
                                new JObject { ["name"] = "Child3", ["position"] = new JArray(0f, 1f, 0f) },
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(reloaded.transform.Find("Child1"), "Child1 should exist");
                Assert.IsNotNull(reloaded.transform.Find("Child2"), "Child2 should exist");
                Assert.IsNotNull(reloaded.transform.Find("Child3"), "Child3 should exist");
                Assert.IsNotNull(reloaded.transform.Find("Child1").GetComponent<BoxCollider>(), "Child1 should be Cube");
                Assert.IsNotNull(reloaded.transform.Find("Child2").GetComponent<SphereCollider>(), "Child2 should be Sphere");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_CreateChild_SupportsNestedParenting()
        {
            string prefabPath = CreateNestedTestPrefab("NestedCreateChildTest");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject
                            {
                                ["name"] = "NewGrandchild",
                                ["parent"] = "Child1",
                                ["primitive_type"] = "Capsule",
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Transform newChild = reloaded.transform.Find("Child1/NewGrandchild");
                Assert.IsNotNull(newChild, "NewGrandchild should be under Child1");
                Assert.IsNotNull(newChild.GetComponent<CapsuleCollider>(), "Should be Capsule primitive");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_CreateChild_ReturnsErrorForInvalidInput()
        {
            string prefabPath = CreateTestPrefab("InvalidChildTest");

            try
            {
                // Missing required 'name' field
                var missingName = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject { ["primitive_type"] = "Cube" },
                        }
                    )
                );
                Assert.IsFalse(missingName.Value<bool>("success"));
                Assert.IsTrue(missingName.Value<string>("error").Contains("name"));

                // Invalid parent
                var invalidParent = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject { ["name"] = "TestChild", ["parent"] = "NonexistentParent" },
                        }
                    )
                );
                Assert.IsFalse(invalidParent.Value<bool>("success"));
                Assert.IsTrue(invalidParent.Value<string>("error").Contains("not found"));

                // Invalid primitive type
                var invalidPrimitive = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["createChild"] = new JObject { ["name"] = "TestChild", ["primitive_type"] = "InvalidType" },
                        }
                    )
                );
                Assert.IsFalse(invalidPrimitive.Value<bool>("success"));
                Assert.IsTrue(invalidPrimitive.Value<string>("error").Contains("Invalid primitive type"));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        #endregion

        #region Delete Child Tests

        [Test]
        public void ModifyContents_DeleteChild_DeletesSingleChild()
        {
            string prefabPath = CreateNestedTestPrefab("DeleteSingleChild");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["deleteChild"] = "Child1",
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNull(reloaded.transform.Find("Child1"), "Child1 should be deleted");
                Assert.IsNotNull(reloaded.transform.Find("Child2"), "Child2 should still exist");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_DeleteChild_DeletesNestedChild()
        {
            string prefabPath = CreateNestedTestPrefab("DeleteNestedChild");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1",
                            ["deleteChild"] = "Grandchild",
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNull(reloaded.transform.Find("Child1/Grandchild"), "Grandchild should be deleted");
                Assert.IsNotNull(reloaded.transform.Find("Child1"), "Child1 should still exist");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_DeleteChild_DeletesMultipleChildrenFromArray()
        {
            string prefabPath = CreateNestedTestPrefab("DeleteMultipleChildren");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["deleteChild"] = new JArray { "Child1", "Child2" },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNull(reloaded.transform.Find("Child1"), "Child1 should be deleted");
                Assert.IsNull(reloaded.transform.Find("Child2"), "Child2 should be deleted");
                // Only the root should remain
                Assert.AreEqual(0, reloaded.transform.childCount, "Root should have no children");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_DeleteChild_ReturnsErrorForNonexistentChild()
        {
            string prefabPath = CreateNestedTestPrefab("DeleteNonexistentChild");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["deleteChild"] = "DeleteNonexistentChild", // This also tests whether it searches itself
                        }
                    )
                );

                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsTrue(result.Value<string>("error").Contains("not found"), $"Expected 'not found' error but got: {result.Value<string>("error")}");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        #endregion

        #region Component Properties Tests

        [Test]
        public void ModifyContents_ComponentProperties_SetsSimpleProperties()
        {
            string prefabPath = CreatePrefabWithComponents("CompPropSimple", typeof(Rigidbody));

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentProperties"] = new JObject
                            {
                                ["Rigidbody"] = new JObject { ["mass"] = 42f, ["useGravity"] = false },
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");
                Assert.IsTrue(result["data"].Value<bool>("modified"));

                // Verify changes persisted
                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                var rb = reloaded.GetComponent<Rigidbody>();
                Assert.IsNotNull(rb);
                Assert.AreEqual(42f, rb.mass, 0.01f);
                Assert.IsFalse(rb.useGravity);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ComponentProperties_SetsMultipleComponents()
        {
            string prefabPath = CreatePrefabWithComponents("CompPropMulti", typeof(Rigidbody), typeof(Light));

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentProperties"] = new JObject
                            {
                                ["Rigidbody"] = new JObject { ["mass"] = 10f },
                                ["Light"] = new JObject { ["intensity"] = 3.5f },
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.AreEqual(10f, reloaded.GetComponent<Rigidbody>().mass, 0.01f);
                Assert.AreEqual(3.5f, reloaded.GetComponent<Light>().intensity, 0.01f);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ComponentProperties_SetsOnChildTarget()
        {
            // Create a prefab with a child that has a Rigidbody
            EnsureFolder(TempDirectory);
            GameObject root = new GameObject("ChildTargetTest");
            GameObject child = new GameObject("Child1") { transform = { parent = root.transform } };
            child.AddComponent<Rigidbody>();

            string prefabPath = Path.Combine(TempDirectory, "ChildTargetTest.prefab").Replace('\\', '/');
            PrefabUtility.SaveAsPrefabAsset(root, prefabPath, out bool success);
            UnityEngine.Object.DestroyImmediate(root);
            AssetDatabase.Refresh();
            Assert.IsTrue(success);

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "Child1",
                            ["componentProperties"] = new JObject
                            {
                                ["Rigidbody"] = new JObject { ["mass"] = 99f, ["drag"] = 2.5f },
                            },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                var childRb = reloaded.transform.Find("Child1").GetComponent<Rigidbody>();
                Assert.AreEqual(99f, childRb.mass, 0.01f);
#if UNITY_6000_0_OR_NEWER
                Assert.AreEqual(2.5f, childRb.linearDamping, 0.01f);
#else
                Assert.AreEqual(2.5f, childRb.drag, 0.01f);
#endif
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ComponentProperties_ReturnsErrorForMissingComponent()
        {
            string prefabPath = CreateTestPrefab("CompPropMissing");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentProperties"] = new JObject { ["Rigidbody"] = new JObject { ["mass"] = 5f } },
                        }
                    )
                );

                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsTrue(result.Value<string>("error").Contains("not found"), $"Expected 'not found' error but got: {result.Value<string>("error")}");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ModifyContents_ComponentProperties_ReturnsErrorForInvalidType()
        {
            string prefabPath = CreateTestPrefab("CompPropInvalidType");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["componentProperties"] = new JObject { ["NonexistentComponent"] = new JObject { ["foo"] = "bar" } },
                        }
                    )
                );

                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsTrue(result.Value<string>("error").Contains("not found"), $"Expected 'not found' error but got: {result.Value<string>("error")}");
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        // Note: root rename is NOT tested here because LoadAssetAtPath<GameObject> returns
        // the asset filename as .name for prefab roots, so rename assertions always fail.
        [Test]
        public void ModifyContents_ComponentProperties_CombinesWithOtherModifications()
        {
            string prefabPath = CreatePrefabWithComponents("CompPropCombined", typeof(Rigidbody));

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["position"] = new JArray(5f, 10f, 15f),
                            ["componentProperties"] = new JObject { ["Rigidbody"] = new JObject { ["mass"] = 25f } },
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"), $"Expected success but got: {result}");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.AreEqual(new Vector3(5f, 10f, 15f), reloaded.transform.localPosition);
                Assert.AreEqual(25f, reloaded.GetComponent<Rigidbody>().mass, 0.01f);
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        #endregion

        #region Error Handling

        [Test]
        public void HandleCommand_ValidatesParameters()
        {
            // Null params
            var nullResult = ToJObject(ManagePrefabs.HandleCommand(null));
            Assert.IsFalse(nullResult.Value<bool>("success"));
            Assert.IsTrue(nullResult.Value<string>("error").Contains("null"));

            // Missing action
            var missingAction = ToJObject(ManagePrefabs.HandleCommand(new JObject()));
            Assert.IsFalse(missingAction.Value<bool>("success"));
            Assert.IsTrue(missingAction.Value<string>("error").Contains("Action parameter is required"));

            // Unknown action
            var unknownAction = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "invalid" }));
            Assert.IsFalse(unknownAction.Value<bool>("success"));
            Assert.IsTrue(unknownAction.Value<string>("error").Contains("Unknown action"));

            // Path traversal
            GameObject testObj = new GameObject("Test");
            var traversal = ToJObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_from_gameobject",
                        ["target"] = "Test",
                        ["prefabPath"] = "../../etc/passwd",
                    }
                )
            );
            Assert.IsFalse(traversal.Value<bool>("success"));
            Assert.IsTrue(traversal.Value<string>("error").Contains("path traversal") || traversal.Value<string>("error").Contains("Invalid"));
            UnityEngine.Object.DestroyImmediate(testObj, true);
        }

        [TestCase("Assets/../Outside.prefab")]
        [TestCase("/Outside.prefab")]
        [TestCase("Packages/Outside.prefab")]
        [TestCase("Assets/Invalid?.prefab")]
        public void CreateFromGameObject_RejectsUnsafePathsBeforeUnlinking(string outputPath)
        {
            string sourcePath = CreateTestPrefab("ValidationSource");
            byte[] savedSource = File.ReadAllBytes(sourcePath);
            var source = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(sourcePath));
            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = source.GetInstanceIDCompat(),
                            ["prefabPath"] = outputPath,
                            ["unlinkIfInstance"] = true,
                        }
                    )
                );
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsNotEmpty(result.Value<string>("error"));
                Assert.AreEqual(PrefabInstanceStatus.Connected, PrefabUtility.GetPrefabInstanceStatus(source));
                Assert.AreEqual(sourcePath, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
                CollectionAssert.AreEqual(savedSource, File.ReadAllBytes(sourcePath));
                LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void ModifyContents_ReturnsErrorsForInvalidInputs()
        {
            string prefabPath = CreateTestPrefab("ErrorTest");

            try
            {
                // Invalid target
                var invalidTarget = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = prefabPath,
                            ["target"] = "NonexistentChild",
                        }
                    )
                );
                Assert.IsFalse(invalidTarget.Value<bool>("success"));
                Assert.IsTrue(invalidTarget.Value<string>("error").Contains("not found"));

                // Invalid path
                LogAssert.Expect(LogType.Error, new Regex(".*modify_contents.*does not exist.*"));
                var invalidPath = ToJObject(
                    ManagePrefabs.HandleCommand(new JObject { ["action"] = "modify_contents", ["prefabPath"] = "Assets/Nonexistent.prefab" })
                );
                Assert.IsFalse(invalidPath.Value<bool>("success"));
            }
            finally
            {
                SafeDeleteAsset(prefabPath);
            }
        }

        #endregion

        #region Test Helpers

        private static string CreateTestPrefab(string name)
        {
            EnsureFolder(TempDirectory);
            GameObject temp = GameObject.CreatePrimitive(PrimitiveType.Cube);
            temp.name = name;

            string path = Path.Combine(TempDirectory, name + ".prefab").Replace('\\', '/');
            PrefabUtility.SaveAsPrefabAsset(temp, path, out bool success);
            UnityEngine.Object.DestroyImmediate(temp);
            AssetDatabase.Refresh();

            if (!success)
                throw new Exception($"Failed to create test prefab at {path}");
            return path;
        }

        private static string CreateNestedTestPrefab(string name)
        {
            EnsureFolder(TempDirectory);
            GameObject root = new GameObject(name);
            GameObject child1 = new GameObject("Child1") { transform = { parent = root.transform } };
            GameObject child2 = new GameObject("Child2") { transform = { parent = root.transform } };
            GameObject grandchild = new GameObject("Grandchild") { transform = { parent = child1.transform } };

            string path = Path.Combine(TempDirectory, name + ".prefab").Replace('\\', '/');
            PrefabUtility.SaveAsPrefabAsset(root, path, out bool success);
            UnityEngine.Object.DestroyImmediate(root);
            AssetDatabase.Refresh();

            if (!success)
                throw new Exception($"Failed to create nested test prefab at {path}");
            return path;
        }

        private static string CreatePrefabWithComponents(string name, params Type[] componentTypes)
        {
            EnsureFolder(TempDirectory);
            GameObject temp = new GameObject(name);
            foreach (var t in componentTypes)
            {
                temp.AddComponent(t);
            }

            string path = Path.Combine(TempDirectory, name + ".prefab").Replace('\\', '/');
            PrefabUtility.SaveAsPrefabAsset(temp, path, out bool success);
            UnityEngine.Object.DestroyImmediate(temp);
            AssetDatabase.Refresh();

            if (!success)
                throw new Exception($"Failed to create test prefab at {path}");
            return path;
        }

        private static string CreateComplexTestPrefab(string name)
        {
            // Creates: Vehicle (root with BoxCollider)
            //   - FrontWheel (Cube with MeshRenderer, BoxCollider)
            //   - BackWheel (Cube with MeshRenderer, BoxCollider)
            //   - Turret (empty)
            //       - Barrel (Cylinder with MeshRenderer, CapsuleCollider)
            EnsureFolder(TempDirectory);

            GameObject root = new GameObject(name);
            root.AddComponent<BoxCollider>();

            GameObject frontWheel = GameObject.CreatePrimitive(PrimitiveType.Cube);
            frontWheel.name = "FrontWheel";
            frontWheel.transform.parent = root.transform;
            frontWheel.transform.localPosition = new Vector3(0, 0.5f, 1f);

            GameObject backWheel = GameObject.CreatePrimitive(PrimitiveType.Cube);
            backWheel.name = "BackWheel";
            backWheel.transform.parent = root.transform;
            backWheel.transform.localPosition = new Vector3(0, 0.5f, -1f);

            GameObject turret = new GameObject("Turret");
            turret.transform.parent = root.transform;
            turret.transform.localPosition = new Vector3(0, 1f, 0);

            GameObject barrel = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            barrel.name = "Barrel";
            barrel.transform.parent = turret.transform;
            barrel.transform.localPosition = new Vector3(0, 0, 1f);

            string path = Path.Combine(TempDirectory, name + ".prefab").Replace('\\', '/');
            PrefabUtility.SaveAsPrefabAsset(root, path, out bool success);
            UnityEngine.Object.DestroyImmediate(root);
            AssetDatabase.Refresh();

            if (!success)
                throw new Exception($"Failed to create complex test prefab at {path}");
            return path;
        }

        #endregion
    }
}
