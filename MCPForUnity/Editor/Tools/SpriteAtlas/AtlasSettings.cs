using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.U2D;

namespace MCPForUnity.Editor.Tools.SpriteAtlasTools
{
    internal sealed class AtlasSettings
    {
        private static readonly string[] PackingFields = { "padding", "enableRotation", "enableTightPacking", "enableAlphaDilation" };
        private static readonly string[] TextureFields = { "anisoLevel", "filterMode", "generateMipMaps", "readable", "sRGB" };
        private static readonly string[] PlatformFields = { "overridden", "maxTextureSize", "format", "textureCompression", "compressionQuality" };
        private readonly JObject packing;
        private readonly JObject texture;
        private readonly Dictionary<string, JObject> platforms = new Dictionary<string, JObject>(StringComparer.Ordinal);
        internal readonly bool? Include;
        internal readonly float? Scale;

        internal AtlasSettings(JObject values)
        {
            if (values["include_in_build"] != null)
                Include = Bool(values["include_in_build"], "include_in_build");
            if (values["variant_scale"] != null)
            {
                JToken token = values["variant_scale"];
                if (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
                    throw new ArgumentException("variant_scale requires a finite number greater than zero and at most one.");
                double scale = token.Value<double>();
                if (double.IsNaN(scale) || double.IsInfinity(scale) || scale <= 0 || scale > 1 || (float)scale <= 0)
                    throw new ArgumentException("variant_scale requires a finite number greater than zero and at most one.");
                Scale = (float)scale;
            }
            packing = Prepare(values["packing"], typeof(SpriteAtlasPackingSettings), PackingFields);
            texture = Prepare(values["texture"], typeof(SpriteAtlasTextureSettings), TextureFields);
            if (values["platforms"] != null)
            {
                if (!(values["platforms"] is JObject profiles) || profiles.Count == 0 || profiles.Count > 4)
                    throw new ArgumentException("platforms must be a nonempty object with at most four supported profiles.");
                foreach (JProperty profile in profiles.Properties())
                {
                    if (!AtlasAccess.Platforms.Contains(profile.Name))
                        throw new ArgumentException("Unsupported atlas platform profile: " + profile.Name + ".");
                    platforms.Add(profile.Name, Prepare(profile.Value, typeof(TextureImporterPlatformSettings), PlatformFields));
                }
            }
        }

        private static bool Bool(JToken token, string name)
        {
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException(name + " requires a boolean without string or integer coercion.");
            return token.Value<bool>();
        }

        private static JObject Prepare(JToken token, Type type, string[] allowed)
        {
            if (token == null)
                return null;
            if (!(token is JObject values) || values.Count == 0)
                throw new ArgumentException(type.Name + " settings must be a nonempty object.");
            var result = new JObject();
            foreach (JProperty value in values.Properties())
            {
                PropertyInfo member = type.GetProperty(value.Name);
                if (!allowed.Contains(value.Name) || member == null || !member.CanRead || !member.CanWrite)
                    throw new ArgumentException("Unknown or unavailable native atlas setting: " + value.Name + ".");
                Type scalar = member.PropertyType;
                if (scalar == typeof(bool))
                    result[value.Name] = Bool(value.Value, value.Name);
                else if (scalar == typeof(int))
                {
                    if (value.Value.Type != JTokenType.Integer || !int.TryParse(value.Value.ToString(), out int number))
                        throw new ArgumentException(value.Name + " requires a 32-bit integer without coercion.");
                    bool valid =
                        value.Name == "padding" ? new[] { 2, 4, 8 }.Contains(number)
                        : value.Name == "anisoLevel" ? number >= 0 && number <= 16
                        : value.Name == "maxTextureSize" ? number >= 32 && number <= 4096 && (number & (number - 1)) == 0
                        : value.Name == "compressionQuality" && number >= 0 && number <= 100;
                    if (!valid)
                        throw new ArgumentException(value.Name + " is outside the bounded atlas range.");
                    result[value.Name] = number;
                }
                else if (scalar.IsEnum)
                {
                    if (value.Value.Type != JTokenType.String || !Enum.IsDefined(scalar, value.Value.Value<string>()))
                        throw new ArgumentException(value.Name + " requires an exact native enum name supported by this Editor.");
                    string name = value.Value.Value<string>();
                    if (value.Name == "format" && !new[] { "Automatic", "RGBA32", "RGB24", "Alpha8" }.Contains(name))
                        throw new ArgumentException("Atlas format scope supports Automatic, RGBA32, RGB24 and Alpha8 only.");
                    result[value.Name] = name;
                }
                else
                    throw new ArgumentException("Native atlas setting type is unsupported: " + value.Name + ".");
            }
            return result;
        }

        private static object Patch(object owner, JObject values)
        {
            if (values == null)
                return owner;
            foreach (JProperty value in values.Properties())
            {
                PropertyInfo member = owner.GetType().GetProperty(value.Name);
                object scalar = member.PropertyType.IsEnum
                    ? Enum.Parse(member.PropertyType, value.Value.Value<string>())
                    : value.Value.ToObject(member.PropertyType);
                member.SetValue(owner, scalar);
            }
            return owner;
        }

        internal static JObject ReadPacking(SpriteAtlasPackingSettings settings) => Read(settings, PackingFields);

        internal static JObject ReadTexture(SpriteAtlasTextureSettings settings) => Read(settings, TextureFields);

        internal static JObject ReadPlatform(TextureImporterPlatformSettings settings) => Read(settings, PlatformFields);

        private static JObject Read(object settings, string[] fields)
        {
            var values = new JObject();
            foreach (string field in fields)
            {
                PropertyInfo member = settings.GetType().GetProperty(field);
                values[field] = member == null ? (JToken)JValue.CreateNull() : Scalar(member.GetValue(settings));
            }
            return values;
        }

        private static JToken Scalar(object value) => value.GetType().IsEnum ? new JValue(value.ToString()) : JToken.FromObject(value);

        private static bool Matches(object owner, JObject values)
        {
            if (values == null)
                return true;
            return values.Properties().All(value => JToken.DeepEquals(value.Value, Scalar(owner.GetType().GetProperty(value.Name).GetValue(owner))));
        }

        internal bool Matches(SpriteAtlasImporter importer)
        {
#if UNITY_2022_1_OR_NEWER
            return (!Include.HasValue || importer.includeInBuild == Include.Value)
                && (!Scale.HasValue || importer.variantScale == Scale.Value)
                && Matches(importer.packingSettings, packing)
                && Matches(importer.textureSettings, texture)
                && platforms.All(profile => Matches(importer.GetPlatformSettings(profile.Key), profile.Value));
#else
            throw AtlasAccess.ImporterSettingsUnavailable();
#endif
        }

        internal void ValidateVariant(bool variant)
        {
            if (!variant && Scale.HasValue)
                throw new ArgumentException("variant_scale requires an existing variant or an explicit master link.");
            if (variant && (packing != null || texture != null))
                throw new ArgumentException(
                    "Variant packing/texture settings inherit from its master; edit the master explicitly. Variant platform overrides remain supported."
                );
        }

        internal void Apply(SpriteAtlasImporter importer)
        {
#if UNITY_2022_1_OR_NEWER
            if (Include.HasValue)
                importer.includeInBuild = Include.Value;
            if (Scale.HasValue)
                importer.variantScale = Scale.Value;
            if (packing != null)
                importer.packingSettings = (SpriteAtlasPackingSettings)Patch(importer.packingSettings, packing);
            if (texture != null)
                importer.textureSettings = (SpriteAtlasTextureSettings)Patch(importer.textureSettings, texture);
            foreach (var profile in platforms)
            {
                var settings = (TextureImporterPlatformSettings)Patch(importer.GetPlatformSettings(profile.Key), profile.Value);
                settings.name = profile.Key;
                importer.SetPlatformSettings(settings);
            }
            EditorUtility.SetDirty(importer);
            importer.SaveAndReimport();
#else
            throw AtlasAccess.ImporterSettingsUnavailable();
#endif
        }
    }
}
