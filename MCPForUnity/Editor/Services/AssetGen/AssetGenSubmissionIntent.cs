using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.AssetGen
{
    // Request contents survive an assembly reload only in this Editor session. The disk journal
    // stores a digest, never this document. In particular, credentials and remote image URLs have
    // no representation here. A missing session document cannot be reconstructed from defaults.
    internal sealed class AssetGenSubmissionIntent
    {
        internal const string KeyPrefix = "MCPForUnity.AssetGen.Submission.";
        internal const int MaxBytes = 64 * 1024;
        public int Version = 1;
        public string JobId;
        public string Kind;
        public string Provider;
        public ModelGenRequest Model;
        public ImageGenRequest Image;
        public AudioGenRequest Audio;
        public string ConfigurationHash;
        public string InputPath;
        public long InputLength;
        public string InputHash;
        public string InputGuid;

        [JsonIgnore]
        internal string Refusal;

        internal static T Copy<T>(T request)
        {
            T copy = JsonConvert.DeserializeObject<T>(JsonConvert.SerializeObject(request));
            if (request is ModelGenRequest model && copy is ModelGenRequest copiedModel)
                copiedModel.CatalogEntry = CopyCatalog(model.CatalogEntry);
            if (request is ImageGenRequest image && copy is ImageGenRequest copiedImage)
                copiedImage.CatalogEntry = CopyCatalog(image.CatalogEntry);
            if (request is AudioGenRequest audio && copy is AudioGenRequest copiedAudio)
                copiedAudio.CatalogEntry = CopyCatalog(audio.CatalogEntry);
            return copy;
        }

        private static ModelEntry CopyCatalog(ModelEntry entry) => entry == null ? null : JObject.FromObject(entry).ToObject<ModelEntry>();

        internal static AssetGenSubmissionIntent Capture(AssetGenJob job, object request)
        {
            var intent = new AssetGenSubmissionIntent
            {
                JobId = job.JobId,
                Kind = job.Kind,
                Provider = job.Provider,
            };
            try
            {
                if (request is ModelGenRequest model)
                    intent.Model = Copy(model);
                else if (request is ImageGenRequest image)
                    intent.Image = Copy(image);
                else if (request is AudioGenRequest audio)
                    intent.Audio = Copy(audio);
                if (!string.IsNullOrEmpty(intent.Model?.ImageUrl) || !string.IsNullOrEmpty(intent.Image?.ImageUrl))
                    throw new InvalidDataException("submission_url_input_not_retained");
                if ((intent.Model?.Prompt ?? intent.Image?.Prompt ?? intent.Audio?.Prompt)?.Length > 16 * 1024)
                    throw new InvalidDataException("submission_intent_too_large");
                string path = intent.Model?.ImagePath ?? intent.Image?.ImagePath;
                if (!string.IsNullOrEmpty(path))
                {
                    if (
                        !LocalImage.ResolveExisting(path, out string absolute, out _) || !AssetGenPaths.TryGetAssetsRelativePath(absolute, out intent.InputPath)
                    )
                        throw new InvalidDataException("submission_input_changed");
                    intent.InputLength = new FileInfo(absolute).Length;
                    intent.InputHash = HashFile(absolute, intent.InputLength);
                    intent.InputGuid = AssetDatabase.AssetPathToGUID(intent.InputPath) ?? "";
                    if (intent.Model != null)
                        intent.Model.ImagePath = intent.InputPath;
                    if (intent.Image != null)
                        intent.Image.ImagePath = intent.InputPath;
                }
                intent.ConfigurationHash = Configuration(
                    intent.ModelId,
                    intent.Model?.CatalogEntry ?? intent.Image?.CatalogEntry ?? intent.Audio?.CatalogEntry
                );
                if (!intent.Valid(job))
                    throw new InvalidDataException("submission_intent_unsupported");
                string json = JsonConvert.SerializeObject(intent);
                if (Encoding.UTF8.GetByteCount(json) > MaxBytes)
                    throw new InvalidDataException("submission_intent_too_large");
            }
            catch (Exception error)
            {
                intent = new AssetGenSubmissionIntent
                {
                    JobId = job.JobId,
                    Kind = job.Kind,
                    Provider = job.Provider,
                    Refusal =
                        error is InvalidDataException && AssetGenJobCheckpoint.SafeCode(error.Message) != null
                            ? error.Message
                            : "submission_intent_unsupported",
                };
            }
            return intent;
        }

        internal void Store(AssetGenJobCheckpoint checkpoint)
        {
            checkpoint.SubmissionRefusalCode = Refusal;
            if (Refusal != null)
                return;
            string json = JsonConvert.SerializeObject(this);
            if (Encoding.UTF8.GetByteCount(json) > MaxBytes)
                throw new InvalidDataException("submission_intent_too_large");
            SessionState.SetString(KeyPrefix + JobId, json);
            checkpoint.SubmissionIntentHash = Hash(Encoding.UTF8.GetBytes(json));
        }

        internal static AssetGenSubmissionIntent Read(AssetGenJob job, AssetGenJobCheckpoint checkpoint)
        {
            string json = SessionState.GetString(KeyPrefix + job.JobId, "");
            if (string.IsNullOrEmpty(json))
                throw new InvalidDataException("submission_intent_unavailable");
            if (json.Length > MaxBytes || Encoding.UTF8.GetByteCount(json) > MaxBytes)
                throw new InvalidDataException("submission_intent_too_large");
            if (Hash(Encoding.UTF8.GetBytes(json)) != checkpoint.SubmissionIntentHash)
                throw new InvalidDataException("submission_intent_invalid");
            try
            {
                using var input = new StringReader(json);
                using var reader = new JsonTextReader(input) { MaxDepth = 8, DateParseHandling = DateParseHandling.None };
                var document = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new InvalidDataException("submission_intent_invalid");
                var serializer = JsonSerializer.Create(new JsonSerializerSettings { MissingMemberHandling = MissingMemberHandling.Error, MaxDepth = 8 });
                var intent = document.ToObject<AssetGenSubmissionIntent>(serializer);
                if (intent == null || !intent.Valid(job))
                    throw new InvalidDataException("submission_intent_invalid");
                return intent;
            }
            catch (JsonException)
            {
                throw new InvalidDataException("submission_intent_invalid");
            }
        }

        private string ModelId => Model?.Model ?? Image?.Model ?? Audio?.Model;

        private bool Valid(AssetGenJob job)
        {
            if (Version != 1 || JobId != job.JobId || Kind != job.Kind || Provider != job.Provider || !AssetGenJobCheckpoint.Hex(ConfigurationHash, 64))
                return false;
            bool shape =
                Kind == "model" && Model != null && Image == null && Audio == null
                || Kind == "image" && Image != null && Model == null && Audio == null
                || Kind == "audio" && Audio != null && Model == null && Image == null
                || Kind == "marketplace" && Model == null && Image == null && Audio == null;
            if (!shape || !string.IsNullOrEmpty(Model?.ImageUrl) || !string.IsNullOrEmpty(Image?.ImageUrl))
                return false;
            if (
                Kind != "marketplace"
                && (string.IsNullOrEmpty(ModelId) || ModelId.Length > 512 || ModelId.Any(c => char.IsControl(c) || c == '?' || c == '#' || c == ':'))
            )
                return false;
            string prompt = Model?.Prompt ?? Image?.Prompt ?? Audio?.Prompt;
            if (prompt?.Length > 16 * 1024 || !AssetGenJobCheckpoint.Finite(Model?.TargetSize ?? 0) || !AssetGenJobCheckpoint.Finite(Audio?.Duration ?? 0))
                return false;
            string path = Model?.ImagePath ?? Image?.ImagePath;
            return string.IsNullOrEmpty(path)
                ? InputPath == null && InputLength == 0 && InputHash == null && InputGuid == null
                : path == InputPath
                    && AssetGenJobCheckpoint.AssetPath(InputPath)
                    && InputLength >= 0
                    && InputLength <= LocalImage.MaxImageBytes
                    && AssetGenJobCheckpoint.Hex(InputHash, 64)
                    && (InputGuid == "" || AssetGenJobCheckpoint.Hex(InputGuid, 32));
        }

        internal void ValidateCurrent(AssetGenJobCheckpoint checkpoint, ModelEntry verified = null)
        {
            if (!AssetGenPaths.TryGetAssetsFolder(checkpoint.OutputFolder, out string output) || output != checkpoint.OutputFolder)
                throw new InvalidDataException("submission_output_changed");
            if (Configuration(ModelId) != ConfigurationHash || verified != null && Configuration(ModelId, verified) != ConfigurationHash)
                throw new InvalidDataException("submission_configuration_changed");
            if (InputPath == null)
                return;
            try
            {
                if (
                    !LocalImage.ResolveExisting(InputPath, out string absolute, out _)
                    || HashFile(absolute, InputLength) != InputHash
                    || (AssetDatabase.AssetPathToGUID(InputPath) ?? "") != InputGuid
                )
                    throw new InvalidDataException("submission_input_changed");
            }
            catch (Exception)
            {
                throw new InvalidDataException("submission_input_changed");
            }
        }

        // Hash only request-affecting catalog fields. Descriptions, prices and refresh timestamps
        // must not prevent a continuation; endpoint/input/output schema changes must prevent it.
        private static string Configuration(string id, ModelEntry verified = null)
        {
            ModelEntry entry = verified ?? AssetGenModelCatalog.Find(id);
            var value = entry == null ? new JObject() : JObject.FromObject(entry);
            foreach (string field in new[] { "Label", "UseCase", "PriceLabel", "CommercialNote", "LicenseType", "ModelUrl", "VerifiedAt" })
                value.Remove(field);
            return Hash(Encoding.UTF8.GetBytes(value.ToString(Formatting.None)));
        }

        private static string HashFile(string path, long length)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (length < 0 || length > LocalImage.MaxImageBytes || stream.Length != length)
                throw new InvalidDataException("submission_input_changed");
            using var sha = SHA256.Create();
            byte[] buffer = new byte[64 * 1024];
            long remaining = length;
            while (remaining > 0)
            {
                int count = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                if (count == 0)
                    throw new InvalidDataException("submission_input_changed");
                sha.TransformBlock(buffer, 0, count, buffer, 0);
                remaining -= count;
            }
            if (stream.ReadByte() != -1)
                throw new InvalidDataException("submission_input_changed");
            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
        }

        private static string Hash(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        internal static void Forget(string id) => SessionState.EraseString(KeyPrefix + id);
    }
}
