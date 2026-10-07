using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "manage_audio",
        AutoRegister = false,
        Group = "core",
        Description = "Issue play or stop requests to an existing scene AudioSource in Play mode. Play optionally assigns an AudioClip asset path after validation; success does not guarantee audible output."
    )]
    public static class ManageAudio
    {
        public static object HandleCommand(JObject parameters)
        {
            if (parameters == null)
                return new ErrorResponse("Parameters cannot be null.");

            JToken actionToken = parameters["action"];
            if (actionToken?.Type != JTokenType.String)
                return new ErrorResponse("'action' must be play or stop.");
            string action = actionToken.ToString().Trim().ToLowerInvariant();
            if (action != "play" && action != "stop")
                return new ErrorResponse("'action' must be play or stop.");

            JToken target = parameters["target"];
            if (target == null || (target.Type != JTokenType.String && target.Type != JTokenType.Integer) || string.IsNullOrWhiteSpace(target.ToString()))
                return new ErrorResponse("'target' must be a GameObject name, hierarchy path or integer instance ID.");
            if (target.Type == JTokenType.Integer && !int.TryParse(target.ToString(), out _))
                return new ErrorResponse("'target' instance ID is outside the supported integer range.");

            JToken searchToken = parameters["searchMethod"] ?? parameters["search_method"];
            if (searchToken != null && searchToken.Type != JTokenType.Null && searchToken.Type != JTokenType.String)
                return new ErrorResponse("'search_method' must be by_id, by_name, by_path or by_id_or_name_or_path.");
            string searchMethod = searchToken?.Type == JTokenType.String ? searchToken.ToString().ToLowerInvariant() : null;
            if (string.IsNullOrEmpty(searchMethod) || searchMethod == "by_id_or_name_or_path")
                searchMethod =
                    int.TryParse(target.ToString(), out _) ? "by_id"
                    : target.ToString().Contains("/") ? "by_path"
                    : "by_name";
            if (searchMethod != "by_id" && searchMethod != "by_name" && searchMethod != "by_path")
                return new ErrorResponse("'search_method' must be by_id, by_name, by_path or by_id_or_name_or_path.");

            JToken clipToken = parameters["clip"];
            bool hasClip = clipToken != null && clipToken.Type != JTokenType.Null;
            if (action == "stop" && hasClip)
                return new ErrorResponse("'clip' is only supported for play.");
            string clipPath = null;
            if (hasClip)
            {
                if (clipToken.Type != JTokenType.String || string.IsNullOrWhiteSpace(clipToken.ToString()))
                    return new ErrorResponse("'clip' must be a nonempty AudioClip asset path.");
                clipPath = AssetPathUtility.NormalizeSeparators(clipToken.ToString());
                if (!clipPath.StartsWith("Assets/", StringComparison.Ordinal) && !clipPath.StartsWith("Packages/", StringComparison.Ordinal))
                    return new ErrorResponse("'clip' must be an Assets/ or Packages/ asset path.");
                foreach (string segment in clipPath.Split('/'))
                    if (
                        segment.Length == 0
                        || segment == "."
                        || segment == ".."
                        || segment.IndexOfAny(new[] { ':', '\0', '*', '?', '"', '<', '>', '|', '\r', '\n' }) >= 0
                    )
                        return new ErrorResponse("'clip' contains an invalid asset path segment.");
            }

            if (!EditorApplication.isPlaying)
                return new ErrorResponse("Audio play/stop requires Play mode. Enter Play mode before issuing this request.");

            try
            {
                // Include inactive objects so stop can address sources that were disabled after playback.
                GameObject gameObject = GameObjectLookup.FindByTarget(target, searchMethod, includeInactive: true);
                if (gameObject == null)
                    return new ErrorResponse($"Target GameObject '{target}' was not found.");
                if (EditorUtility.IsPersistent(gameObject) || !gameObject.scene.IsValid() || !gameObject.scene.isLoaded)
                    return new ErrorResponse("'target' must be a GameObject in a loaded scene.");
                AudioSource source = gameObject.GetComponent<AudioSource>();
                if (source == null)
                    return new ErrorResponse($"GameObject '{gameObject.name}' has no AudioSource.");

                if (action == "play")
                {
                    if (!source.isActiveAndEnabled)
                        return new ErrorResponse("AudioSource must be enabled on an active GameObject to play.");
                    AudioClip clip = hasClip
                        ? AssetDatabase.LoadAssetAtPath<AudioClip>(AssetPathUtility.GetAssetReferencePath(clipPath, allowPackages: true))
                        : source.clip;
                    if (clip == null)
                        return new ErrorResponse(hasClip ? $"AudioClip was not found at '{clipPath}'." : "AudioSource has no assigned AudioClip.");
                    if (clip.loadState == AudioDataLoadState.Failed)
                        return new ErrorResponse("AudioClip data failed to load.");

                    // Play loads unloaded clips and waits for background loading; do not force a synchronous preload.
                    if (hasClip)
                        source.clip = clip;
                    source.Play();
                }
                else
                {
                    source.Stop();
                }

                return new SuccessResponse(
                    $"Audio {action} request issued.",
                    new
                    {
                        action,
                        target = gameObject.name,
                        instanceID = gameObject.GetInstanceIDCompat(),
                    }
                );
            }
            catch (Exception exception)
            {
                McpLog.Error($"[ManageAudio] Audio {action} request failed: {exception.Message}");
                return new ErrorResponse($"Audio {action} request failed: {exception.Message}");
            }
        }
    }
}
