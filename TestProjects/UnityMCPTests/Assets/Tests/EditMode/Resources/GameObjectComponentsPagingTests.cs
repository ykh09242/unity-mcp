using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Resources
{
    public class GameObjectComponentsPagingTests
    {
        private GameObject _target;

        [SetUp]
        public void SetUp()
        {
            _target = new GameObject("ComponentPagingTest");
            _target.AddComponent<BoxCollider>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(_target);
        }

        [Test]
        public void NegativeCursor_ReturnsFirstPageWithAdvancingCursor()
        {
            var response = ReadPage(-10);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(1, response["data"]["components"].Count());
            Assert.AreEqual(1, response["data"]["nextCursor"].Value<int>());
        }

        [Test]
        public void CursorBeyondEnd_ReturnsEmptyFinalPage()
        {
            var response = ReadPage(int.MaxValue);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(2, response["data"]["cursor"].Value<int>());
            Assert.IsEmpty(response["data"]["components"]);
            Assert.AreEqual(JTokenType.Null, response["data"]["nextCursor"].Type);
        }

        private static IEnumerable<JToken> LargeWholeNumbers()
        {
            yield return new JValue(2147483648L);
            yield return new JValue(long.MaxValue);
            yield return new JValue(ulong.MaxValue);
            yield return new JValue(BigInteger.Parse("999999999999999999999999999999"));
            yield return new JValue("2147483648");
        }

        [TestCaseSource(nameof(LargeWholeNumbers))]
        public void LargeCursor_ReturnsEmptyFinalPage(JToken cursor)
        {
            var response = ReadPage(cursor);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(2, response["data"]["cursor"].Value<int>());
            Assert.IsEmpty(response["data"]["components"]);
            Assert.AreEqual(JTokenType.Null, response["data"]["nextCursor"].Type);
            Assert.IsFalse(response["data"]["hasMore"].Value<bool>());
        }

        [TestCaseSource(nameof(LargeWholeNumbers))]
        public void LargePageSize_UsesMaximum(JToken pageSize)
        {
            var response = ReadPage(0, pageSize);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(100, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(2, response["data"]["components"].Count());
        }

        [TestCase("-2147483649")]
        [TestCase("-99999999999999999999999")]
        public void LargeNegativePaging_UsesFirstPageAndMinimumSize(string wholeNumber)
        {
            foreach (var token in new[] { JToken.Parse(wholeNumber), new JValue(wholeNumber) })
            {
                var response = ReadPage(token, token);
                Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
                Assert.AreEqual(1, response["data"]["pageSize"].Value<int>());
                Assert.AreEqual(1, response["data"]["nextCursor"].Value<int>());
            }
        }

        [Test]
        public void NullPaging_UsesDefaultCursorAndPageSize()
        {
            var token = JValue.CreateNull();
            var response = ReadPage(token, token);
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(25, response["data"]["pageSize"].Value<int>());
        }

        private static IEnumerable<TestCaseData> InvalidPagingParameters()
        {
            foreach (string json in new[] { "\"bad\"", "{}", "[]", "true", "1.9", "\"1.9\"" })
            foreach (string parameter in new[] { "cursor", "pageSize" })
                yield return new TestCaseData(json, parameter);
        }

        [TestCaseSource(nameof(InvalidPagingParameters))]
        public void ExplicitInvalidPaging_RejectsEachParameter(string json, string parameter)
        {
            var token = JToken.Parse(json);
            var error = Assert.Throws<ArgumentException>(() =>
                ReadPage(parameter == "cursor" ? token : new JValue(0), parameter == "pageSize" ? token : new JValue(1))
            );
            Assert.AreEqual(parameter, error.ParamName);
        }

        [Test]
        public void CamelCasePageSize_TakesPrecedenceOverSnakeCase()
        {
            var response = JObject.FromObject(
                GameObjectComponentsResource.HandleCommand(
                    new JObject
                    {
                        ["instanceID"] = _target.GetInstanceIDCompat(),
                        ["pageSize"] = 1,
                        ["page_size"] = new JValue(2147483648L),
                        ["includeProperties"] = false,
                    }
                )
            );
            Assert.AreEqual(1, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(1, response["data"]["components"].Count());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingSlots_KeepLiveCountOrderAndPageBoundaries(bool includeProperties)
        {
            _target.AddComponent<SphereCollider>();
            string folderName = "PC03MissingSlots_" + Guid.NewGuid().ToString("N");
            string folder = "Assets/" + folderName;
            Assert.IsFalse(string.IsNullOrEmpty(AssetDatabase.CreateFolder("Assets", folderName)));
            try
            {
                string assetPath = folder + "/Missing.prefab";
                string fullPath = Path.Combine(Application.dataPath, folderName, "Missing.prefab");
                Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(_target, assetPath));
                string yaml = File.ReadAllText(fullPath);
                var gameObject = Regex.Match(yaml, @"(?m)^--- !u!1 &(-?\d+)\r?$");
                var componentBlock = Regex.Match(yaml, @"(?m)^  m_Component:\r?\n(?<entries>(?:  - component: \{fileID: -?\d+\}\r?\n)+)");
                Assert.IsTrue(gameObject.Success && componentBlock.Success, "The engine-authored prefab must contain a GameObject component block.");
                var entries = Regex.Matches(componentBlock.Groups["entries"].Value, @"(?m)^  - component: \{fileID: -?\d+\}\r?$");
                Assert.AreEqual(3, entries.Count);
                var patchedEntries = new StringBuilder();
                var missingComponents = new StringBuilder();
                string missingScriptGuid = Guid.NewGuid().ToString("N");
                Assert.IsEmpty(AssetDatabase.GUIDToAssetPath(missingScriptGuid));
                for (int index = 0; index < entries.Count; index++)
                {
                    int missingId = 114000001 + index;
                    patchedEntries.Append(entries[index].Value.TrimEnd('\r')).Append('\n');
                    patchedEntries.Append("  - component: {fileID: ").Append(missingId).Append("}\n");
                    missingComponents
                        .Append("--- !u!114 &")
                        .Append(missingId)
                        .Append("\nMonoBehaviour:\n")
                        .Append("  m_ObjectHideFlags: 0\n")
                        .Append("  m_CorrespondingSourceObject: {fileID: 0}\n")
                        .Append("  m_PrefabInstance: {fileID: 0}\n")
                        .Append("  m_PrefabAsset: {fileID: 0}\n")
                        .Append("  m_GameObject: {fileID: ")
                        .Append(gameObject.Groups[1].Value)
                        .Append("}\n")
                        .Append("  m_Enabled: 1\n  m_EditorHideFlags: 0\n")
                        .Append("  m_Script: {fileID: 11500000, guid: ")
                        .Append(missingScriptGuid)
                        .Append(", type: 3}\n")
                        .Append("  m_Name: \n  m_EditorClassIdentifier: \n");
                }
                string patchedBlock = "  m_Component:\n" + patchedEntries;
                string patchedYaml = yaml.Substring(0, componentBlock.Index) + patchedBlock + yaml.Substring(componentBlock.Index + componentBlock.Length);
                File.WriteAllText(fullPath, patchedYaml + missingComponents, new UTF8Encoding(false));
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
                Assert.IsNotNull(prefab);
                Object.DestroyImmediate(_target);
                _target = Object.Instantiate(prefab);
                var snapshot = _target.GetComponents<Component>();
                Assert.AreEqual(3, snapshot.Count(component => component == null), "The imported fixture must exercise actual native missing slots.");
                CollectionAssert.AreEqual(new[] { false, true, false, true, false, true }, snapshot.Select(component => component == null));
                CollectionAssert.AreEqual(
                    new[] { typeof(Transform), typeof(BoxCollider), typeof(SphereCollider) },
                    snapshot.Where(component => component != null).Select(component => component.GetType())
                );
                var expected = snapshot.Where(component => component != null).Select(component => component.GetInstanceIDCompat()).ToArray();
                var actual = new List<int>();
                for (int cursor = 0; cursor <= expected.Length; cursor++)
                {
                    var data = ReadPage(cursor, new JValue(1), includeProperties)["data"];
                    Assert.AreEqual(expected.Length, (int)data["totalCount"]);
                    Assert.AreEqual(cursor, (int)data["cursor"]);
                    if (cursor == expected.Length)
                    {
                        Assert.IsEmpty(data["components"]);
                        Assert.IsFalse((bool)data["hasMore"]);
                    }
                    else
                    {
                        actual.Add((int)data["components"][0]["instanceID"]);
                        Assert.AreEqual(cursor + 1 < expected.Length ? new JValue(cursor + 1) : JValue.CreateNull(), data["nextCursor"]);
                    }
                }
                CollectionAssert.AreEqual(expected, actual);
            }
            finally
            {
                Object.DestroyImmediate(_target);
                _target = null;
                Assert.IsTrue(AssetDatabase.DeleteAsset(folder), "Remove only this fixture's uniquely owned asset folder and metadata.");
            }
        }

        [Test]
        public void ReentrantGetter_CannotReplaceOuterComponentSnapshot()
        {
            var nestedTarget = new GameObject("NestedComponentPage");
            try
            {
                var component = _target.AddComponent<PagingReentrantComponent>();
                component.NestedTarget = nestedTarget;
                var last = _target.AddComponent<SphereCollider>();
                var expected = _target.GetComponents<Component>().Select(value => value.GetInstanceIDCompat()).ToArray();

                var response = JObject.FromObject(
                    GameObjectComponentsResource.HandleCommand(
                        new JObject
                        {
                            ["instanceID"] = _target.GetInstanceIDCompat(),
                            ["pageSize"] = 100,
                            ["includeProperties"] = true,
                            ["evaluate_getters"] = true,
                        }
                    )
                );

                Assert.Greater(component.Reads, 0, "The getter must perform a nested request.");
                CollectionAssert.AreEqual(expected, response["data"]["components"].Select(value => (int)value["instanceID"]));
                Assert.AreEqual(last.GetInstanceIDCompat(), (int)response["data"]["components"].Last["instanceID"]);
                Assert.AreEqual(expected.Length, (int)response["data"]["totalCount"]);
            }
            finally
            {
                Object.DestroyImmediate(nestedTarget);
            }
        }

        [Test]
        public void GetterMutation_PreservesInitialCountOrderAndDestroyedSlot()
        {
            var mutator = _target.AddComponent<PagingMutatingComponent>();
            mutator.DestroyTarget = _target.AddComponent<SphereCollider>();
            var snapshot = _target.GetComponents<Component>();
            var expectedIds = snapshot.Take(snapshot.Length - 1).Select(value => value.GetInstanceIDCompat()).ToArray();

            var response = JObject.FromObject(
                GameObjectComponentsResource.HandleCommand(
                    new JObject
                    {
                        ["instanceID"] = _target.GetInstanceIDCompat(),
                        ["pageSize"] = 100,
                        ["includeProperties"] = true,
                        ["evaluate_getters"] = true,
                    }
                )
            );

            Assert.IsTrue((bool)response["success"]);
            Assert.Greater(mutator.Reads, 0);
            var data = response["data"];
            Assert.AreEqual(snapshot.Length, (int)data["totalCount"]);
            Assert.AreEqual(snapshot.Length, data["components"].Count());
            CollectionAssert.AreEqual(expectedIds, data["components"].Take(expectedIds.Length).Select(value => (int)value["instanceID"]));
            Assert.AreEqual(JTokenType.Null, data["components"].Last.Type, "A later destroyed snapshot entry keeps the serializer's null result.");
            Assert.IsFalse((bool)data["hasMore"]);

            var fresh = ReadPage(0, new JValue(100))["data"];
            Assert.AreEqual(snapshot.Length + 1, (int)fresh["totalCount"]);
            Assert.AreEqual(snapshot.Length + 1, fresh["components"].Count());
            CollectionAssert.AreEqual(
                _target.GetComponents<Component>().Select(value => value.GetInstanceIDCompat()),
                fresh["components"].Select(value => (int)value["instanceID"])
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReturnedPage_RemainsDetachedAcrossLaterReadsAndMutation(bool includeProperties)
        {
            var response = GameObjectComponentsResource.HandleCommand(
                new JObject
                {
                    ["instanceID"] = _target.GetInstanceIDCompat(),
                    ["pageSize"] = 100,
                    ["includeProperties"] = includeProperties,
                }
            );
            var frozen = JObject.FromObject(response);
            var data = response.GetType().GetProperty("data").GetValue(response);
            var detached = (List<object>)data.GetType().GetProperty("components").GetValue(data);
            ReadPage(1, new JValue(100), includeProperties);
            Assert.IsTrue(JToken.DeepEquals(frozen, JObject.FromObject(response)));
            detached.Clear();
            var fresh = ReadPage(0, new JValue(100), includeProperties);
            Assert.AreEqual(2, fresh["data"]["components"].Count());
        }

        private JObject ReadPage(JToken cursor, JToken pageSize = null, bool includeProperties = false) =>
            JObject.FromObject(
                GameObjectComponentsResource.HandleCommand(
                    new JObject
                    {
                        ["instanceID"] = _target.GetInstanceIDCompat(),
                        ["cursor"] = cursor,
                        ["pageSize"] = pageSize ?? new JValue(1),
                        ["includeProperties"] = includeProperties,
                    }
                )
            );
    }

    public sealed class PagingMutatingComponent : MonoBehaviour
    {
        [System.NonSerialized]
        public Component DestroyTarget;

        [System.NonSerialized]
        public int Reads;

        public int ChangedCount
        {
            get
            {
                Reads++;
                if (Reads == 1)
                {
                    Object.DestroyImmediate(DestroyTarget);
                    gameObject.AddComponent<SphereCollider>();
                    gameObject.AddComponent<CapsuleCollider>();
                }
                return gameObject.GetComponents<Component>().Length;
            }
        }
    }

    public sealed class PagingReentrantComponent : MonoBehaviour
    {
        [System.NonSerialized]
        public GameObject NestedTarget;

        [System.NonSerialized]
        public int Reads;

        public int NestedCount
        {
            get
            {
                Reads++;
                var nested = JObject.FromObject(
                    GameObjectComponentsResource.HandleCommand(
                        new JObject { ["instanceID"] = NestedTarget.GetInstanceIDCompat(), ["includeProperties"] = false }
                    )
                );
                return (int)nested["data"]["totalCount"];
            }
        }
    }
}
