using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class InputActionAuthoringTests
    {
        private string folder,
            path;
        private int undoGroup;
        private Scene preview;
        private Type assetType;

        [SetUp]
        public void SetUp()
        {
            assetType = Type.GetType("UnityEngine.InputSystem.InputActionAsset, Unity.InputSystem");
            Assert.IsNotNull(assetType, "Controller fixture requires com.unity.inputsystem 1.7+; package absence is not product RED.");
            folder = "Assets/Play17_" + Guid.NewGuid().ToString("N").Substring(0, 12);
            Directory.CreateDirectory(AssetPathUtility.GetFullAssetPath(folder));
            path = folder + "/Controls.inputactions";
            Undo.IncrementCurrentGroup();
            undoGroup = Undo.GetCurrentGroup();
        }

        [TearDown]
        public void TearDown()
        {
            if (undoGroup > 0)
                Undo.RevertAllDownToGroup(undoGroup);
            if (preview.IsValid())
                EditorSceneManager.ClosePreviewScene(preview);
            if (folder != null)
            {
                AssetDatabase.DeleteAsset(folder);
                string full = AssetPathUtility.GetFullAssetPath(folder);
                if (Directory.Exists(full))
                    Directory.Delete(full, true);
                if (File.Exists(full + ".meta"))
                    File.Delete(full + ".meta");
            }
        }

        private JObject Call(string action, JObject values = null)
        {
            Type handler = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("MCPForUnity.Editor.Tools.InputActions.ManageInputActions"))
                .FirstOrDefault(type => type != null);
            Assert.IsNotNull(handler, "PLAY17 public InputAction authoring handler is missing.");
            MethodInfo method = handler.GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method);
            var request = values == null ? new JObject() : (JObject)values.DeepClone();
            request["action"] = action;
            request["path"] = path;
            return JObject.FromObject(method.Invoke(null, new object[] { request }));
        }

        private JObject Succeed(string action, JObject values = null)
        {
            JObject reply = Call(action, values);
            Assert.IsTrue(reply.Value<bool>("success"), reply.ToString());
            return (JObject)reply["data"];
        }

        private JObject Edit(string action, JObject values)
        {
            values["expected_revision"] = Succeed("read")["revision"];
            return Succeed(action, values);
        }

        private (string map, string action, string binding) CreateOneBinding()
        {
            Succeed("create_asset");
            string map = (string)Edit("add_map", new JObject { ["name"] = "Gameplay" })["created_id"];
            string action = (string)
                Edit(
                    "add_action",
                    new JObject
                    {
                        ["map_id"] = map,
                        ["name"] = "Fire",
                        ["action_type"] = "Button",
                    }
                )["created_id"];
            string binding = (string)
                Edit(
                    "add_binding",
                    new JObject
                    {
                        ["map_id"] = map,
                        ["action_id"] = action,
                        ["binding_path"] = "<Keyboard>/space",
                    }
                )["created_id"];
            return (map, action, binding);
        }

        private JObject NativeJson()
        {
            var asset = AssetDatabase.LoadAssetAtPath(path, assetType);
            Assert.IsNotNull(asset, "The real Input System importer must produce a native asset.");
            return JObject.Parse((string)assetType.GetMethod("ToJson", Type.EmptyTypes).Invoke(asset, null));
        }

        [Test]
        public void Author_MapAndTwoBindings_ReimportPreservesNativeIds()
        {
            // Given an actual imported action asset and its first map/action/binding.
            var ids = CreateOneBinding();
            string second = (string)
                Edit(
                    "add_binding",
                    new JObject
                    {
                        ["map_id"] = ids.map,
                        ["action_id"] = ids.action,
                        ["binding_path"] = "<Gamepad>/buttonSouth",
                    }
                )["created_id"];
            string guid = AssetDatabase.AssetPathToGUID(path);
            // When Unity force-reimports the serialized .inputactions source.
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            JObject native = NativeJson();
            // Then native, serialized and public readback identities agree, including both bindings.
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(ids.map, (string)native["maps"][0]["id"]);
            Assert.AreEqual(ids.action, (string)native["maps"][0]["actions"][0]["id"]);
            CollectionAssert.AreEquivalent(new[] { ids.binding, second }, native["maps"][0]["bindings"].Select(row => (string)row["id"]).ToArray());
            JObject read = Succeed("read");
            Assert.AreEqual(2, read["rows"].Count(row => (string)row["kind"] == "binding"));
            Assert.AreEqual(guid, (string)read["asset_guid"]);
        }

        [Test]
        public void DuplicateMapAndBinding_RefuseWithoutChangingSource()
        {
            // Given a uniquely named map and an existing simple binding.
            var ids = CreateOneBinding();
            byte[] before = File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(path));
            string revision = (string)Succeed("read")["revision"];
            // When exact duplicate authoring is requested.
            JObject duplicateMap = Call("add_map", new JObject { ["name"] = "gameplay", ["expected_revision"] = revision });
            JObject duplicateBinding = Call(
                "add_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["action_id"] = ids.action,
                    ["binding_path"] = "<Keyboard>/space",
                    ["expected_revision"] = revision,
                }
            );
            // Then both duplicate guards refuse and native identities/source bytes survive.
            Assert.IsFalse(duplicateMap.Value<bool>("success"), duplicateMap.ToString());
            Assert.IsFalse(duplicateBinding.Value<bool>("success"), duplicateBinding.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(path)));
            Assert.AreEqual(ids.binding, (string)NativeJson()["maps"][0]["bindings"][0]["id"]);
        }

        [Test]
        public void GuidFormActionReference_DuplicateBindingRefusesAndOriginalReferenceSurvives()
        {
            // Given a valid imported binding that refers to its action by GUID instead of name.
            var ids = CreateOneBinding();
            string full = AssetPathUtility.GetFullAssetPath(path);
            JObject source = JObject.Parse(File.ReadAllText(full));
            source["maps"][0]["actions"][0]["name"] = "Move";
            string reference = ids.action;
            source["maps"][0]["bindings"][0]["action"] = reference;
            source["maps"][0]["bindings"][0]["path"] = "<Keyboard>/w";
            source["maps"][0]["bindings"][0]["groups"] = "Keyboard";
            File.WriteAllText(full, source.ToString());
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            var native = AssetDatabase.LoadAssetAtPath(path, assetType);
            object action = assetType.GetMethod("FindAction", new[] { typeof(string), typeof(bool) }).Invoke(native, new object[] { ids.action, true });
            object bindings = action.GetType().GetProperty("bindings").GetValue(action);
            Assert.AreEqual(
                1,
                bindings.GetType().GetProperty("Count").GetValue(bindings),
                "Real Input System must resolve the GUID-form binding to this action."
            );
            byte[] before = File.ReadAllBytes(full);
            string guid = AssetDatabase.AssetPathToGUID(path);
            // When adding the same action/path/group through the public action GUID selector.
            JObject refused = Call(
                "add_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["action_id"] = ids.action,
                    ["binding_path"] = "<Keyboard>/w",
                    ["groups"] = "Keyboard",
                    ["expected_revision"] = Succeed("read")["revision"],
                }
            );
            // Then equivalent name/GUID references cannot bypass the duplicate guard or rewrite source.
            Assert.IsFalse(refused.Value<bool>("success"), "Equivalent GUID-form existing binding was accepted: " + refused);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(full));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            string second = (string)
                Edit(
                    "add_binding",
                    new JObject
                    {
                        ["map_id"] = ids.map,
                        ["action_id"] = ids.action,
                        ["binding_path"] = "<Keyboard>/s",
                        ["groups"] = "Keyboard",
                    }
                )["created_id"];
            Assert.AreEqual(reference, (string)JObject.Parse(File.ReadAllText(full))["maps"][0]["bindings"][0]["action"]);
            before = File.ReadAllBytes(full);
            refused = Call(
                "set_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["binding_id"] = second,
                    ["binding_path"] = "<Keyboard>/w",
                    ["groups"] = "Keyboard",
                    ["expected_revision"] = Succeed("read")["revision"],
                }
            );
            Assert.IsFalse(refused.Value<bool>("success"), "Changing another binding to the equivalent GUID-form target was accepted: " + refused);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(full));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(ids.binding, (string)NativeJson()["maps"][0]["bindings"][0]["id"]);
        }

        [Test]
        public void UndoRedo_PersistsBindingEditAndPreservesExistingPlayerInputLink()
        {
            // Given a real PlayerInput component already linked to this imported asset.
            var ids = CreateOneBinding();
            preview = EditorSceneManager.NewPreviewScene();
            var player = new GameObject("OwnedPlayer");
            player.SetActive(false);
            SceneManager.MoveGameObjectToScene(player, preview);
            Type playerType = Type.GetType("UnityEngine.InputSystem.PlayerInput, Unity.InputSystem");
            Assert.IsNotNull(playerType);
            Component component = player.AddComponent(playerType);
            PropertyInfo actions = playerType.GetProperty("actions");
            actions.SetValue(component, AssetDatabase.LoadAssetAtPath(path, assetType));
            string guid = AssetDatabase.AssetPathToGUID(path);
            Undo.IncrementCurrentGroup();
            // When one binding path changes and native Undo/Redo is exercised.
            Edit(
                "set_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["binding_id"] = ids.binding,
                    ["binding_path"] = "<Keyboard>/enter",
                }
            );
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual("<Keyboard>/space", (string)NativeJson()["maps"][0]["bindings"][0]["path"]);
            Assert.AreEqual(
                "<Keyboard>/space",
                (string)JObject.Parse(File.ReadAllText(AssetPathUtility.GetFullAssetPath(path)))["maps"][0]["bindings"][0]["path"]
            );
            Undo.PerformRedo();
            // Then redo keeps the same binding and the component's existing asset reference.
            Assert.AreEqual("<Keyboard>/enter", (string)NativeJson()["maps"][0]["bindings"][0]["path"]);
            Assert.AreEqual(ids.binding, (string)NativeJson()["maps"][0]["bindings"][0]["id"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            var linked = (UnityEngine.Object)actions.GetValue(component);
            Assert.IsNotNull(linked);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(linked)));
        }

        [Test]
        public void StaleRevisionAndUndoExternalEdit_PreserveExternalBytes()
        {
            // Given a valid edited binding and its previous revision.
            var ids = CreateOneBinding();
            string stale = (string)Succeed("read")["revision"];
            Undo.IncrementCurrentGroup();
            Edit(
                "set_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["binding_id"] = ids.binding,
                    ["binding_path"] = "<Keyboard>/enter",
                }
            );
            byte[] changed = File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(path));
            // When a stale mutation is requested, then an external owner changes the file before Undo.
            JObject refused = Call(
                "remove_binding",
                new JObject
                {
                    ["map_id"] = ids.map,
                    ["binding_id"] = ids.binding,
                    ["expected_revision"] = stale,
                }
            );
            Assert.IsFalse(refused.Value<bool>("success"), refused.ToString());
            CollectionAssert.AreEqual(changed, File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(path)));
            byte[] external = changed.Concat(new byte[] { 10, 32 }).ToArray();
            File.WriteAllBytes(AssetPathUtility.GetFullAssetPath(path), external);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            // Then file-backed Undo refuses drift instead of silently replacing external bytes.
            CollectionAssert.AreEqual(external, File.ReadAllBytes(AssetPathUtility.GetFullAssetPath(path)));
            Assert.AreEqual("conflict", (string)Succeed("read")["undo_status"]);
        }
    }
}
