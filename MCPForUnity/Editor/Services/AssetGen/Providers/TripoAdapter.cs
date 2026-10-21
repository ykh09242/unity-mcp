using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>
    /// Tripo v3 generation and polling, including explicitly consented local image uploads.
    /// Keys, upload tokens and provider response bodies never enter public error messages.
    /// </summary>
    public sealed class TripoAdapter : IModelProviderAdapter
    {
        private const string ApiRoot = "https://openapi.tripo3d.ai/v3";
        private const int MaxEnvelopeBytes = 64 * 1024;

        // Current recommended Tripo model (v3.1). Premium alternative: P1-20260311.
        // internal so the model catalog references it directly (single source of truth, drift-guarded).
        internal const string ModelVersion = "v3.1-20260211";

        public string Id => "tripo";

        public Task<string> SubmitAsync(ModelGenRequest req, string apiKey, IHttpTransport http, CancellationToken ct) =>
            SubmitAsync(req, apiKey, http, ct, null);

        internal async Task<string> SubmitAsync(ModelGenRequest req, string apiKey, IHttpTransport http, CancellationToken ct, Action admitGeneration)
        {
            if (req == null)
                throw new ArgumentNullException(nameof(req));
            if (http == null)
                throw new ArgumentNullException(nameof(http));
            ct.ThrowIfCancellationRequested();
            ValidateOptions(req);
            bool image = string.Equals(req.Mode, "image", StringComparison.OrdinalIgnoreCase);
            string input = null;
            string uploadToken = null;
            if (image && !string.IsNullOrWhiteSpace(req.ImageUrl))
            {
                if (
                    !Uri.TryCreate(req.ImageUrl, UriKind.Absolute, out Uri source)
                    || (source.Scheme != Uri.UriSchemeHttps && source.Scheme != Uri.UriSchemeHttp)
                )
                    throw new InvalidDataException("Tripo hosted image input must be an HTTP or HTTPS URL.");
                input = req.ImageUrl;
            }
            else if (image)
            {
                if (!req.AllowImageUpload)
                    throw new InvalidOperationException("Tripo local image upload requires explicit allow_image_upload=true consent.");
                using var upload = TripoLocalImageUpload.Prepare(req.ImagePath, ct);
                HttpRequestSpec uploadRequest = new HttpRequestSpec
                {
                    Method = "POST",
                    Url = ApiRoot + "/files",
                    ContentType = upload.ContentType,
                    Body = upload.Body,
                };
                uploadRequest.Headers["Authorization"] = "Bearer " + apiKey;
                try
                {
                    ct.ThrowIfCancellationRequested();
                    HttpResult uploaded = await SendAsync(http, uploadRequest, ct, "upload");
                    JObject response = ParseAndValidate(uploaded, "upload");
                    uploadToken = SafeIdentifier((response["data"] as JObject)?["file_token"], "upload");
                    input = uploadToken;
                }
                finally
                {
                    // Release this adapter's multipart references. A transport/native handler or
                    // a test recorder may retain its own copy; no universal memory zeroing claim.
                    uploadRequest = null;
                }
            }
            var body = new JObject
            {
                ["model"] = string.IsNullOrEmpty(req.Model) ? ModelVersion : req.Model,
                ["texture"] = req.Texture,
                ["pbr"] = req.Texture,
            };
            if (image)
                body["input"] = input;
            else
                body["prompt"] = req.Prompt ?? string.Empty;
            var spec = new HttpRequestSpec
            {
                Method = "POST",
                Url = ApiRoot + (image ? "/generation/image-to-model" : "/generation/text-to-model"),
                ContentType = "application/json",
                Body = ProviderHttp.SerializeRequest(body),
            };
            spec.Headers["Authorization"] = "Bearer " + apiKey;
            // The callback is the manager's existing original-deadline/epoch/ownership and
            // durable submission admission. It runs after upload await, before the paid POST.
            ct.ThrowIfCancellationRequested();
            if (uploadToken != null)
                admitGeneration?.Invoke();
            ct.ThrowIfCancellationRequested();
            HttpResult result = await SendAsync(http, spec, ct, "generation");
            JObject generation = ParseAndValidate(result, "generation");
            string taskId = SafeIdentifier((generation["data"] as JObject)?["task_id"], "generation");
            if (uploadToken != null && string.Equals(taskId, uploadToken, StringComparison.Ordinal))
                throw new InvalidDataException("Tripo generation returned an invalid task receipt.");
            return taskId;
        }

        public async Task<ProviderPollResult> PollAsync(string providerJobId, string apiKey, IHttpTransport http, CancellationToken ct)
        {
            if (string.IsNullOrEmpty(providerJobId))
                throw new ArgumentNullException(nameof(providerJobId));
            if (http == null)
                throw new ArgumentNullException(nameof(http));
            ct.ThrowIfCancellationRequested();
            string receipt = SafeIdentifier(new JValue(providerJobId), "poll");
            var spec = new HttpRequestSpec { Method = "GET", Url = ApiRoot + "/tasks/" + Uri.EscapeDataString(receipt) };
            spec.Headers["Authorization"] = "Bearer " + apiKey;
            JObject json = ParseAndValidate(await SendAsync(http, spec, ct, "poll"), "poll");
            JObject data = json["data"] as JObject;
            if (data == null)
                throw new InvalidDataException("Tripo poll returned an invalid response.");
            var result = new ProviderPollResult { State = MapState(data["status"]?.Type == JTokenType.String ? (string)data["status"] : null) };
            JToken progress = data["progress"];
            if (progress?.Type == JTokenType.Integer || progress?.Type == JTokenType.Float)
            {
                try
                {
                    double value = progress.Value<double>();
                    if (!double.IsInfinity(value) && !double.IsNaN(value))
                        result.Progress = Mathf.Clamp01((float)(value / 100));
                }
                catch (Exception error) when (error is OverflowException || error is InvalidCastException || error is FormatException)
                { /* Optional malformed progress remains zero. */
                }
            }
            if (result.State == ProviderPollState.Succeeded)
            {
                result.Progress = 1f;
                result.DownloadUrl = ExtractDownloadUrl(data);
                if (string.IsNullOrEmpty(result.DownloadUrl))
                {
                    result.State = ProviderPollState.Failed;
                    result.Error = "Tripo reported success without a model URL.";
                }
                else if (!Uri.TryCreate(result.DownloadUrl, UriKind.Absolute, out Uri uri))
                {
                    result.State = ProviderPollState.Failed;
                    result.Error = "Tripo returned an invalid model URL.";
                }
                else
                {
                    string extension = Path.GetExtension(uri.AbsolutePath).TrimStart('.').ToLowerInvariant();
                    if (extension.Length != 0 && extension != "glb")
                    {
                        result.State = ProviderPollState.Failed;
                        result.Error = "Tripo returned a non-GLB artifact; conversion is not supported.";
                    }
                    else
                        result.ResultExt = extension.Length == 0 ? null : extension;
                }
            }
            else if (result.State == ProviderPollState.Failed)
                result.Error = "Tripo task failed (code=" + NumericCode(data["code"] ?? data["error_code"]) + ").";
            return result;
        }

        internal static void ValidateOptions(ModelGenRequest req)
        {
            string format = (req.Format ?? "glb").Trim().TrimStart('.');
            if (format.Length != 0 && !string.Equals(format, "glb", StringComparison.OrdinalIgnoreCase))
                throw new NotSupportedException(
                    "Tripo generation supports GLB only. Other formats require a separate conversion task, which this integration does not submit."
                );
            req.Format = "glb";
            if (!string.IsNullOrWhiteSpace(req.Tier))
                throw new NotSupportedException("Tripo does not support the 'tier' option in this integration; omit it.");
            string model = string.IsNullOrEmpty(req.Model) ? ModelVersion : req.Model;
            if (!req.Texture && model != ModelVersion && model != "v3.0-20250812" && model != "P1-20260311")
                throw new NotSupportedException(
                    "Tripo texture=false is supported here only for v3.0-20250812, v3.1-20260211, and P1-20260311. Choose one of those models."
                );
        }

        // Container validation only: a valid GLB need not contain a usable mesh or be importable
        // by the installed Unity packages. Never infer those properties from its extension.
        internal static string ValidateModelBytes(byte[] bytes, string declaredExtension)
        {
            using var stream = new MemoryStream(bytes ?? Array.Empty<byte>(), false);
            return ValidateModelStream(stream, declaredExtension);
        }

        internal static string ValidateModelStream(Stream stream, string declaredExtension)
        {
            if (!string.IsNullOrEmpty(declaredExtension) && !string.Equals(declaredExtension.TrimStart('.'), "glb", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Tripo artifact format does not match the supported GLB result.");
            const string error = "Tripo returned an invalid GLB 2.0 container; no model file was written.";
            if (stream == null || !stream.CanSeek || stream.Length < 20 || stream.Length > uint.MaxValue)
                throw new InvalidDataException(error);
            stream.Position = 0;
            byte[] ReadExactly(int count)
            {
                var result = new byte[count];
                int offset = 0;
                while (offset < count)
                {
                    int read = stream.Read(result, offset, count - offset);
                    if (read == 0)
                        throw new InvalidDataException(error);
                    offset += read;
                }
                return result;
            }
            byte[] header = ReadExactly(12);
            if (UInt32At(header, 0) != 0x46546C67 || UInt32At(header, 4) != 2 || UInt32At(header, 8) != stream.Length)
                throw new InvalidDataException(error);
            long offset = 12;
            int index = 0;
            bool binarySeen = false;
            while (offset < stream.Length)
            {
                if (stream.Length - offset < 8)
                    throw new InvalidDataException(error);
                stream.Position = offset;
                byte[] chunkHeader = ReadExactly(8);
                uint length = UInt32At(chunkHeader, 0);
                uint type = UInt32At(chunkHeader, 4);
                if (length % 4 != 0 || length > stream.Length - offset - 8)
                    throw new InvalidDataException(error);
                if (index == 0)
                {
                    // Only metadata is decoded. The binary chunk can span GiB without a byte array.
                    if (type != 0x4E4F534A || length > 64 * 1024 * 1024)
                        throw new InvalidDataException(error);
                    try
                    {
                        string json = new UTF8Encoding(false, true).GetString(ReadExactly((int)length));
                        if (!IsStrictJson(json))
                            throw new InvalidDataException(error);
                        JObject document = JObject.Parse(json, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                        if (
                            (string)document["asset"]?["version"] != "2.0"
                            || (document["asset"]?["minVersion"] != null && (string)document["asset"]["minVersion"] != "2.0")
                        )
                            throw new InvalidDataException(error);
                    }
                    catch (Exception ex) when (ex is JsonException || ex is DecoderFallbackException || ex is InvalidCastException)
                    {
                        throw new InvalidDataException(error);
                    }
                }
                else if (type == 0x4E4F534A)
                    throw new InvalidDataException(error);
                else if (type == 0x004E4942)
                {
                    if (index != 1 || binarySeen)
                        throw new InvalidDataException(error);
                    binarySeen = true;
                }
                offset += 8L + length;
                index++;
            }
            return "glb";
        }

        // Json.NET accepts JavaScript extensions (comments, single quotes, trailing commas).
        // Gate only this provider's GLB JSON against RFC 8259 before reading its metadata.
        private static bool IsStrictJson(string json)
        {
            int position = 0;
            bool Digit() => position < json.Length && json[position] >= '0' && json[position] <= '9';
            void Whitespace()
            {
                while (position < json.Length && (json[position] == ' ' || json[position] == '\t' || json[position] == '\r' || json[position] == '\n'))
                    position++;
            }
            bool Take(char c)
            {
                if (position == json.Length || json[position] != c)
                    return false;
                position++;
                return true;
            }
            bool Text()
            {
                if (!Take('"'))
                    return false;
                while (position < json.Length)
                {
                    char c = json[position++];
                    if (c == '"')
                        return true;
                    if (c < 0x20)
                        return false;
                    if (c != '\\')
                        continue;
                    if (position == json.Length)
                        return false;
                    c = json[position++];
                    if (c == 'u')
                    {
                        for (int i = 0; i < 4; i++)
                        {
                            if (position == json.Length)
                                return false;
                            char hex = json[position++];
                            if (!(hex >= '0' && hex <= '9') && !(hex >= 'a' && hex <= 'f') && !(hex >= 'A' && hex <= 'F'))
                                return false;
                        }
                    }
                    else if (c != '"' && c != '\\' && c != '/' && c != 'b' && c != 'f' && c != 'n' && c != 'r' && c != 't')
                        return false;
                }
                return false;
            }
            bool Number()
            {
                Take('-');
                if (!Take('0'))
                {
                    if (!Digit() || json[position] == '0')
                        return false;
                    while (Digit())
                        position++;
                }
                if (Take('.'))
                {
                    if (!Digit())
                        return false;
                    while (Digit())
                        position++;
                }
                if (Take('e') || Take('E'))
                {
                    if (!Take('+'))
                        Take('-');
                    if (!Digit())
                        return false;
                    while (Digit())
                        position++;
                }
                return true;
            }
            bool Value(int depth)
            {
                if (depth > 64)
                    return false;
                Whitespace();
                if (Take('{'))
                {
                    Whitespace();
                    if (Take('}'))
                        return true;
                    do
                    {
                        Whitespace();
                        if (!Text())
                            return false;
                        Whitespace();
                        if (!Take(':') || !Value(depth + 1))
                            return false;
                        Whitespace();
                        if (Take('}'))
                            return true;
                    } while (Take(','));
                    return false;
                }
                if (Take('['))
                {
                    Whitespace();
                    if (Take(']'))
                        return true;
                    do
                    {
                        if (!Value(depth + 1))
                            return false;
                        Whitespace();
                        if (Take(']'))
                            return true;
                    } while (Take(','));
                    return false;
                }
                if (position < json.Length && json[position] == '"')
                    return Text();
                return Literal("true") || Literal("false") || Literal("null") || Number();
            }
            bool Literal(string literal)
            {
                if (json.Length - position < literal.Length || string.CompareOrdinal(json, position, literal, 0, literal.Length) != 0)
                    return false;
                position += literal.Length;
                return true;
            }
            bool valid = Value(0);
            Whitespace();
            return valid && position == json.Length;
        }

        private static uint UInt32At(byte[] bytes, int offset) =>
            (uint)bytes[offset] | ((uint)bytes[offset + 1] << 8) | ((uint)bytes[offset + 2] << 16) | ((uint)bytes[offset + 3] << 24);

        private static ProviderPollState MapState(string status)
        {
            switch ((status ?? string.Empty).ToLowerInvariant())
            {
                case "success":
                case "succeeded":
                    return ProviderPollState.Succeeded;
                case "failed":
                case "error":
                case "cancelled":
                case "canceled":
                case "banned":
                case "expired":
                    return ProviderPollState.Failed;
                case "running":
                case "processing":
                    return ProviderPollState.Running;
                default:
                    return ProviderPollState.Queued;
            }
        }

        /// <summary>
        /// Resolve the model download URL, defensive about Tripo's nested output shapes. Prefer a
        /// textured / PBR model; fall back to the base model. Accepts both the newer flat form
        /// (<c>output.pbr_model</c> = url string) and the older nested form
        /// (<c>result.pbr_model.url</c> = object with a url field).
        /// </summary>
        private static string ExtractDownloadUrl(JObject data)
        {
            JObject output = data["output"] as JObject;
            JObject resultObj = data["result"] as JObject;

            return UrlOf(output?["model_url"])
                ?? UrlOf(output?["pbr_model"])
                ?? UrlOf(resultObj?["pbr_model"])
                ?? UrlOf(output?["model"])
                ?? UrlOf(resultObj?["model"])
                ?? UrlOf(output?["base_model"])
                ?? UrlOf(resultObj?["base_model"]);
        }

        /// <summary>A field may be a plain URL string or an object carrying a "url" property.</summary>
        private static string UrlOf(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type == JTokenType.String)
            {
                string s = token.ToString();
                return string.IsNullOrEmpty(s) ? null : s;
            }
            if (token is JObject obj)
            {
                string u = obj["url"]?.ToString();
                return string.IsNullOrEmpty(u) ? null : u;
            }
            return null;
        }

        /// <summary>
        /// Validate transport success + Tripo's body-level <c>code</c> (0 == ok), parse the JSON,
        /// and throw a redacted exception otherwise.
        /// </summary>
        private static JObject ParseAndValidate(HttpResult result, string phase)
        {
            JObject json = null;
            try
            {
                if (result?.Body?.Length > MaxEnvelopeBytes || result?.Text?.Length > MaxEnvelopeBytes)
                    throw new InvalidDataException("Tripo " + phase + " response exceeds the envelope limit.");
                string text = ProviderHttp.BodyText(result);
                if (string.IsNullOrEmpty(text) || Encoding.UTF8.GetByteCount(text) > MaxEnvelopeBytes)
                    throw new InvalidDataException("Tripo " + phase + " returned an invalid response.");
                using var source = new StringReader(text);
                using var reader = new JsonTextReader(source) { MaxDepth = 16, DateParseHandling = DateParseHandling.None };
                json = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new InvalidDataException("Tripo " + phase + " returned an invalid response.");
            }
            catch (Exception error) when (error is JsonException || error is DecoderFallbackException)
            {
                throw new InvalidDataException("Tripo " + phase + " returned an invalid response.");
            }
            int code = NumericCode(json?["code"]);
            if (result?.Ok != true || code != 0)
                throw new InvalidOperationException("Tripo " + phase + " failed (status=" + (result?.Status ?? 0) + ", code=" + code + ").");
            return json;
        }

        private static int NumericCode(JToken token) =>
            token?.Type == JTokenType.Integer && int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int code) ? code : -1;

        private static string SafeIdentifier(JToken token, string phase)
        {
            if (token?.Type != JTokenType.String)
                throw new InvalidDataException("Tripo " + phase + " returned an invalid identifier.");
            string value = (string)token;
            if (string.IsNullOrEmpty(value) || value.Length > 256)
                throw new InvalidDataException("Tripo " + phase + " returned an invalid identifier.");
            foreach (char character in value)
                if (
                    !(character >= 'a' && character <= 'z')
                    && !(character >= 'A' && character <= 'Z')
                    && !(character >= '0' && character <= '9')
                    && character != '_'
                    && character != '-'
                )
                    throw new InvalidDataException("Tripo " + phase + " returned an invalid identifier.");
            return value;
        }

        private static async Task<HttpResult> SendAsync(IHttpTransport http, HttpRequestSpec request, CancellationToken token, string phase)
        {
            token.ThrowIfCancellationRequested();
            try
            {
                return await http.SendAsync(request, token);
            }
            catch (OperationCanceledException)
            {
                throw new OperationCanceledException("Tripo request canceled.", token);
            }
            catch (Exception)
            {
                // Transport exceptions can contain URLs, request bodies or provider secrets.
                throw new InvalidOperationException("Tripo " + phase + " request failed; remote outcome may be unknown.");
            }
        }
    }
}
