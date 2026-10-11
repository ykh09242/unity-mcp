using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageSceneEditsTests
    {
        private readonly List<GameObject> _objects = new List<GameObject>();

        [SetUp]
        public void SetUp()
        {
            Undo.ClearAll();
            Cache().Clear();
        }

        [TearDown]
        public void TearDown()
        {
            Undo.ClearAll();
            Cache().Clear();
            foreach (GameObject go in _objects)
                Object.DestroyImmediate(go);
            _objects.Clear();
        }

        private GameObject Create(string name)
        {
            var go = new GameObject(name);
            _objects.Add(go);
            return go;
        }

        private static IDictionary Cache() =>
            (IDictionary)typeof(ManageSceneEdits).GetField("Previews", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

        private static JObject Send(JObject request) => JObject.FromObject(ManageSceneEdits.HandleCommand(request));

        private static JObject Edit(GameObject target, string property, JToken value, string type = "Transform") =>
            new JObject
            {
                ["target"] = target.GetInstanceIDCompat(),
                ["component_type"] = type,
                ["property"] = property,
                ["value"] = value,
            };

        private static JObject Preview(params JObject[] edits) => Send(new JObject { ["action"] = "preview", ["edits"] = new JArray(edits) });

        private static string Token(JObject preview)
        {
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            return preview["data"].Value<string>("preview_id");
        }

        private static JObject Apply(string token) =>
            Send(
                new JObject
                {
                    ["action"] = "apply",
                    ["preview_id"] = token,
                    ["undo_name"] = "Scene edit test",
                }
            );

        [Test]
        public void Preview_DeterministicValuesWithoutSceneOrUndoMutation()
        {
            var go = Create("scene-edit-preview");
            go.transform.localPosition = new Vector3(1, 2, 3);
            int undo = Undo.GetCurrentGroup();
            bool dirty = go.scene.isDirty;
            var request = Edit(go, "localPosition", new JArray(4, 5, 6));
            var first = Preview(request);
            var second = Preview(request);
            Token(first);
            Token(second);
            Assert.IsTrue(JToken.DeepEquals(first["data"]["edits"], second["data"]["edits"]));
            Assert.AreEqual(new Vector3(1, 2, 3), go.transform.localPosition);
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
            Assert.AreEqual(dirty, go.scene.isDirty);
            Assert.AreEqual(JTokenType.Integer, first["data"]["edits"][0]["scene_handle"].Type);
            Assert.AreEqual("m_LocalPosition", first["data"]["edits"][0].Value<string>("property"));
            CollectionAssert.AreEqual(new[] { 1f, 2f, 3f }, first["data"]["edits"][0]["before"].ToObject<float[]>());
        }

        [Test]
        public void Apply_MultipleComponentsAndObjects_RestoredByOneUndo()
        {
            var first = Create("scene-edit-a");
            var second = Create("scene-edit-b");
            var collider = second.AddComponent<BoxCollider>();
            string token = Token(
                Preview(
                    Edit(first, "localPosition", new JArray(1, 2, 3)),
                    Edit(first, "localScale", new JArray(2, 2, 2)),
                    Edit(second, "m_IsTrigger", true, "BoxCollider")
                )
            );
            JObject result = Apply(token);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(new Vector3(1, 2, 3), first.transform.localPosition);
            Assert.AreEqual(new Vector3(2, 2, 2), first.transform.localScale);
            Assert.IsTrue(collider.isTrigger);
            Undo.PerformUndo();
            Assert.AreEqual(Vector3.zero, first.transform.localPosition);
            Assert.AreEqual(Vector3.one, first.transform.localScale);
            Assert.IsFalse(collider.isTrigger);
            Assert.IsFalse(Apply(token).Value<bool>("success"));
        }

        [Test]
        public void DriftOnLaterTarget_RejectsEntirePlanAndConsumesToken()
        {
            var first = Create("scene-edit-a");
            var second = Create("scene-edit-b");
            string token = Token(Preview(Edit(first, "localPosition", new JArray(1, 0, 0)), Edit(second, "localScale", new JArray(2, 2, 2))));
            second.transform.localScale = new Vector3(3, 3, 3);
            int undo = Undo.GetCurrentGroup();
            JObject rejected = Apply(token);
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, first.transform.localPosition);
            Assert.AreEqual(new Vector3(3, 3, 3), second.transform.localScale);
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
            Assert.IsFalse(Apply(token).Value<bool>("success"));
        }

        [Test]
        public void ExpiredPreview_RejectsWithoutMutation()
        {
            var go = Create("scene-edit-expired");
            string token = Token(Preview(Edit(go, "localPosition", new JArray(5, 0, 0))));
            object plan = Cache()[token];
            plan.GetType().GetField("ExpiresAt", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(plan, -1d);
            Assert.IsFalse(Apply(token).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
            Assert.IsFalse(Cache().Contains(token));
        }

        [Test]
        public void AmbiguousNameOrComponents_RequireExplicitSelection()
        {
            var first = Create("scene-edit-duplicate");
            Create(first.name);
            var edit = Edit(first, "localPosition", new JArray(1, 2, 3));
            edit["target"] = first.name;
            edit["search_method"] = "by_name";
            Assert.IsFalse(Preview(edit).Value<bool>("success"));
            first.AddComponent<BoxCollider>();
            var second = first.AddComponent<BoxCollider>();
            edit = Edit(first, "m_IsTrigger", true, "BoxCollider");
            Assert.IsFalse(Preview(edit).Value<bool>("success"));
            edit["component_index"] = 1;
            Assert.IsTrue(Apply(Token(Preview(edit))).Value<bool>("success"));
            Assert.IsTrue(second.isTrigger);
            Assert.IsFalse(first.GetComponent<BoxCollider>().isTrigger);
        }

        [TestCase("m_Father")]
        [TestCase("m_GameObject")]
        [TestCase("position")]
        [TestCase("m_LocalPosition.x")]
        public void UnsupportedProperties_FailBeforeAnyApply(string property)
        {
            var go = Create("scene-edit-invalid");
            var good = Edit(go, "localPosition", new JArray(1, 2, 3));
            good["expected_old"] = new JArray(0, 0, 0);
            var bad = Edit(go, property, 1);
            bad["expected_old"] = 0;
            var result = Send(new JObject { ["action"] = "apply", ["edits"] = new JArray(good, bad) });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
        }

        [Test]
        public void DirectApply_RequiresExpectedOldAndRejectsMismatch()
        {
            var go = Create("scene-edit-expected");
            var edit = Edit(go, "localPosition", new JArray(1, 2, 3));
            var request = new JObject { ["action"] = "apply", ["edits"] = new JArray(edit) };
            Assert.IsFalse(Send(request).Value<bool>("success"));
            ((JObject)request["edits"][0])["expected_old"] = new JArray(9, 9, 9);
            Assert.IsFalse(Send(request).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
            ((JObject)request["edits"][0])["expected_old"] = new JArray(0, 0, 0);
            Assert.IsTrue(Send(request).Value<bool>("success"));
            Assert.AreEqual(new Vector3(1, 2, 3), go.transform.localPosition);
        }

        [Test]
        public void DuplicateAliasesAndNonfiniteValues_RejectWithoutMutation()
        {
            var go = Create("scene-edit-bounds");
            Assert.IsFalse(Preview(Edit(go, "localPosition", new JArray(1, 2, 3)), Edit(go, "m_LocalPosition", new JArray(3, 2, 1))).Value<bool>("success"));
            Assert.IsFalse(Preview(Edit(go, "localPosition", new JArray(float.NaN, 0, 0))).Value<bool>("success"));
            Assert.IsFalse(Preview(Edit(go, "localRotation", new JArray(0, 0, 0, 0))).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
        }

        [Test]
        public void SupportedWriteFailure_CompensatesEarlierWrites()
        {
            var go = Create("scene-edit-compensation");
            string token = Token(Preview(Edit(go, "localPosition", new JArray(1, 2, 3)), Edit(go, "localScale", new JArray(2, 2, 2))));
            object plan = Cache()[token];
            var edits = (IList)plan.GetType().GetField("Edits", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(plan);
            // Inject a write-time codec failure after production prevalidation. This exercises
            // real Undo compensation without adding a production fault-injection API.
            object later = edits[1];
            later.GetType().GetField("After", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(later, new JArray("invalid", 2, 2));
            var method = typeof(ManageSceneEdits).GetMethod("Apply", BindingFlags.Static | BindingFlags.NonPublic);
            JObject result = JObject.FromObject(method.Invoke(null, new[] { plan, "Scene edit failure" }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(result["data"].Value<bool>("rollback_verified"), result.ToString());
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
            Assert.AreEqual(Vector3.one, go.transform.localScale);
        }
    }
}
