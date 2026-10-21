using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    // Stateless compare-before-dispatch guards. No Unity objects or command payloads are retained.
    internal static class BatchTargetPreview
    {
        private static readonly byte[] SigningKey = NewKey();

        private sealed class Incomplete : Exception
        {
            internal Incomplete(string message)
                : base(message) { }
        }

        private static byte[] NewKey()
        {
            var key = new byte[32];
            using (var random = RandomNumberGenerator.Create())
                random.GetBytes(key);
            return key;
        }

        private static JToken Canonical(JToken value)
        {
            if (value is JObject obj)
                return new JObject(obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => new JProperty(p.Name, Canonical(p.Value))));
            if (value is JArray array)
                return new JArray(array.Select(Canonical));
            return value.DeepClone();
        }

        private static byte[] Bytes(JToken value) => Encoding.UTF8.GetBytes(Canonical(value).ToString(Formatting.None));

        private static string Hash(JToken value)
        {
            using (var hash = SHA256.Create())
                return Convert.ToBase64String(hash.ComputeHash(Bytes(value)));
        }

        private static string Sign(JObject value)
        {
            using (var hmac = new HMACSHA256(SigningKey))
                return Convert.ToBase64String(hmac.ComputeHash(Bytes(value)));
        }

        private static bool SignatureMatches(string expected, string actual)
        {
            if (actual == null || expected.Length != actual.Length)
                return false;
            int difference = 0;
            for (int index = 0; index < expected.Length; index++)
                difference |= expected[index] ^ actual[index];
            return difference == 0;
        }

        internal static object Prepare(JArray commands)
        {
            var items = new JArray();
            var revisions = new JArray();
            var touched = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            bool valid = true,
                complete = true;
            for (int index = 0; index < commands.Count; index++)
            {
                var command = (JObject)commands[index];
                string name = command.Value<string>("tool"),
                    status = "valid",
                    reason = "ok";
                try
                {
                    JObject target = Describe(command);
                    foreach (string key in target["keys"].Values<string>())
                        if (!touched.Add(key))
                            throw new Incomplete("dependent_target");
                    revisions.Add(Hash(target));
                }
                catch (Incomplete exception)
                {
                    status = "incomplete";
                    reason = exception.Message;
                    complete = false;
                }
                catch (Exception exception)
                    when (exception is ArgumentException
                        || exception is InvalidOperationException
                        || exception is IOException
                        || exception is UnauthorizedAccessException
                    )
                {
                    status = "rejected";
                    reason = "invalid_parameters_or_target";
                }
                valid &= status == "valid";
                items.Add(
                    new JObject
                    {
                        ["index"] = index,
                        ["tool"] = name.Length <= 128 ? name : name.Substring(0, 128),
                        ["toolTruncated"] = name.Length > 128,
                        ["status"] = status,
                        ["reason"] = reason,
                    }
                );
            }
            JObject preview = null;
            if (valid)
            {
                preview = new JObject
                {
                    ["contractVersion"] = 1,
                    ["commandsHash"] = Hash(commands),
                    ["revisions"] = revisions,
                };
                preview["signature"] = Sign(preview);
            }
            var data = new JObject
            {
                ["validation"] = new JObject
                {
                    ["contractVersion"] = 1,
                    ["validateOnly"] = true,
                    ["scope"] = "targets",
                    ["complete"] = complete,
                    ["valid"] = valid,
                    ["items"] = items,
                },
                ["preview"] = preview,
                ["requestedCount"] = commands.Count,
                ["attemptedCount"] = 0,
                ["notRunCount"] = commands.Count,
                ["executionStatus"] = "not_started",
                ["rollbackPerformed"] = false,
                ["atomicity"] = "recheck_before_each_supported_mutation",
            };
            return valid
                ? new SuccessResponse("Supported targets inspected without mutation or refresh.", data)
                : new ErrorResponse("Target preview is rejected or incomplete; no plan can be applied.", data);
        }

        internal static bool Verify(JArray commands, JObject preview)
        {
            if (
                preview?["contractVersion"]?.Type != JTokenType.Integer
                || preview.Value<int>("contractVersion") != 1
                || preview["signature"]?.Type != JTokenType.String
                || preview["revisions"] is not JArray revisions
                || revisions.Count != commands.Count
            )
                return false;
            var unsigned = (JObject)preview.DeepClone();
            unsigned.Remove("signature");
            if (!SignatureMatches(Sign(unsigned), preview.Value<string>("signature")) || preview.Value<string>("commandsHash") != Hash(commands))
                return false;
            for (int index = 0; index < commands.Count; index++)
                if (!Matches((JObject)commands[index], preview, index))
                    return false;
            return true;
        }

        internal static bool Matches(JObject command, JObject preview, int index)
        {
            try
            {
                return preview["revisions"][index]?.Type == JTokenType.String && Hash(Describe(command)) == preview["revisions"][index].Value<string>();
            }
            catch (Exception exception)
                when (exception is Incomplete
                    || exception is ArgumentException
                    || exception is InvalidOperationException
                    || exception is IOException
                    || exception is UnauthorizedAccessException
                )
            {
                return false;
            }
        }

        private static JObject Describe(JObject command)
        {
            if (
                EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || PrefabStageUtility.GetCurrentPrefabStage() != null
            )
                throw new Incomplete("editor_not_stable");
            string name = command.Value<string>("tool");
            if (!MCPServiceLocator.ToolDiscovery.IsToolEnabled("batch_execute") || !MCPServiceLocator.ToolDiscovery.IsToolEnabled(name))
                throw new InvalidOperationException("Tool disabled.");
            string handler = CommandRegistry.GetBatchTargetIdentity(name);
            if (handler == null)
                throw new Incomplete("unsupported_target_preview");
            JObject parameters = BatchExecute.NormalizeParameterKeys(command["params"] as JObject ?? new JObject());
            string action = parameters["action"]?.Type == JTokenType.String ? parameters.Value<string>("action").ToLowerInvariant() : null;
            JObject state;
            switch (name)
            {
                case "asset_edit":
                case "manage_asset":
                    if (action != "move" && action != "rename")
                        throw new Incomplete("unsupported_target_preview");
                    state = AssetMove(parameters);
                    break;
                case "gameobject_edit":
                case "manage_gameobject":
                    if (action != "modify")
                        throw new Incomplete("unsupported_target_preview");
                    state = ObjectEdit(parameters);
                    break;
                case "prefab_contents":
                case "manage_prefabs":
                    if (action != "apply_preview")
                        throw new Incomplete("prepare_prefab_plan_first");
                    Keys(parameters, "action", "previewId");
                    state = PrefabSourcePlans.InspectBatchPlan(Text(parameters, "previewId"));
                    break;
                default:
                    throw new Incomplete("unsupported_target_preview");
            }
            state["handler"] = handler;
            return state;
        }

        private static JObject AssetMove(JObject parameters)
        {
            Keys(parameters, "action", "path", "destination");
            string source = AssetPathUtility.GetContainedAssetPath(Text(parameters, "path"));
            string destination = AssetPathUtility.GetContainedAssetPath(Text(parameters, "destination"));
            string fullSource = AssetPathUtility.GetFullAssetPath(source),
                fullDestination = AssetPathUtility.GetFullAssetPath(destination);
            if (Directory.Exists(fullSource))
                throw new Incomplete("directory_move_not_modeled");
            if (!File.Exists(fullSource) || string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(source)))
                throw new ArgumentException("Source is not an imported file asset.");
            if (
                File.Exists(fullDestination)
                || Directory.Exists(fullDestination)
                || File.Exists(fullDestination + ".meta")
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(destination))
            )
                throw new ArgumentException("Destination is occupied.");
            string parent = AssetPathUtility.NormalizeSeparators(Path.GetDirectoryName(destination));
            if (!AssetDatabase.IsValidFolder(parent))
                throw new Incomplete("destination_parent_not_present");
            if (ManageAsset.RequireScriptConsent(source, destination) != null)
                throw new InvalidOperationException("Script consent unavailable.");
            return new JObject
            {
                ["keys"] = new JArray("asset:" + source, "asset:" + destination),
                ["guid"] = AssetDatabase.AssetPathToGUID(source),
                ["parentGuid"] = AssetDatabase.AssetPathToGUID(parent),
                ["source"] = FileHash(fullSource),
                ["meta"] = FileHash(AssetPathUtility.GetFullAssetPath(source + ".meta")),
            };
        }

        private static string FileHash(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return Convert.ToBase64String(sha.ComputeHash(stream));
        }

        private static JObject ObjectEdit(JObject parameters)
        {
            Keys(parameters, "action", "target", "searchMethod", "name", "setActive", "position", "rotation", "scale");
            if (parameters["target"]?.Type != JTokenType.Integer)
                throw new Incomplete("concrete_instance_id_required");
            if (parameters["searchMethod"] != null && parameters["searchMethod"].Type != JTokenType.Null && parameters.Value<string>("searchMethod") != "by_id")
                throw new Incomplete("concrete_instance_id_required");
            GameObject target = GameObjectLookup.FindById(parameters.Value<int>("target"));
            if (
                target == null
                || EditorUtility.IsPersistent(target)
                || !target.scene.IsValid()
                || !target.scene.isLoaded
                // Match GameObjectModify by_id eligibility without scanning the whole scene.
                || target.scene != SceneManager.GetActiveScene()
                || (!target.activeInHierarchy && parameters["setActive"]?.ReadScalar<bool?>() != true)
                || EditorSceneManager.IsPreviewScene(target.scene)
            )
                throw new ArgumentException("Target is not an ordinary loaded scene object.");
            foreach (string field in new[] { "position", "rotation", "scale" })
                if (parameters[field] != null && parameters[field].Type != JTokenType.Null)
                    ValidateVector(parameters[field]);
            if (parameters["name"] != null && parameters["name"].Type != JTokenType.Null)
                Text(parameters, "name");
            if (parameters["setActive"] != null && parameters["setActive"].Type != JTokenType.Null && parameters["setActive"].Type != JTokenType.Boolean)
                throw new ArgumentException("setActive requires a boolean.");
            var ancestors = new JArray();
            for (Transform node = target.transform; node != null; node = node.parent)
                ancestors.Add(
                    new JObject
                    {
                        ["id"] = node.gameObject.GetInstanceIDCompat(),
                        ["active"] = node.gameObject.activeSelf,
                        ["position"] = Vector(node.localPosition),
                        ["rotation"] = new JArray(node.localRotation.x, node.localRotation.y, node.localRotation.z, node.localRotation.w),
                        ["scale"] = Vector(node.localScale),
                    }
                );
            return new JObject
            {
                ["keys"] = new JArray("object:" + target.GetInstanceIDCompat()),
                ["scene"] = SceneIdentity(target.scene),
                ["scenePath"] = target.scene.path,
                ["name"] = target.name,
                ["active"] = target.activeSelf,
                ["ancestors"] = ancestors,
                ["dirty"] = EditorUtility.GetDirtyCount(target),
                ["transformDirty"] = EditorUtility.GetDirtyCount(target.transform),
                ["components"] = new JArray(target.GetComponents<Component>().Select(c => c == null ? 0 : c.GetInstanceIDCompat())),
            };
        }

        private static JValue SceneIdentity(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        private static JArray Vector(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static void ValidateVector(JToken token)
        {
            if (token is not JArray array || array.Count != 3)
                throw new ArgumentException("Exactly three finite numbers are required.");
            foreach (JToken number in array)
                if (
                    (number.Type != JTokenType.Float && number.Type != JTokenType.Integer)
                    || float.IsNaN(number.Value<float>())
                    || float.IsInfinity(number.Value<float>())
                )
                    throw new ArgumentException("Finite float numbers are required.");
        }

        private static string Text(JObject parameters, string key)
        {
            if (parameters[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(parameters.Value<string>(key)))
                throw new ArgumentException(key + " is required.");
            return parameters.Value<string>(key);
        }

        private static void Keys(JObject parameters, params string[] keys)
        {
            if (parameters.Properties().Any(p => !keys.Contains(p.Name)))
                throw new Incomplete("unsupported_parameters");
        }
    }
}
