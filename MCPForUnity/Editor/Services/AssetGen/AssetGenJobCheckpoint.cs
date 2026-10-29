using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen
{
    // Only continuation inputs belong here. Never add prompts, keys, provider documents or download URLs.
    internal sealed class AssetGenJobCheckpoint
    {
        internal const int MaxActive = 64;
        internal const int MaxCheckpointBytes = 16 * 1024;
        internal const int MaxRecordBytes = 1024 * 1024;
        internal const int MaxIndexBytes = 16 * 1024;
        internal const int MaxDepth = 16;
        internal const long MaxFileBytes = Http.AssetDownloadTransport.MaxDownloadBytes;
        internal const double MaxDurationSeconds = 2 * 60 * 60;

        [JsonProperty("schema_version")]
        public int Version = 1;
        public string JobId;
        public string Kind;
        public string Provider;
        public long Revision;
        public string Phase = "preparing";
        public double StartedAt;
        public double Deadline;
        public string Receipt;
        public string OutputFolder;
        public string Name;
        public string Extension;
        public string Subfolder;
        public bool AsSprite;
        public bool Transparent;
        public string MarketplaceUid;
        public string FalOutputField;
        public MeshyRecoveryState Meshy;
        public string SavedPath;
        public long SavedLength;
        public string SavedHash;
        public bool MetadataPresent;
        public long MetadataLength;
        public string MetadataHash;
        public string RegisteredGuid;

        // Digest only: request contents are retained separately in Editor SessionState.
        public string SubmissionIntentHash;
        public string SubmissionRefusalCode;

        private static readonly HashSet<string> Phases = new(StringComparer.Ordinal)
        {
            "preparing",
            "submitting",
            "polling",
            "refine_submitting",
            "refining",
            "downloading",
            "writing",
            "saved",
            "importing",
            "nonresumable",
            "terminal",
        };

        internal static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

        internal static bool SafeId(string value, int max = 256) =>
            !string.IsNullOrEmpty(value)
            && value.Length <= max
            && value.All(c => c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-');

        internal static bool Hex(string value, int length) =>
            value != null && value.Length == length && value.All(c => c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F' || c >= '0' && c <= '9');

        internal static bool AssetPath(string value, bool allowRoot = false)
        {
            if (allowRoot && value == "Assets")
                return true;
            if (
                string.IsNullOrEmpty(value)
                || value.Length > 2048
                || !value.StartsWith("Assets/", StringComparison.Ordinal)
                || value.Any(char.IsControl)
                || value.IndexOfAny(new[] { '\\', ':', '?', '*', '"', '<', '>', '|' }) >= 0
            )
                return false;
            string[] parts = value.Split('/');
            return parts.Length <= 64 && parts.All(part => part.Length > 0 && part != "." && part != "..");
        }

        internal static bool FalReceipt(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Length > 2048 || value.Any(char.IsControl) || value.Contains("%") || value.Contains("\\"))
                return false;
            if (
                !Uri.TryCreate(value, UriKind.Absolute, out var uri)
                || uri.Scheme != "https"
                || uri.Host != "queue.fal.run"
                || !uri.IsDefaultPort
                || uri.UserInfo.Length != 0
                || uri.Query.Length != 0
                || uri.Fragment.Length != 0
            )
                return false;
            string[] parts = uri.AbsolutePath.Split('/');
            return parts.Length == 5
                && parts[0] == ""
                && SafeId(parts[1])
                && SafeId(parts[2])
                && parts[3] == "requests"
                && SafeId(parts[4])
                // Uri normalizes dot segments; require the accepted path bytes to be the inspected route.
                && value.Substring(value.IndexOf('/', "https://".Length)) == uri.AbsolutePath;
        }

        internal bool HasSafeReceipt() =>
            Provider == "fal" ? FalReceipt(Receipt)
            : Provider == "sketchfab" ? SafeId(MarketplaceUid, 128)
            : (Provider == "tripo" || Provider == "meshy") && SafeId(Receipt);

        internal bool Validate(string id, double now)
        {
            if (
                Version != 1
                || !Hex(JobId, 32)
                || JobId != id
                || Revision < 0
                || !Phases.Contains(Phase)
                || !Finite(StartedAt)
                || !Finite(Deadline)
                || StartedAt < 0
                || StartedAt > now
                || Deadline < StartedAt
                || Deadline - StartedAt > MaxDurationSeconds + 0.001
                || !AssetPath(OutputFolder, true)
                || string.IsNullOrEmpty(Name)
                || Name.Length > 48
                || Name.Any(c => !char.IsLetterOrDigit(c) && c != '_' && c != '-')
                || !AssetGenJobManager.IsAllowedResultExtension(Kind, Extension)
            )
                return false;
            bool pair =
                Kind == "model" && (Provider == "tripo" || Provider == "meshy" || Provider == "fal")
                || Kind == "image" && (Provider == "fal" || Provider == "openrouter")
                || Kind == "audio" && Provider == "fal"
                || Kind == "marketplace" && Provider == "sketchfab";
            if (
                !pair
                || Subfolder
                    != (
                        Kind == "model" ? "Models"
                        : Kind == "image" ? "Images"
                        : Kind == "audio" ? "Audio"
                        : "Sketchfab"
                    )
            )
                return false;
            if (MarketplaceUid != null && !SafeId(MarketplaceUid, 128))
                return false;
            if (Receipt != null && !HasSafeReceipt())
                return false;
            if (FalOutputField != null && FalOutputField != "model_glb" && FalOutputField != "model_urls.glb")
                return false;
            if (Meshy != null && !Meshy.Validate())
                return false;
            if (
                SubmissionIntentHash != null && !Hex(SubmissionIntentHash, 64)
                || SubmissionRefusalCode != null
                    && (!SubmissionRefusalCode.StartsWith("submission_", StringComparison.Ordinal) || SafeCode(SubmissionRefusalCode) == null)
                || SubmissionIntentHash != null && SubmissionRefusalCode != null
            )
                return false;
            if (Phase == "preparing" && SubmissionIntentHash != null && (Receipt != null || Meshy != null || FalOutputField != null || SavedPath != null))
                return false;
            if (Phase == "polling" || Phase == "refining" || Phase == "refine_submitting" || Phase == "downloading")
            {
                if (!HasSafeReceipt() || Provider == "meshy" && Meshy == null || Provider == "fal" && Kind == "model" && FalOutputField == null)
                    return false;
                if (Phase == "refining" && (Meshy == null || !SafeId(Meshy.RefineId)))
                    return false;
                if ((Phase == "refining" || Phase == "refine_submitting") && (Provider != "meshy" || Meshy.IsImage || !Meshy.WantTexture))
                    return false;
            }
            // These typed fields are serialized in every phase. Inactive fields must not bypass bounds/privacy checks.
            bool savedIdentityPresent =
                SavedPath != null
                || SavedLength != 0
                || SavedHash != null
                || MetadataPresent
                || MetadataLength != 0
                || MetadataHash != null
                || RegisteredGuid != null;
            if (savedIdentityPresent || Phase == "writing" || Phase == "saved" || Phase == "importing")
            {
                if (
                    !AssetPath(SavedPath)
                    || !SavedPath.StartsWith(OutputFolder.TrimEnd('/') + "/", StringComparison.Ordinal)
                    || Path.GetExtension(SavedPath) != "." + Extension
                    || SavedLength <= 0
                    || SavedLength > MaxFileBytes
                    || !Hex(SavedHash, 64)
                )
                    return false;
                if (MetadataPresent && (MetadataLength <= 0 || MetadataLength > MaxFileBytes || !Hex(MetadataHash, 64)))
                    return false;
                if (!MetadataPresent && (MetadataLength != 0 || MetadataHash != null || !string.IsNullOrEmpty(RegisteredGuid)))
                    return false;
                if (!string.IsNullOrEmpty(RegisteredGuid) && !Hex(RegisteredGuid, 32))
                    return false;
            }
            return true;
        }

        internal static JObject ParseRecord(string json)
        {
            if (string.IsNullOrEmpty(json) || json.Length > MaxRecordBytes || Encoding.UTF8.GetByteCount(json) > MaxRecordBytes)
                throw new InvalidDataException("record_size_invalid");
            using var input = new StringReader(json);
            using var reader = new JsonTextReader(input) { MaxDepth = MaxDepth, DateParseHandling = DateParseHandling.None };
            var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new InvalidDataException("record_trailing_data");
            return value;
        }

        internal static AssetGenJobCheckpoint Read(JObject record, string id, double now)
        {
            JToken token = record["_resume_v1"];
            if (token == null)
                return null;
            if (token.Type != JTokenType.Object || Encoding.UTF8.GetByteCount(token.ToString(Formatting.None)) > MaxCheckpointBytes)
                throw new InvalidDataException("checkpoint_size_invalid");
            var serializer = JsonSerializer.Create(new JsonSerializerSettings { MissingMemberHandling = MissingMemberHandling.Error, MaxDepth = MaxDepth });
            var checkpoint = token.ToObject<AssetGenJobCheckpoint>(serializer);
            if (checkpoint == null || !checkpoint.Validate(id, now))
                throw new InvalidDataException("checkpoint_invalid");
            return checkpoint;
        }

        // Persist only known schema, never opaque public error strings or extension objects.
        internal static JObject Project(AssetGenJob job, AssetGenJobCheckpoint checkpoint)
        {
            var record = new JObject
            {
                ["JobId"] = SafeId(job.JobId) ? job.JobId : null,
                ["Kind"] = Known(job.Kind, "model", "image", "audio", "marketplace"),
                ["Provider"] = Known(job.Provider, "tripo", "meshy", "fal", "openrouter", "sketchfab"),
                ["Action"] = Known(job.Action, "generate", "import"),
                ["State"] = (int)job.State,
                ["Progress"] = Finite(job.Progress) ? job.Progress : 0,
                ["Format"] = AssetGenJobManager.IsAllowedResultExtension(job.Kind, job.Format) ? job.Format : null,
                ["TargetSize"] = Finite(job.TargetSize) ? job.TargetSize : 0,
                ["AnimationType"] = Known(job.AnimationType?.Trim().ToLowerInvariant(), "none", "generic", "humanoid", "human", "legacy"),
                ["AssetPath"] = AssetPath(job.AssetPath) ? job.AssetPath : null,
                ["AssetGuid"] = Hex(job.AssetGuid, 32) ? job.AssetGuid : null,
                ["ModelImportStage"] = Known(job.ModelImportStage, "saved", "registered", "usable"),
                ["ModelImportErrorCode"] = ImportCode(job.ModelImportErrorCode),
                ["ArchiveModelPath"] = RelativeArchivePath(job.ArchiveModelPath) ? job.ArchiveModelPath : null,
                ["ArchiveSelection"] = ArchiveEvidence(job.ArchiveSelection),
                ["RecoveryState"] = Known(job.RecoveryState, "available", "resuming", "not_submitted", "outcome_unknown", "unavailable", "terminal"),
                ["RecoveryCode"] = SafeCode(job.RecoveryCode),
                ["ProviderTaskId"] = SafeId(job.ProviderTaskId) ? job.ProviderTaskId : null,
                ["ProviderPhase"] = Known(job.ProviderPhase, "submit", "poll", "preview", "refine", "download", "saved", "import", "terminal"),
                ["Error"] = job.State == AssetGenJobState.Failed ? ErrorSummary(job.RecoveryCode) : null,
            };
            if (checkpoint != null)
            {
                if (!checkpoint.Validate(job.JobId, double.MaxValue))
                    throw new InvalidDataException("checkpoint_invalid");
                var token = JObject.FromObject(checkpoint);
                if (Encoding.UTF8.GetByteCount(token.ToString(Formatting.None)) > MaxCheckpointBytes)
                    throw new InvalidDataException("checkpoint_size_invalid");
                record["_resume_v1"] = token;
            }
            if (Encoding.UTF8.GetByteCount(record.ToString(Formatting.None)) > MaxRecordBytes)
                throw new InvalidDataException("record_size_invalid");
            return record;
        }

        internal static string SafeCode(string value) =>
            Known(
                value,
                "reload_before_submission",
                "submission_intent_unavailable",
                "submission_intent_invalid",
                "submission_intent_too_large",
                "submission_intent_unsupported",
                "submission_url_input_not_retained",
                "submission_input_changed",
                "submission_output_changed",
                "submission_configuration_changed",
                "submission_authorization_revoked",
                "submission_outcome_unknown",
                "refine_outcome_unknown",
                "local_write_outcome_unknown",
                "import_outcome_unknown",
                "legacy_reload_interrupted",
                "synchronous_result_unavailable_after_reload",
                "receipt_not_resumable",
                "reconnect_key_unavailable",
                "checkpoint_invalid",
                "checkpoint_clock_uncertain",
                "checkpoint_write_failed",
                "job_timeout",
                "generation_failed",
                "saved_asset_identity_changed",
                "saved_asset_missing",
                "canceled",
                "resumed",
                "completed"
            );

        internal static string ErrorSummary(string code) =>
            SafeCode(code) switch
            {
                "job_timeout" => "Timed out within the original generation execution budget.",
                "reconnect_key_unavailable" => "The provider key needed to reconnect is unavailable.",
                "reload_before_submission" => "Editor reload interrupted the job before provider submission.",
                "submission_intent_unavailable" => "The original submission intent is unavailable in this Editor session; no provider request was submitted.",
                "submission_intent_invalid" => "The retained submission intent is invalid; no provider request was submitted.",
                "submission_intent_too_large" =>
                    "The original submission exceeds the bounded session-intent limit; no provider request was submitted after reload.",
                "submission_intent_unsupported" => "The original submission cannot be reconstructed safely; no provider request was submitted after reload.",
                "submission_url_input_not_retained" => "Remote image URLs are not retained across reload; no provider request was submitted after reload.",
                "submission_input_changed" =>
                    "The original local input is missing or its content or asset identity changed; no provider request was submitted.",
                "submission_output_changed" => "The original output folder is no longer valid; no provider request was submitted.",
                "submission_configuration_changed" => "The provider model configuration changed; no provider request was submitted.",
                "submission_authorization_revoked" => "The generation/import tool is no longer authorized; no provider request was submitted.",
                "saved_asset_identity_changed" => "Saved asset identity changed; retained output was not imported.",
                "saved_asset_missing" => "Saved output is missing; no replacement was written.",
                "checkpoint_write_failed" => "Recovery checkpoint could not be stored; further work stopped.",
                "checkpoint_invalid" => "Recovery metadata is invalid; no work was resumed.",
                "checkpoint_clock_uncertain" => "The clock moved backwards across restart; the execution budget could not be verified and no work was resumed.",
                "generation_failed" => "Generation failed. Provider diagnostic payloads are not retained across reload.",
                _ => "Editor reload recovery is unavailable or the prior outcome is unknown. Inspect retained work before retrying.",
            };

        private static string Known(string value, params string[] choices) => choices.Contains(value, StringComparer.Ordinal) ? value : null;

        private static bool RelativeArchivePath(string value) =>
            !string.IsNullOrEmpty(value)
            && Encoding.UTF8.GetByteCount(value) <= 512
            && !value.StartsWith("/", StringComparison.Ordinal)
            && !value.Any(char.IsControl)
            && value.IndexOfAny(new[] { ':', '\\', '?', '*', '"', '<', '>', '|' }) < 0
            && value.Split('/').Length <= 32
            && value.Split('/').All(p => p.Length > 0 && p != "." && p != "..");

        private static string ImportCode(string value) =>
            Known(
                value,
                "model_import_failed",
                "unusable_model",
                "missing_gltf_importer",
                "model_file_missing",
                "invalid_target_size",
                "model_not_registered",
                "model_validation_limit",
                "unsupported_model_importer",
                "target_size_not_achieved",
                "target_size_out_of_range",
                "normalization_invalid_bounds",
                "normalization_bounds_limit",
                "normalization_output_conflict",
                "normalization_variant_failed",
                "normalization_variant_unsupported",
                "archive_selector_requires_zip",
                "archive_model_path_invalid",
                "archive_model_not_found",
                "archive_no_model",
                "archive_extraction_failed",
                "archive_selection_limit",
                "archive_entry_conflict",
                "archive_output_conflict"
            );

        private static JObject ArchiveEvidence(JObject input)
        {
            if (input == null)
                return null;
            var output = new JObject();
            foreach (string key in new[] { "archive_path", "selected_asset_path", "extraction_root" })
                if (input.Property(key) != null)
                    output[key] = input[key]?.Type == JTokenType.String && AssetPath((string)input[key]) ? (string)input[key] : null;
            foreach (string key in new[] { "requested_path", "selected_path" })
                if (input.Property(key) != null)
                    output[key] = input[key]?.Type == JTokenType.String && RelativeArchivePath((string)input[key]) ? (string)input[key] : null;
            string Text(string key) => input[key]?.Type == JTokenType.String ? (string)input[key] : null;
            if (input.Property("mode") != null)
                output["mode"] = Known(Text("mode"), "default", "explicit");
            if (input.Property("policy") != null)
                output["policy"] = Known(Text("policy"), "builtin_then_ordinal");
            if (input.Property("extraction") != null)
                output["extraction"] = Known(Text("extraction"), "not_started", "complete", "failed");
            if (input.Property("selection_error_code") != null)
                output["selection_error_code"] = ImportCode(Text("selection_error_code"));
            if (input.Property("limit") != null)
                output["limit"] = Known(Text("limit"), "entry_path", "candidate_count");
            var candidates = new JArray();
            bool complete = input["candidates_complete"]?.Type == JTokenType.Boolean && (bool)input["candidates_complete"];
            if (input["candidates"] is JArray values)
            {
                if (values.Count > Import.ModelArchiveSelection.MaxCandidates)
                    complete = false;
                foreach (var value in values.Take(Import.ModelArchiveSelection.MaxCandidates))
                {
                    if (value.Type == JTokenType.String && RelativeArchivePath((string)value))
                        candidates.Add((string)value);
                    else
                        complete = false;
                }
            }
            output["candidates_complete"] = complete;
            output["candidates"] = candidates;
            output["candidate_count"] = candidates.Count;
            return output;
        }
    }

    internal sealed class MeshyRecoveryState
    {
        public string Format;
        public bool IsImage;
        public bool WantTexture;
        public string Model;
        public string RefineId;

        internal bool Validate() =>
            new[] { "glb", "fbx", "obj", "usdz", "blend", "stl" }.Contains(Format)
            && AssetGenJobCheckpoint.SafeId(Model, 200)
            && (RefineId == null || AssetGenJobCheckpoint.SafeId(RefineId))
            && (RefineId == null || !IsImage && WantTexture);
    }
}
