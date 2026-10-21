using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
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

        internal static object Prepare(JArray commands) => PrepareCore(commands, false, CancellationToken.None).GetAwaiter().GetResult();

        internal static Task<object> PrepareAsync(JArray commands, CancellationToken cancellationToken) => PrepareCore(commands, true, cancellationToken);

        private static async Task<object> PrepareCore(JArray commands, bool background, CancellationToken cancellationToken)
        {
            var observations = new List<Observation>();
            try
            {
                var items = new JArray();
                var revisions = new JArray();
                var touched = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                bool valid = true,
                    complete = true;
                for (int index = 0; index < commands.Count; index++)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var command = (JObject)commands[index];
                    string name = command.Value<string>("tool"),
                        status = "valid",
                        reason = "ok";
                    try
                    {
                        Observation observation = await ObserveAsync(command, background, cancellationToken);
                        observations.Add(observation);
                        JObject target = observation.State;
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
                // Later file work yields the Editor. Recheck earlier scalar observations together
                // before publishing a guard; no Unity object crosses the worker boundary.
                if (background && valid)
                    for (int index = 0; index < commands.Count; index++)
                        if (!observations[index].IsCurrent((JObject)commands[index]))
                        {
                            items[index]["status"] = "incomplete";
                            items[index]["reason"] = "target_changed_during_preparation";
                            valid = complete = false;
                        }
                cancellationToken.ThrowIfCancellationRequested();
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
            finally
            {
                foreach (Observation observation in observations)
                    observation.Dispose();
            }
        }

        internal static async Task<Verification> VerifyAsync(JArray commands, JObject preview, CancellationToken cancellationToken)
        {
            if (
                preview?["contractVersion"]?.Type != JTokenType.Integer
                || preview.Value<int>("contractVersion") != 1
                || preview["signature"]?.Type != JTokenType.String
                || preview["revisions"] is not JArray revisions
                || revisions.Count != commands.Count
            )
                return null;
            var unsigned = (JObject)preview.DeepClone();
            unsigned.Remove("signature");
            if (!SignatureMatches(Sign(unsigned), preview.Value<string>("signature")) || preview.Value<string>("commandsHash") != Hash(commands))
                return null;
            var observations = new List<Observation>(commands.Count);
            bool transferred = false;
            try
            {
                for (int index = 0; index < commands.Count; index++)
                {
                    Observation observation = await ObserveAsync((JObject)commands[index], true, cancellationToken);
                    observations.Add(observation);
                    if (revisions[index]?.Type != JTokenType.String || Hash(observation.State) != revisions[index].Value<string>())
                        return null;
                }
                cancellationToken.ThrowIfCancellationRequested();
                for (int index = 0; index < commands.Count; index++)
                    if (!observations[index].IsCurrent((JObject)commands[index]))
                        return null;
                var verification = new Verification(observations);
                transferred = true;
                return verification;
            }
            catch (Exception exception) when (IsObservationFailure(exception))
            {
                return null;
            }
            finally
            {
                if (!transferred)
                    foreach (Observation observation in observations)
                        observation.Dispose();
            }
        }

        internal static async Task<Observation> MatchAsync(JObject command, JObject preview, int index, CancellationToken cancellationToken)
        {
            Observation observation = null;
            try
            {
                observation = await ObserveAsync(command, true, cancellationToken);
                if (preview["revisions"][index]?.Type == JTokenType.String && Hash(observation.State) == preview["revisions"][index].Value<string>())
                {
                    Observation matched = observation;
                    observation = null;
                    return matched;
                }
                return null;
            }
            catch (Exception exception) when (IsObservationFailure(exception))
            {
                return null;
            }
            finally
            {
                observation?.Dispose();
            }
        }

        private static bool IsObservationFailure(Exception exception) =>
            exception is Incomplete
            || exception is ArgumentException
            || exception is InvalidOperationException
            || exception is IOException
            || exception is UnauthorizedAccessException;

        internal sealed class Verification : IDisposable
        {
            private readonly List<Observation> observations;

            internal Verification(List<Observation> observations) => this.observations = observations;

            internal bool IsCurrent(JArray commands)
            {
                if (commands.Count != observations.Count)
                    return false;
                for (int index = 0; index < commands.Count; index++)
                    if (!observations[index].IsCurrent((JObject)commands[index]))
                        return false;
                return true;
            }

            public void Dispose()
            {
                foreach (Observation observation in observations)
                    observation.Dispose();
                observations.Clear();
            }
        }

        internal sealed class Observation : IDisposable
        {
            internal JObject State;
            internal Dictionary<string, string> FileHashes;
            internal readonly List<FileStream> Readers = new List<FileStream>(2);

            public void Dispose()
            {
                foreach (FileStream reader in Readers)
                    reader.Dispose();
                Readers.Clear();
            }

            internal bool IsCurrent(JObject command)
            {
                try
                {
                    return JToken.DeepEquals(State, Describe(command, ReadHash));
                }
                catch (Exception exception) when (IsObservationFailure(exception))
                {
                    return false;
                }
            }

            private string ReadHash(string path) =>
                FileHashes != null && FileHashes.TryGetValue(path, out string hash) ? hash : throw new Incomplete("target_changed_during_preparation");
        }

        private static async Task<Observation> ObserveAsync(JObject command, bool background, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!background)
                return new Observation { State = Describe(command) };
            var paths = new List<string>(2);
            JObject before = Describe(
                command,
                path =>
                {
                    paths.Add(path);
                    return string.Empty;
                }
            );
            if (paths.Count == 0)
                return new Observation { State = before };
            string assetsRoot = Application.dataPath;
            var observed = new Observation { State = before };
            try
            {
                Dictionary<string, string> ReadFiles()
                {
                    var result = new Dictionary<string, string>(StringComparer.Ordinal);
                    var buffer = new byte[128 * 1024];
                    foreach (string path in paths)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        string checkedPath = SafePathUtility.ResolveWithinRoot(assetsRoot, path);
                        // Preserve read leases through the Editor continuation and whole-batch
                        // observation. Closing here would allow an Editor callback to replace bytes
                        // after their hash but before the compare-before-dispatch check.
                        var stream = File.OpenRead(checkedPath);
                        observed.Readers.Add(stream);
                        using (var sha = SHA256.Create())
                        {
                            int count;
                            while ((count = stream.Read(buffer, 0, buffer.Length)) != 0)
                            {
                                cancellationToken.ThrowIfCancellationRequested();
                                sha.TransformBlock(buffer, 0, count, buffer, 0);
                            }
                            cancellationToken.ThrowIfCancellationRequested();
                            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                            result.Add(path, Convert.ToBase64String(sha.Hash));
                        }
                    }
                    return result;
                }
#if UNITY_EDITOR_WIN
                observed.FileHashes = await Task.Run(ReadFiles, cancellationToken);
#else
                // File sharing outside Windows can be advisory. Keep the existing no-yield
                // observation semantics until native sharing guarantees are verified there.
                observed.FileHashes = await Task.FromResult(ReadFiles());
#endif
                cancellationToken.ThrowIfCancellationRequested();
                // Only AssetMove describes files; retain the original full-byte revision contract.
                before["source"] = observed.FileHashes[paths[0]];
                before["meta"] = observed.FileHashes[paths[1]];
                if (!observed.IsCurrent(command))
                    throw new Incomplete("target_changed_during_preparation");
                return observed;
            }
            catch
            {
                // Task settlement precedes disposal, including cancellation and partial reads.
                observed.Dispose();
                throw;
            }
        }

        private static JObject Describe(JObject command, Func<string, string> fileHash = null)
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
                    state = AssetMove(parameters, fileHash ?? FileHash);
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

        private static JObject AssetMove(JObject parameters, Func<string, string> fileHash)
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
                ["source"] = fileHash(fullSource),
                ["meta"] = fileHash(AssetPathUtility.GetFullAssetPath(source + ".meta")),
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
