using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    // Tests only owned hierarchy path matching, never global object-search APIs.
    public class GameObjectPathIntegrityTests
    {
        private readonly List<GameObject> owned = new List<GameObject>();
        private Scene ownedScene;
        private Scene previousScene;
        private UnityEngine.Object[] previousSelection;
        private bool captured;
        private GameObject root;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            owned.Clear();
            ownedScene = default;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned prefab stage is open.");
            previousScene = SceneManager.GetActiveScene();
            previousSelection = Selection.objects;
            captured = true;
            ownedScene = EditorSceneManager.NewPreviewScene();
            root = new GameObject("McpPathIntegrity_" + Guid.NewGuid().ToString("N"));
            owned.Add(root);
            SceneManager.MoveGameObjectToScene(root, ownedScene);
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured)
                return;
            try
            {
                foreach (GameObject obj in owned)
                    if (obj != null)
                        UnityEngine.Object.DestroyImmediate(obj);
                if (ownedScene.IsValid())
                    EditorSceneManager.ClosePreviewScene(ownedScene);
            }
            finally
            {
                if (previousScene.IsValid() && previousScene.isLoaded)
                    SceneManager.SetActiveScene(previousScene);
                Selection.objects = previousSelection;
                captured = false;
            }
        }

        private GameObject Child(string name)
        {
            GameObject obj = new GameObject(name);
            owned.Add(obj);
            SceneManager.MoveGameObjectToScene(obj, ownedScene);
            obj.transform.SetParent(root.transform);
            return obj;
        }

        private static bool Matches(GameObject obj, string path)
        {
            MethodInfo method = typeof(GameObjectLookup).GetMethod("MatchesPath", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            return (bool)method.Invoke(null, new object[] { obj, path });
        }

        [TestCase("en-US")]
        [TestCase("tr-TR")]
        public void SuffixComparisonPreservesLiteralSoftHyphenIdentity(string culture)
        {
            CultureInfo previous = CultureInfo.CurrentCulture;
            try
            {
                CultureInfo.CurrentCulture = new CultureInfo(culture);
                GameObject plain = Child("Child");
                GameObject marked = Child("Ch\u00ADild");
                Assert.IsFalse(Matches(plain, "Ch\u00ADild"));
                Assert.IsTrue(Matches(marked, "Ch\u00ADild"));
                Assert.IsFalse(Matches(marked, "Child"));
            }
            finally
            {
                CultureInfo.CurrentCulture = previous;
            }
        }

        [Test]
        public void RootedPathDoesNotMatchTrailingSegment()
        {
            GameObject child = Child("Child");
            Assert.IsTrue(Matches(child, "/" + root.name + "/Child"));
            Assert.IsFalse(Matches(child, "/Child"));
        }

        [Test]
        public void RelativeSuffixAndFullPathRemainValid()
        {
            GameObject child = Child("Child");
            Assert.IsTrue(Matches(child, "Child"));
            Assert.IsTrue(Matches(child, root.name + "/Child"));
            Assert.IsFalse(Matches(child, "child"));
        }

        [TestCase(null)]
        [TestCase("")]
        public void MissingPathReturnsFalse(string path)
        {
            Assert.IsFalse(Matches(root, path));
            Assert.IsFalse(Matches(null, "Child"));
        }

        [TestCase("")]
        [TestCase("Plain")]
        [TestCase("Nested/Name")]
        [TestCase("/Leading/")]
        [TestCase("한글_😀")]
        [TestCase("Ch\u00ADild")]
        public void HierarchyPathPreservesLiteralNames(string name)
        {
            GameObject child = Child(name);
            Assert.AreEqual(root.name + "/" + name, GameObjectLookup.GetGameObjectPath(child));
            Assert.AreEqual(root.name, GameObjectLookup.GetGameObjectPath(root));
        }

        [Test]
        public void DeepHierarchyPathReflectsRenameAndReparent()
        {
            var names = new List<string> { root.name };
            GameObject leaf = root;
            for (int i = 1; i < 128; i++)
            {
                GameObject child = Child("Node_" + i);
                child.transform.SetParent(leaf.transform, false);
                leaf = child;
                names.Add(child.name);
            }

            Assert.AreEqual(string.Join("/", names), GameObjectLookup.GetGameObjectPath(leaf));
            root.name = "RenamedRoot";
            names[0] = root.name;
            Assert.AreEqual(string.Join("/", names), GameObjectLookup.GetGameObjectPath(leaf));
            leaf.transform.SetParent(root.transform, false);
            Assert.AreEqual(root.name + "/" + leaf.name, GameObjectLookup.GetGameObjectPath(leaf));
        }

        [Test]
        public void MissingOrDestroyedObjectHasEmptyPath()
        {
            GameObject child = Child("Destroyed");
            UnityEngine.Object.DestroyImmediate(child);
            Assert.AreEqual(string.Empty, GameObjectLookup.GetGameObjectPath(child));
            Assert.AreEqual(string.Empty, GameObjectLookup.GetGameObjectPath(null));
        }
    }
}
