using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class GameObjectFallbackLookupTests
    {
        private Scene originalScene;
        private Scene ownedScene;
        private GameObject root;
        private GameObject first;
        private GameObject second;

        [SetUp]
        public void SetUp()
        {
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "This fixture requires normal scene mode.");
            originalScene = SceneManager.GetActiveScene();
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(ownedScene);
            root = new GameObject("FallbackLookup_" + Guid.NewGuid().ToString("N"));
            first = new GameObject("Matched");
            first.transform.SetParent(root.transform);
            second = new GameObject(first.name);
            second.transform.SetParent(root.transform);
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null)
                UnityEngine.Object.DestroyImmediate(root);
            if (originalScene.IsValid() && originalScene.isLoaded)
                SceneManager.SetActiveScene(originalScene);
            if (ownedScene.IsValid() && ownedScene.isLoaded)
                EditorSceneManager.CloseScene(ownedScene, true);
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void ManualPath_PreservesFirstAllAndInactiveOrder(bool findAll, bool inactiveFirst)
        {
            first.SetActive(!inactiveFirst);
            var results = ManageGameObjectCommon.FindObjectsInternal(first.name, "by_path", findAll, new JObject { ["searchInactive"] = true });
            var expected = findAll ? new[] { first, second } : new[] { first };
            CollectionAssert.AreEqual(expected.Select(go => go.GetInstanceIDCompat()), results.Select(go => go.GetInstanceIDCompat()));
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void NameFallback_PreservesFirstAllAndInactiveOrder(bool findAll, bool inactiveFirst)
        {
            first.name = second.name = "/Matched";
            first.SetActive(!inactiveFirst);
            Assert.IsFalse(GameObjectLookup.MatchesPath(first, first.name), "The leading slash must select the name fallback.");
            var results = ManageGameObjectCommon.FindObjectsInternal(first.name, "by_id_or_name_or_path", findAll);
            var expected = findAll ? new[] { first, second } : new[] { first };
            CollectionAssert.AreEqual(expected.Select(go => go.GetInstanceIDCompat()), results.Select(go => go.GetInstanceIDCompat()));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MixedSelector_PreservesIdPriority(bool integerToken)
        {
            int id = first.GetInstanceIDCompat();
            second.name = id.ToString();
            JToken token = integerToken ? new JValue(id) : new JValue(second.name);
            var results = ManageGameObjectCommon.FindObjectsInternal(token, "by_id_or_name_or_path", true);
            CollectionAssert.AreEqual(new[] { id }, results.Select(go => go.GetInstanceIDCompat()));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MixedSelector_PreservesFirstPathMatch(bool findAll)
        {
            var results = ManageGameObjectCommon.FindObjectsInternal(first.name, "by_id_or_name_or_path", findAll);
            CollectionAssert.AreEqual(new[] { first.GetInstanceIDCompat() }, results.Select(go => go.GetInstanceIDCompat()));
        }
    }
}
