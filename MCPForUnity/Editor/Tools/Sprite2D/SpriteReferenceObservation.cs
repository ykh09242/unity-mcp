using System;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    internal static class SpriteReferenceObservation
    {
        private static JObject Identity(Sprite sprite)
        {
            if (!sprite || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sprite, out string guid, out long id))
                return null;
            Rect rect = sprite.rect;
            return new JObject
            {
                ["guid"] = guid,
                ["file_id"] = id.ToString(CultureInfo.InvariantCulture),
                ["name"] = sprite.name,
                ["rect"] = new JArray(rect.x, rect.y, rect.width, rect.height),
            };
        }

        internal static object Read(JObject request, SpriteDiagnosticBuilder diagnostics)
        {
            if (!SpriteParams.TryReadAssetPath(request, "path", out string path, out string error))
                return diagnostics.Fail("BAD_PARAM", error);
            if (
                request["guid"]?.Type != JTokenType.String
                || !Guid.TryParseExact((string)request["guid"], "N", out Guid guid)
                || request["file_id"]?.Type != JTokenType.String
                || !long.TryParse((string)request["file_id"], NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out long id)
                || id == 0
                || id.ToString(CultureInfo.InvariantCulture) != (string)request["file_id"]
                || request["scene_target"]?.Type != JTokenType.String
                || string.IsNullOrWhiteSpace((string)request["scene_target"])
                || ((string)request["scene_target"]).Length > 256
            )
                return diagnostics.Fail("BAD_PARAM", "Supply a texture GUID, canonical nonzero signed file_id string and scene_target up to 256 characters.");

            string fileId = id.ToString(CultureInfo.InvariantCulture);
            float[] expectedRect = null;
            if (request["expected_rect"] != null)
            {
                if (
                    !(request["expected_rect"] is JArray rect)
                    || rect.Count != 4
                    || rect.Any(value => value.Type != JTokenType.Integer && value.Type != JTokenType.Float)
                )
                    return diagnostics.Fail("BAD_PARAM", "expected_rect requires four source-pixel coordinates.");
                expectedRect = rect.Values<float>().ToArray();
                if (expectedRect.Any(value => float.IsNaN(value) || float.IsInfinity(value) || value < 0) || expectedRect[2] <= 0 || expectedRect[3] <= 0)
                    return diagnostics.Fail("BAD_PARAM", "expected_rect requires finite nonnegative coordinates and positive size.");
            }
            string expectedGuid = guid.ToString("N");
            string importedGuid = AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets);
            JObject imported = null;
            string importState = "texture_guid_mismatch";
            if (EditorApplication.isUpdating)
                importState = "import_in_progress";
            else if (string.IsNullOrEmpty(importedGuid) || AssetDatabase.IsValidFolder(path))
                importState = "texture_unavailable";
            else if (expectedGuid == importedGuid)
            {
                var objects = AssetDatabase.LoadAllAssetsAtPath(path);
                if (objects.Length > 4096)
                    importState = "subasset_limit";
                else
                {
                    imported = objects
                        .OfType<Sprite>()
                        .Select(Identity)
                        .FirstOrDefault(row => row?.Value<string>("guid") == expectedGuid && row.Value<string>("file_id") == fileId);
                    importState = imported == null ? "file_id_missing" : "resolved";
                }
            }
            var matches = ManageUGUI.SceneObjects(includeInactive: true).Where(go => go.name == (string)request["scene_target"]).Take(2).ToArray();
            var owner = matches.Length == 1 ? matches[0] : null;
            string targetState = matches.Length > 1 ? "ambiguous" : "not_loaded";
            JObject serialized = null;
            JObject assigned = null;
            string component = null;
            if (owner != null)
            {
                var renderer = owner.GetComponent<SpriteRenderer>();
                var imageType = Type.GetType("UnityEngine.UI.Image, UnityEngine.UI", false);
                var image = imageType == null ? null : owner.GetComponent(imageType) as Behaviour;
                if ((renderer != null) == (image != null))
                    targetState = renderer == null ? "no_sprite_component" : "ambiguous_component";
                else
                {
                    Component source = renderer != null ? (Component)renderer : image;
                    component = renderer != null ? "SpriteRenderer" : "Image";
                    using (var so = new SerializedObject(source))
                    using (var property = so.FindProperty("m_Sprite"))
                        serialized = Identity(property?.objectReferenceValue as Sprite);
                    Sprite live = renderer != null ? renderer.sprite : imageType.GetProperty("overrideSprite")?.GetValue(image) as Sprite;
                    assigned = Identity(live);
                    bool active = owner.activeInHierarchy && (renderer != null ? renderer.enabled : image.enabled);
                    targetState =
                        assigned == null ? "no_persistent_sprite"
                        : !active ? "inactive"
                        : "assigned";
                }
            }
            return new JObject
            {
                ["success"] = true,
                ["requested_reference"] = new JObject { ["guid"] = expectedGuid, ["file_id"] = fileId },
                ["texture_guid"] = importedGuid,
                ["import_state"] = importState,
                ["imported_sprite"] = imported,
                ["component"] = component,
                ["target_state"] = targetState,
                ["target_scope"] = PrefabStageUtility.GetCurrentPrefabStage() == null ? "loaded_scenes" : "prefab_stage",
                ["target_scene"] = owner != null ? owner.scene.name : null,
                ["target_scene_path"] = owner != null ? owner.scene.path : null,
                ["serialized_sprite"] = serialized,
                ["assigned_sprite"] = assigned,
                ["assigned_rect_matches"] =
                    expectedRect == null || targetState != "assigned"
                        ? JValue.CreateNull()
                        : new JValue(assigned["rect"].Values<float>().SequenceEqual(expectedRect)),
                ["identity_matches"] =
                    targetState != "assigned" || imported == null
                        ? JValue.CreateNull()
                        : new JValue(assigned.Value<string>("guid") == expectedGuid && assigned.Value<string>("file_id") == fileId),
                ["observation"] = "Current component assignment. Camera visibility, occlusion and final pixels require frame evidence.",
            };
        }
    }
}
