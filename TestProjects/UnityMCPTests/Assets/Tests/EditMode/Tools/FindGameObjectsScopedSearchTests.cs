using System;
using System.Linq;
using MCPForUnity.Editor.Tools;
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
    public class FindGameObjectsScopedSearchTests
    {
        private string _assetRoot;
        private Scene _originalScene;
        private Scene _firstScene;
        private Scene _secondScene;

        [SetUp]
        public void SetUp()
        {
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "These tests require ordinary scene editing.");
            _originalScene = SceneManager.GetActiveScene();
            _assetRoot = "Assets/__McpScopedSearch_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", _assetRoot.Substring(7));
            _firstScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(_firstScene, _assetRoot + "/First.unity"));
            _secondScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(_secondScene, _assetRoot + "/Second.unity"));
            SceneManager.SetActiveScene(_firstScene);
        }

        [TearDown]
        public void TearDown()
        {
            if (_originalScene.IsValid() && _originalScene.isLoaded)
                SceneManager.SetActiveScene(_originalScene);
            if (_secondScene.IsValid() && _secondScene.isLoaded)
                EditorSceneManager.CloseScene(_secondScene, true);
            if (_firstScene.IsValid() && _firstScene.isLoaded)
                EditorSceneManager.CloseScene(_firstScene, true);
            if (!string.IsNullOrEmpty(_assetRoot))
                AssetDatabase.DeleteAsset(_assetRoot);
        }

        [Test]
        public void AllLoaded_NameSearchIncludesBothScenesAndInactiveMatches()
        {
            string name = "Scoped_" + Guid.NewGuid().ToString("N");
            GameObject first = Create(name, _firstScene);
            GameObject second = Create(name, _secondScene);
            second.SetActive(false);
            var query = Query("all_loaded", "by_name", name);
            query["include_inactive"] = true;

            int[] actual = ReadAll(query);

            CollectionAssert.AreEquivalent(new[] { first.GetInstanceIDCompat(), second.GetInstanceIDCompat() }, actual);
        }

        [Test]
        public void Active_TagSearchCannotLeakMatchesFromOtherLoadedScene()
        {
            GameObject first = Create("First", _firstScene);
            GameObject second = Create("Second", _secondScene);
            first.tag = "Player";
            second.tag = "Player";

            int[] actual = ReadAll(Query("active", "by_tag", "Player"));

            CollectionAssert.AreEqual(new[] { first.GetInstanceIDCompat() }, actual);
        }

        [Test]
        public void CompoundSearchRequiresEveryPredicate()
        {
            string name = "Compound_" + Guid.NewGuid().ToString("N");
            GameObject matching = Create(name, _firstScene);
            matching.AddComponent<BoxCollider>();
            Create(name, _firstScene);
            var query = Query("active", "by_name", name);
            query["conditions"] = new JArray(new JObject { ["search_method"] = "by_component", ["search_term"] = "UnityEngine.BoxCollider" });

            int[] actual = ReadAll(query);

            CollectionAssert.AreEqual(new[] { matching.GetInstanceIDCompat() }, actual);
        }

        [Test]
        public void SparseSearchReturnsEmptyContinuationBeforeLateMatch()
        {
            GameObject root = Create("Wide", _firstScene);
            for (int i = 0; i < 4200; i++)
                new GameObject("Other").transform.SetParent(root.transform);
            string name = "Late_" + Guid.NewGuid().ToString("N");
            GameObject last = new GameObject(name);
            last.transform.SetParent(root.transform);
            var query = Query("active", "by_name", name);

            JObject page = Search(query);

            Assert.IsEmpty((JArray)page["instanceIDs"], "A sparse search must stop at its traversal budget before reaching the late match.");
            Assert.IsTrue(page.Value<bool>("hasMore"));
            Assert.IsFalse(page.Value<bool>("totalKnown"));
            Assert.AreEqual(JTokenType.Null, page["totalCount"].Type);
            Assert.AreEqual(32, page.Value<string>("nextCursor").Length);
            query["cursor"] = page["nextCursor"];
            CollectionAssert.AreEqual(new[] { last.GetInstanceIDCompat() }, ReadAll(query));
        }

        [TestCase("by_name", false)]
        [TestCase("by_name", true)]
        [TestCase("by_tag", false)]
        [TestCase("by_tag", true)]
        [TestCase("by_layer", false)]
        [TestCase("by_layer", true)]
        [TestCase("by_component", false)]
        [TestCase("by_component", true)]
        [TestCase("by_path", false)]
        [TestCase("by_path", true)]
        [TestCase("by_id", false)]
        [TestCase("by_id", true)]
        public void EveryMethodUsesTheSameSceneAndInactiveFilter(string method, bool inactive)
        {
            string name = "Matrix_" + Guid.NewGuid().ToString("N");
            GameObject first = Create(name, _firstScene);
            GameObject second = Create(name, _secondScene);
            first.tag = second.tag = "Player";
            first.layer = second.layer = 2;
            first.AddComponent<BoxCollider>().enabled = false;
            second.AddComponent<BoxCollider>().enabled = false;
            second.SetActive(false);
            string term =
                method == "by_tag" ? "Player"
                : method == "by_layer" ? "Ignore Raycast"
                : method == "by_component" ? "BoxCollider"
                : method == "by_id" ? second.GetInstanceIDCompat().ToString()
                : name;
            var query = Query("active", method, term);
            query["include_inactive"] = inactive;
            int[] expected = method == "by_id" ? Array.Empty<int>() : new[] { first.GetInstanceIDCompat() };
            CollectionAssert.AreEquivalent(expected, ReadAll(query));
            query["scene_scope"] = "all_loaded";
            expected =
                method == "by_id" ? (inactive ? new[] { second.GetInstanceIDCompat() } : Array.Empty<int>())
                : inactive ? new[] { first.GetInstanceIDCompat(), second.GetInstanceIDCompat() }
                : new[] { first.GetInstanceIDCompat() };
            CollectionAssert.AreEquivalent(expected, ReadAll(query));
        }

        [Test]
        public void InactiveParentPrunesChildrenUnlessIncluded()
        {
            GameObject parent = Create("Parent", _secondScene);
            GameObject child = Create("HiddenChild", _secondScene);
            child.transform.SetParent(parent.transform);
            parent.SetActive(false);
            var query = Query("all_loaded", "by_id", child.GetInstanceIDCompat().ToString());
            Assert.IsEmpty(ReadAll(query));
            query["include_inactive"] = true;
            CollectionAssert.AreEqual(new[] { child.GetInstanceIDCompat() }, ReadAll(query));
        }

        [TestCase("/Parent/Branch/Leaf", true)]
        [TestCase("Parent/Branch/Leaf", true)]
        [TestCase("Branch/Leaf", true)]
        [TestCase("Leaf", true)]
        [TestCase("/Branch/Leaf", false)]
        [TestCase("anch/Leaf", false)]
        public void PathsPreserveAnchoredAndSegmentSuffixSemantics(string term, bool match)
        {
            GameObject parent = Create("Parent", _firstScene);
            GameObject leaf = Create("Branch/Leaf", _firstScene);
            leaf.transform.SetParent(parent.transform);
            int[] actual = ReadAll(Query("active", "by_path", term));
            CollectionAssert.AreEqual(match ? new[] { leaf.GetInstanceIDCompat() } : Array.Empty<int>(), actual);
        }

        [Test]
        public void ConditionsOnlyRequireMultipleComponentsAndDoNotModifyInput()
        {
            GameObject matching = Create("Both", _firstScene);
            matching.AddComponent<BoxCollider>();
            matching.AddComponent<Rigidbody>();
            Create("One", _firstScene).AddComponent<BoxCollider>();
            var query = new JObject
            {
                ["scene_scope"] = "active",
                ["conditions"] = new JArray(
                    new JObject { ["search_method"] = "by_component", ["search_term"] = "BoxCollider" },
                    new JObject { ["search_method"] = "by_component", ["search_term"] = "Rigidbody" }
                ),
            };
            var before = query.DeepClone();
            CollectionAssert.AreEqual(new[] { matching.GetInstanceIDCompat() }, ReadAll(query));
            Assert.IsTrue(JToken.DeepEquals(before, query));
        }

        [Test]
        public void StablePagesAndReplayAreExactWithUnknownIntermediateTotals()
        {
            string name = "Stable_" + Guid.NewGuid().ToString("N");
            GameObject root = Create(name, _firstScene);
            var expected = new System.Collections.Generic.List<int> { root.GetInstanceIDCompat() };
            for (int i = 0; i < 11; i++)
            {
                GameObject child = Create(name, _firstScene);
                child.transform.SetParent(root.transform);
                expected.Add(child.GetInstanceIDCompat());
            }
            var query = Query("active", "by_name", name);
            query["page_size"] = 2;
            JObject first = Search(query);
            Assert.IsFalse(first.Value<bool>("totalKnown"));
            Assert.AreEqual(JTokenType.Null, first["totalCount"].Type);
            Assert.AreEqual(2, first.Value<int>("matchedSoFar"));
            query["cursor"] = first["nextCursor"];
            JObject page = Search(query);
            JObject replay = Search(query);
            CollectionAssert.AreEqual(((JArray)page["instanceIDs"]).Values<int>(), ((JArray)replay["instanceIDs"]).Values<int>());
            Assert.AreEqual(page["matchedSoFar"], replay["matchedSoFar"]);
            var actual = ((JArray)first["instanceIDs"]).Values<int>().Concat(ReadAll(query)).ToArray();
            CollectionAssert.AreEqual(expected, actual);
        }

        [Test]
        public void DeepTraversalResumesAndReportsDeclaredDepthExclusion()
        {
            GameObject root = Create("Deep", _firstScene);
            GameObject current = root;
            var expected = new System.Collections.Generic.List<int> { root.GetInstanceIDCompat() };
            for (int depth = 1; depth <= 129; depth++)
            {
                GameObject child = Create("Deep", _firstScene);
                child.transform.SetParent(current.transform);
                current = child;
                if (depth <= 128)
                    expected.Add(child.GetInstanceIDCompat());
            }
            GameObject sibling = Create("Deep", _firstScene);
            sibling.transform.SetParent(root.transform);
            expected.Add(sibling.GetInstanceIDCompat());
            var query = Query("active", "by_name", "Deep");
            query["max_depth"] = 128;
            query["page_size"] = 7;
            CollectionAssert.AreEqual(expected, ReadAll(query));
            query["page_size"] = 500;
            JObject page;
            do
            {
                page = Search(query);
                query["cursor"] = page["nextCursor"];
            } while (page.Value<bool>("hasMore"));
            Assert.IsTrue(page.Value<bool>("depthLimited"));
            Assert.AreEqual(expected.Count, page.Value<int>("totalCount"));
            Assert.AreEqual(128, page.Value<int>("maxDepth"));
        }

        [Test]
        public void NoMatchWideTraversalEventuallyCompletesWithZeroTotal()
        {
            GameObject root = Create("Wide", _firstScene);
            for (int i = 0; i < 4200; i++)
                new GameObject("Other").transform.SetParent(root.transform);
            var query = Query("active", "by_name", "Missing");
            JObject page = Search(query);
            Assert.IsEmpty((JArray)page["instanceIDs"]);
            Assert.IsTrue(page.Value<bool>("hasMore"));
            query["cursor"] = page["nextCursor"];
            Assert.IsEmpty(ReadAll(query));
        }

        [TestCase(4095, true)]
        [TestCase(4096, true)]
        [TestCase(4097, false)]
        public void RootAdmissionIsDistinctFromTraversalBudget(int roots, bool accepted)
        {
            for (int i = 0; i < roots; i++)
                Create("Root", _firstScene);
            var query = Query("active", "by_name", "Missing");
            var observer = SearchHelper.GetField(
                "BeforeRootEnumerationForTests",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic
            );
            object previous = observer.GetValue(null);
            int calls = 0;
            JObject response;
            try
            {
                observer.SetValue(null, new Action(() => calls++));
                response = JObject.FromObject(FindGameObjects.HandleCommand(query));
            }
            finally
            {
                observer.SetValue(null, previous);
            }
            Assert.AreEqual(accepted ? 1 : 0, calls, "The observer surrounds the real native root call; refusal must happen before it.");
            Assert.AreEqual(accepted, response.Value<bool>("success"), response.ToString());
            if (accepted)
            {
                Assert.AreEqual(roots, response["data"].Value<int>("rootPreparationCount"));
                Assert.AreEqual(1, response["data"].Value<int>("rootEnumerationCalls"));
                Assert.LessOrEqual(response["data"].Value<int>("traversalWorkUsed"), 4096);
            }
            else
                Assert.AreEqual("source_limit_exceeded", response.Value<string>("code"));
        }

        [Test]
        public void LoadedSceneAdmissionRejectsTooManyManagerEntries()
        {
            var extra = new System.Collections.Generic.List<Scene>();
            try
            {
                while (SceneManager.sceneCount <= 32)
                {
                    Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    extra.Add(scene);
                    Assert.IsTrue(EditorSceneManager.SaveScene(scene, _assetRoot + "/Extra" + extra.Count + ".unity"));
                }
                JObject response = JObject.FromObject(FindGameObjects.HandleCommand(Query("all_loaded", "by_name", "Missing")));
                Assert.IsFalse(response.Value<bool>("success"));
                Assert.AreEqual("source_limit_exceeded", response.Value<string>("code"));
            }
            finally
            {
                foreach (Scene scene in extra)
                    if (scene.IsValid() && scene.isLoaded)
                        EditorSceneManager.CloseScene(scene, true);
            }
        }

        [Test]
        public void AllLoadedReportsSelectedScenesAndAllowsEmptySceneBoundaryPage()
        {
            GameObject target = Create("OnlySecond", _secondScene);
            var query = Query("all_loaded", "by_id", target.GetInstanceIDCompat().ToString());
            JObject first = Search(query);
            CollectionAssert.Contains(((JArray)first["sceneHandles"]).Values<int>(), _firstScene.handle);
            CollectionAssert.Contains(((JArray)first["sceneHandles"]).Values<int>(), _secondScene.handle);
            Assert.IsTrue(first.Value<bool>("hasMore"));
            Assert.AreEqual("root_enumeration_budget", first.Value<string>("stopReason"));
            CollectionAssert.AreEqual(new[] { target.GetInstanceIDCompat() }, ReadAll(query));
        }

        [Test]
        public void PreviewScenesAreExcludedWithoutSubstitutingActiveScene()
        {
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                GameObject hidden = Create("PreviewOnly", preview);
                Assert.IsEmpty(ReadAll(Query("all_loaded", "by_id", hidden.GetInstanceIDCompat().ToString())));
                GameObject ordinary = Create("Ordinary", _firstScene);
                CollectionAssert.AreEqual(new[] { ordinary.GetInstanceIDCompat() }, ReadAll(Query("active", "by_name", "Ordinary")));
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        [TestCase("page_size", "0")]
        [TestCase("page_size", "501")]
        [TestCase("page_size", "true")]
        [TestCase("page_size", "1.5")]
        [TestCase("page_size", "\"2\"")]
        [TestCase("max_depth", "-1")]
        [TestCase("max_depth", "129")]
        [TestCase("cursor", "0")]
        [TestCase("cursor", "\"gggggggggggggggggggggggggggggggg\"")]
        [TestCase("page_number", "1")]
        [TestCase("conditions", "{}")]
        [TestCase("conditions", "[{\"search_method\":\"by_name\",\"search_term\":\"x\",\"extra\":true}]")]
        [TestCase("conditions", "[{\"search_method\":\"unknown\",\"search_term\":\"x\"}]")]
        public void ScopedMalformedParametersFail(string key, string json)
        {
            var query = Query("active", "by_name", "Missing");
            query[key] = JToken.Parse(json);
            JObject response = JObject.FromObject(FindGameObjects.HandleCommand(query));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
        }

        [Test]
        public void QueryLimitsRejectExcessPredicatesAndUtf16UnitsWithoutTruncation()
        {
            var query = Query("active", "by_name", new string('x', 513));
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
            query["search_term"] = string.Concat(Enumerable.Repeat("\ud83d\ude00", 257));
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
            query["search_term"] = "Valid";
            query["conditions"] = new JArray(Enumerable.Range(0, 8).Select(_ => new JObject { ["search_method"] = "by_name", ["search_term"] = "Valid" }));
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
        }

        [Test]
        public void AliasesAgreeOrRejectAndLegacyCannotUseScopedOptions()
        {
            var query = Query("active", "by_name", "Missing");
            query["sceneScope"] = "all_loaded";
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
            query["sceneScope"] = "active";
            Search(query);
            query["pageSize"] = 3;
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
            query = Query("legacy", "by_name", "Missing");
            query["max_depth"] = 1;
            Assert.IsFalse(JObject.FromObject(FindGameObjects.HandleCommand(query)).Value<bool>("success"));
        }

        [TestCase("page_size", "3")]
        [TestCase("max_depth", "2")]
        [TestCase("include_inactive", "true")]
        [TestCase("search_term", "\"Other\"")]
        [TestCase("scene_scope", "\"all_loaded\"")]
        public void CursorBindsEveryQueryOption(string key, string json)
        {
            JObject query = ContinuationQuery();
            query[key] = JToken.Parse(json);
            AssertStale(query);
        }

        [Test]
        public void ImmediateFrontierMutationAndActiveSceneChangeInvalidateCursor()
        {
            JObject query = ContinuationQuery();
            GameObject changed = Create("NewChild", _firstScene);
            AssertStale(query);
            UnityEngine.Object.DestroyImmediate(changed);
            query = ContinuationQuery();
            SceneManager.SetActiveScene(_secondScene);
            AssertStale(query);
        }

        [Test]
        public void CancellationDoesNotConsumeTheInputCursor()
        {
            JObject query = ContinuationQuery();
            using (var cancellation = new System.Threading.CancellationTokenSource())
            {
                cancellation.Cancel();
                Assert.Throws<OperationCanceledException>(() => FindGameObjects.HandleCommand(query, cancellation.Token).GetAwaiter().GetResult());
            }
            JObject page = Search(query);
            Assert.IsNotEmpty((JArray)page["instanceIDs"]);
        }

        [Test]
        public void ExpiredAndEvictedCursorsFailExplicitly()
        {
            JObject query = ContinuationQuery();
            Type helper = typeof(FindGameObjects).Assembly.GetType("MCPForUnity.Editor.Helpers.GameObjectSearch", true);
            var cache = (System.Collections.IDictionary)
                helper.GetField("States", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).GetValue(null);
            object state = cache[query.Value<string>("cursor")];
            state.GetType().GetField("Expires", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).SetValue(state, -1d);
            AssertStale(query);
            query = ContinuationQuery();
            JObject fresh = (JObject)query.DeepClone();
            fresh.Remove("cursor");
            for (int i = 0; i < 33; i++)
                Search(fresh);
            AssertStale(query);
        }

        [Test]
        public void ObservedEditorChangeInvalidatesCursor()
        {
            JObject query = ContinuationQuery();
            Type helper = typeof(FindGameObjects).Assembly.GetType("MCPForUnity.Editor.Helpers.GameObjectSearch", true);
            helper.GetMethod("Changed", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).Invoke(null, null);
            AssertStale(query);
        }

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator RealEditorHierarchyEventInvalidatesCursor()
        {
            GameObject root = Create("Event", _firstScene);
            for (int i = 0; i < 3; i++)
                new GameObject("Event").transform.SetParent(root.transform);
            // Let fixture creation notifications settle before the first page.
            yield return null;
            var query = Query("active", "by_name", "Event");
            query["page_size"] = 1;
            query["cursor"] = Search(query)["nextCursor"];
            Undo.RecordObject(root, "Scoped search event fixture");
            root.name = "Changed";
            EditorUtility.SetDirty(root);
            EditorApplication.QueuePlayerLoopUpdate();
            yield return null;
            AssertStale(query);
            Undo.ClearUndo(root);
        }

        [Test]
        public void MaximumPageSizeBoundsOutputAndKeepsContinuationComplete()
        {
            GameObject root = Create("Output", _firstScene);
            for (int i = 0; i < 700; i++)
                new GameObject("Output").transform.SetParent(root.transform);
            var query = Query("active", "by_name", "Output");
            query["page_size"] = 500;
            JObject page = Search(query);
            Assert.AreEqual(500, ((JArray)page["instanceIDs"]).Count);
            Assert.AreEqual("page_size", page.Value<string>("stopReason"));
            Assert.AreEqual(701, ReadAll(query).Distinct().Count());
        }

        [TestCase("by_tag", "player")]
        [TestCase("by_tag", "__McpUnknownTag_7ca031")]
        [TestCase("by_layer", "__McpUnknownLayer_7ca031")]
        [TestCase("by_layer", "32")]
        [TestCase("by_component", "__McpUnknownComponent_7ca031")]
        [TestCase("by_id", "not-an-id")]
        public void UnknownPredicateValuesMatchNothing(string method, string term)
        {
            Create("Existing", _firstScene);
            Assert.IsEmpty(ReadAll(Query("active", method, term)));
        }

        [Test]
        public void DepthZeroIncludesOnlyRootsAndLongNamesAreNotTruncated()
        {
            GameObject root = Create(new string('x', 513), _firstScene);
            GameObject child = Create("Child", _firstScene);
            child.transform.SetParent(root.transform);
            Assert.IsEmpty(ReadAll(Query("active", "by_name", new string('x', 512))));
            var query = Query("active", "by_component", "Transform");
            query["max_depth"] = 0;
            JObject page = Search(query);
            CollectionAssert.AreEqual(new[] { root.GetInstanceIDCompat() }, ((JArray)page["instanceIDs"]).Values<int>());
            Assert.IsTrue(page.Value<bool>("depthLimited"));
            Assert.AreEqual(1, page.Value<int>("totalCount"));
        }

        [Test]
        public void EightPredicatesAnd512Utf16UnitsAreAccepted()
        {
            string name = string.Concat(Enumerable.Repeat("\ud83d\ude00", 256));
            GameObject target = Create(name, _firstScene);
            var query = Query("active", "by_name", name);
            query["conditions"] = new JArray(
                Enumerable.Range(0, 7).Select(_ => new JObject { ["search_method"] = "by_component", ["search_term"] = "Transform" })
            );
            CollectionAssert.AreEqual(new[] { target.GetInstanceIDCompat() }, ReadAll(query));
        }

        [Test]
        public void ContinuationDetectsChangedFrontierWithoutRootCountChange()
        {
            JObject query = ContinuationQuery();
            string cursor = query.Value<string>("cursor");
            Type helper = typeof(FindGameObjects).Assembly.GetType("MCPForUnity.Editor.Helpers.GameObjectSearch", true);
            var cache = (System.Collections.IDictionary)
                helper.GetField("States", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).GetValue(null);
            object state = cache[cursor];
            var observations = (Array)
                state.GetType().GetField("Observations", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).GetValue(state);
            object last = observations.GetValue(observations.Length - 1);
            int id = (int)last.GetType().GetField("Id", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).GetValue(last);
            GameObject go = UnityEditor.EditorUtility.InstanceIDToObject(id) as GameObject;
            Assert.IsNotNull(go);
            go.name = "Changed frontier";
            EditorUtility.SetDirty(go);
            AssertStale(query);
        }

        [Test]
        public void CancellationDuringRealTraversalLeavesOriginalCursorReusable()
        {
            GameObject root = Create("Cancellation", _firstScene);
            for (int i = 0; i < 200; i++)
                new GameObject("Cancellation").transform.SetParent(root.transform);
            var query = Query("active", "by_name", "Cancellation");
            query["cursor"] = Search(query)["nextCursor"];
            JObject expected = Search(query);
            var observer = SearchHelper.GetField("TraversalStepForTests", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            object previous = observer.GetValue(null);
            int observed = 0;
            using (var cancellation = new System.Threading.CancellationTokenSource())
            {
                try
                {
                    observer.SetValue(
                        null,
                        new Action<int>(step =>
                        {
                            observed = step;
                            if (step == 30)
                                cancellation.Cancel();
                        })
                    );
                    Assert.Throws<OperationCanceledException>(() => FindGameObjects.HandleCommand(query, cancellation.Token).GetAwaiter().GetResult());
                }
                finally
                {
                    observer.SetValue(null, previous);
                }
            }
            Assert.AreEqual(30, observed, "Cancellation must occur inside the native iterator, after resumption and candidate work.");
            JObject actual = Search(query);
            Assert.IsTrue(JToken.DeepEquals(expected["instanceIDs"], actual["instanceIDs"]));
            Assert.AreEqual(expected["matchedSoFar"], actual["matchedSoFar"]);
        }

        [Test]
        public void RootEnumerationObserverConfirmsOneRealCallAcrossSceneBoundaries()
        {
            GameObject target = Create("Target", _secondScene);
            var query = Query("all_loaded", "by_id", target.GetInstanceIDCompat().ToString());
            var observer = SearchHelper.GetField(
                "BeforeRootEnumerationForTests",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic
            );
            object previous = observer.GetValue(null);
            int calls = 0;
            try
            {
                observer.SetValue(null, new Action(() => calls++));
                bool complete = false;
                for (int pages = 0; pages < 32 && !complete; pages++)
                {
                    calls = 0;
                    JObject page = Search(query);
                    Assert.LessOrEqual(calls, 1);
                    Assert.AreEqual(calls, page.Value<int>("rootEnumerationCalls"));
                    query["cursor"] = page["nextCursor"];
                    complete = !page.Value<bool>("hasMore");
                }
                Assert.IsTrue(complete);
            }
            finally
            {
                observer.SetValue(null, previous);
            }
        }

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator RealMutationNotificationsInvalidateUnvisitedChanges()
        {
            foreach (string change in new[] { "rename", "reparent", "delete", "tag", "layer", "active", "component" })
            {
                GameObject root = Create("Mutation", _firstScene);
                for (int i = 0; i < 5; i++)
                    new GameObject("Mutation").transform.SetParent(root.transform);
                GameObject target = root.transform.GetChild(3).gameObject;
                // Creation notifications are allowed to settle before establishing the cursor revision.
                yield return null;
                var query = Query("active", "by_name", "Mutation");
                query["page_size"] = 1;
                query["cursor"] = Search(query)["nextCursor"];
                var epoch = SearchHelper.GetField("_epoch", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
                int before = (int)epoch.GetValue(null);
                Undo.RecordObject(target, "Scoped search mutation fixture");
                switch (change)
                {
                    case "rename":
                        target.name = "Renamed";
                        break;
                    case "reparent":
                        Undo.SetTransformParent(target.transform, root.transform.GetChild(0), "Scoped reparent");
                        break;
                    case "delete":
                        Undo.DestroyObjectImmediate(target);
                        break;
                    case "tag":
                        target.tag = "Player";
                        break;
                    case "layer":
                        target.layer = 2;
                        break;
                    case "active":
                        target.SetActive(false);
                        break;
                    case "component":
                        Undo.AddComponent<BoxCollider>(target);
                        break;
                }
                if (target != null)
                    EditorUtility.SetDirty(target);
                Undo.FlushUndoRecordObjects();
                EditorApplication.QueuePlayerLoopUpdate();
                for (int frame = 0; frame < 8 && before == (int)epoch.GetValue(null); frame++)
                    yield return null;
                Assert.AreNotEqual(before, (int)epoch.GetValue(null), "Actual Editor notifications were not observed for " + change);
                AssertStale(query);
                if (target != null)
                    Undo.ClearUndo(target);
                Undo.ClearUndo(root);
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void ClosingSelectedSceneInvalidatesContinuation()
        {
            JObject query = ContinuationQuery();
            SceneManager.SetActiveScene(_secondScene);
            EditorSceneManager.CloseScene(_firstScene, true);
            AssertStale(query);
        }

        private static Type SearchHelper => typeof(FindGameObjects).Assembly.GetType("MCPForUnity.Editor.Helpers.GameObjectSearch", true);

        [TestCase("Player")]
        [TestCase("Untagged")]
        public void ScopedTagMatchingAgreesWithNativeForDeclaredTags(string term)
        {
            GameObject player = Create("Tagged", _firstScene);
            player.tag = "Player";
            GameObject untagged = Create("Untagged", _firstScene);
            var objects = new[] { player, untagged };
            int[] expected = objects.Where(go => go.CompareTag(term)).Select(go => go.GetInstanceIDCompat()).ToArray();
            CollectionAssert.AreEquivalent(expected, ReadAll(Query("active", "by_tag", term)));
        }

        private JObject ContinuationQuery()
        {
            GameObject root = Create("Cursor", _firstScene);
            for (int i = 0; i < 4; i++)
                new GameObject("Cursor").transform.SetParent(root.transform);
            var query = Query("active", "by_name", "Cursor");
            query["page_size"] = 1;
            query["cursor"] = Search(query)["nextCursor"];
            return query;
        }

        private static void AssertStale(JObject query)
        {
            JObject response = JObject.FromObject(FindGameObjects.HandleCommand(query));
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("stale_cursor", response.Value<string>("code"), response.ToString());
        }

        private static GameObject Create(string name, Scene scene)
        {
            var result = new GameObject(name);
            SceneManager.MoveGameObjectToScene(result, scene);
            return result;
        }

        private static JObject Query(string scope, string method, string term)
        {
            return new JObject
            {
                ["scene_scope"] = scope,
                ["search_method"] = method,
                ["search_term"] = term,
                ["page_size"] = 50,
            };
        }

        private static JObject Search(JObject query)
        {
            JObject response = JObject.FromObject(FindGameObjects.HandleCommand(query));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JObject data = (JObject)response["data"];
            Assert.LessOrEqual(data.Value<int>("traversalWorkUsed"), 4096);
            Assert.LessOrEqual(data.Value<int>("rootPreparationCount"), 4096);
            Assert.LessOrEqual(data.Value<int>("rootEnumerationCalls"), 1);
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 16384);
            Assert.AreEqual("stable_source_required", data.Value<string>("consistency"));
            if (!data.Value<bool>("hasMore"))
            {
                Assert.IsTrue(data.Value<bool>("totalKnown"));
                Assert.IsTrue(data.Value<bool>("scanComplete"));
                Assert.AreEqual(data["matchedSoFar"], data["totalCount"]);
            }
            return data;
        }

        private static int[] ReadAll(JObject query)
        {
            var ids = new System.Collections.Generic.List<int>();
            var request = (JObject)query.DeepClone();
            for (int pages = 0; pages < 100; pages++)
            {
                JObject page = Search(request);
                ids.AddRange(((JArray)page["instanceIDs"]).Values<int>());
                if (!page.Value<bool>("hasMore"))
                    return ids.ToArray();
                request["cursor"] = page["nextCursor"];
            }
            Assert.Fail("Search failed to complete within the fixture's page bound.");
            return ids.ToArray();
        }
    }
}
