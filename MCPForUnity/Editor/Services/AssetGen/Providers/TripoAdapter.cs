using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>
    /// Tripo3D model provider. Submits text→3D / image→3D tasks to the OpenAPI task endpoint and
    /// polls for completion. The bearer key is supplied per call and never logged; every error
    /// message is run through <see cref="SecretRedactor"/> before it is surfaced.
    /// </summary>
    public sealed class TripoAdapter : IModelProviderAdapter
    {
        private const string TaskEndpoint = "https://api.tripo3d.ai/v2/openapi/task";

        // Current recommended Tripo model (v3.1). Premium alternative: P1-20260311.
        // internal so the model catalog references it directly (single source of truth, drift-guarded).
        internal const string ModelVersion = "v3.1-20260211";

        public string Id => "tripo";

        public async Task<string> SubmitAsync(ModelGenRequest req, string apiKey, IHttpTransport http, CancellationToken ct)
        {
            if (req == null)
                throw new ArgumentNullException(nameof(req));
            if (http == null)
                throw new ArgumentNullException(nameof(http));

            ValidateOptions(req);

            // Tripo rejects base64 data URIs and needs a multipart upload→token flow for local files,
            // which isn't wired yet — fail clearly rather than silently falling back to text mode.
            bool imageMode = string.Equals(req.Mode, "image", StringComparison.OrdinalIgnoreCase);
            if (imageMode && string.IsNullOrEmpty(req.ImageUrl))
                throw new Exception(
                    "Tripo image input requires a hosted 'image_url'; local 'image_path' upload is not yet supported for Tripo (use Meshy for local-image→3D, or host the image)."
                );

            JObject body;
            bool image = imageMode && !string.IsNullOrEmpty(req.ImageUrl);
            if (image)
            {
                body = new JObject
                {
                    ["type"] = "image_to_model",
                    ["model_version"] = string.IsNullOrEmpty(req.Model) ? ModelVersion : req.Model,
                    ["file"] = new JObject { ["type"] = "url", ["url"] = req.ImageUrl },
                };
            }
            else
            {
                body = new JObject
                {
                    ["type"] = "text_to_model",
                    ["prompt"] = req.Prompt ?? string.Empty,
                    ["model_version"] = string.IsNullOrEmpty(req.Model) ? ModelVersion : req.Model,
                };
            }

            // PBR defaults to true and forces textures on, so both flags must agree.
            body["texture"] = req.Texture;
            body["pbr"] = req.Texture;

            var spec = new HttpRequestSpec
            {
                Method = "POST",
                Url = TaskEndpoint,
                ContentType = "application/json",
                Body = Encoding.UTF8.GetBytes(body.ToString(Formatting.None)),
            };
            spec.Headers["Authorization"] = "Bearer " + apiKey;

            HttpResult res = await http.SendAsync(spec, ct);
            JObject json = ParseAndValidate(res, apiKey, "submit");

            string taskId = json["data"]?["task_id"]?.ToString();
            if (string.IsNullOrEmpty(taskId))
            {
                throw new Exception(SecretRedactor.Scrub("Tripo submit returned no task_id: " + ProviderHttp.Truncate(res?.Text), apiKey));
            }
            return taskId;
        }

        public async Task<ProviderPollResult> PollAsync(string providerJobId, string apiKey, IHttpTransport http, CancellationToken ct)
        {
            if (string.IsNullOrEmpty(providerJobId))
                throw new ArgumentNullException(nameof(providerJobId));
            if (http == null)
                throw new ArgumentNullException(nameof(http));

            var spec = new HttpRequestSpec { Method = "GET", Url = TaskEndpoint + "/" + providerJobId };
            spec.Headers["Authorization"] = "Bearer " + apiKey;

            HttpResult res = await http.SendAsync(spec, ct);
            JObject json = ParseAndValidate(res, apiKey, "poll");
            JObject data = json["data"] as JObject ?? new JObject();

            var result = new ProviderPollResult { State = MapState(data["status"]?.ToString()) };

            JToken progressTok = data["progress"];
            if (progressTok != null && progressTok.Type != JTokenType.Null)
            {
                result.Progress = Mathf.Clamp01(progressTok.Value<float>() / 100f);
            }

            if (result.State == ProviderPollState.Succeeded)
            {
                result.Progress = 1f;
                result.DownloadUrl = ExtractDownloadUrl(data);
                if (string.IsNullOrEmpty(result.DownloadUrl))
                {
                    result.State = ProviderPollState.Failed;
                    result.Error = "Tripo reported success but no model URL was present in the response.";
                }
                else
                {
                    if (!Uri.TryCreate(result.DownloadUrl, UriKind.Absolute, out Uri uri))
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
                            result.Error = "Tripo returned a non-GLB artifact; model conversion is not supported by this integration.";
                        }
                        else
                            result.ResultExt = extension.Length == 0 ? null : extension;
                    }
                }
            }
            else if (result.State == ProviderPollState.Failed)
            {
                string err = data["error"]?.ToString() ?? data["message"]?.ToString() ?? json["message"]?.ToString() ?? "Tripo task failed.";
                result.Error = SecretRedactor.Scrub(err, apiKey);
            }

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
            if (!string.IsNullOrEmpty(declaredExtension) && !string.Equals(declaredExtension.TrimStart('.'), "glb", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Tripo artifact format does not match the supported GLB result.");
            const string error = "Tripo returned an invalid GLB 2.0 container; no model file was written.";
            if (bytes == null || bytes.Length < 20 || UInt32At(bytes, 0) != 0x46546C67 || UInt32At(bytes, 4) != 2 || UInt32At(bytes, 8) != bytes.Length)
                throw new InvalidDataException(error);
            int offset = 12;
            int index = 0;
            bool binarySeen = false;
            while (offset < bytes.Length)
            {
                if (bytes.Length - offset < 8)
                    throw new InvalidDataException(error);
                uint length = UInt32At(bytes, offset);
                uint type = UInt32At(bytes, offset + 4);
                if (length % 4 != 0 || length > bytes.Length - offset - 8)
                    throw new InvalidDataException(error);
                if (index == 0)
                {
                    if (type != 0x4E4F534A)
                        throw new InvalidDataException(error);
                    try
                    {
                        string json = new UTF8Encoding(false, true).GetString(bytes, offset + 8, (int)length);
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
                offset += 8 + (int)length;
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

            return UrlOf(output?["pbr_model"])
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
        private static JObject ParseAndValidate(HttpResult res, string apiKey, string phase)
        {
            string text = ProviderHttp.BodyText(res);

            JObject json = null;
            if (!string.IsNullOrEmpty(text))
            {
                try
                {
                    json = JObject.Parse(text);
                }
                catch { /* non-JSON body; handled below */ }
            }

            bool httpOk = res?.Ok == true;

            int code = 0;
            JToken codeTok = json?["code"];
            if (codeTok != null && codeTok.Type != JTokenType.Null)
            {
                try
                {
                    code = codeTok.Value<int>();
                }
                catch
                {
                    code = -1;
                }
            }

            if (!httpOk || code != 0)
            {
                string detail = json?["message"]?.ToString() ?? json?["error"]?.ToString() ?? ProviderHttp.Truncate(text);
                throw new Exception(SecretRedactor.Scrub($"Tripo {phase} failed (status={res?.Status}, code={code}): {detail}", apiKey));
            }

            return json ?? new JObject();
        }
    }
}
