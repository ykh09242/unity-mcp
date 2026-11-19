using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.U2D;
using UnityEngine.U2D;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.SpriteAtlasTools
{
    internal sealed class AtlasSettings
    {
        internal sealed class ImporterApi
        {
            private const BindingFlags PublicInstance = BindingFlags.Public | BindingFlags.Instance | BindingFlags.ExactBinding;

            internal static readonly ImporterApi Native = FindNative();

            internal readonly MethodInfo ReadInclude;
            internal readonly MethodInfo WriteInclude;
            internal readonly MethodInfo ReadScale;
            internal readonly MethodInfo WriteScale;
            internal readonly MethodInfo ReadPacking;
            internal readonly MethodInfo WritePacking;
            internal readonly MethodInfo ReadTexture;
            internal readonly MethodInfo WriteTexture;
            internal readonly MethodInfo ReadPlatform;
            internal readonly MethodInfo WritePlatform;
            internal readonly bool UsesSource;
            internal readonly string ReadUnavailableReason;

            internal ImporterApi(Type type, bool usesSource = false)
            {
                UsesSource = usesSource;
                if (UsesSource)
                {
                    ReadInclude = FindMethod(type, "IsIncludeInBuild", typeof(bool));
                    WriteInclude = FindMethod(type, "SetIncludeInBuild", typeof(void), typeof(bool));
                    WriteScale = FindMethod(type, "SetVariantScale", typeof(void), typeof(float));
                    ReadPacking = FindMethod(type, "GetPackingSettings", typeof(SpriteAtlasPackingSettings));
                    WritePacking = FindMethod(type, "SetPackingSettings", typeof(void), typeof(SpriteAtlasPackingSettings));
                    ReadTexture = FindMethod(type, "GetTextureSettings", typeof(SpriteAtlasTextureSettings));
                    WriteTexture = FindMethod(type, "SetTextureSettings", typeof(void), typeof(SpriteAtlasTextureSettings));
                }
                else
                {
                    var include = FindProperty(type, "includeInBuild", typeof(bool));
                    var scale = FindProperty(type, "variantScale", typeof(float));
                    var packing = FindProperty(type, "packingSettings", typeof(SpriteAtlasPackingSettings));
                    var texture = FindProperty(type, "textureSettings", typeof(SpriteAtlasTextureSettings));
                    ReadInclude = include?.GetGetMethod();
                    WriteInclude = include?.GetSetMethod();
                    ReadScale = scale?.GetGetMethod();
                    WriteScale = scale?.GetSetMethod();
                    ReadPacking = packing?.GetGetMethod();
                    WritePacking = packing?.GetSetMethod();
                    ReadTexture = texture?.GetGetMethod();
                    WriteTexture = texture?.GetSetMethod();
                }
                ReadPlatform = FindMethod(type, "GetPlatformSettings", typeof(TextureImporterPlatformSettings), typeof(string));
                WritePlatform = FindMethod(type, "SetPlatformSettings", typeof(void), typeof(TextureImporterPlatformSettings));

                var missing = new List<string>();
                if (ReadInclude == null)
                    missing.Add(UsesSource ? "IsIncludeInBuild" : "includeInBuild");
                if (!UsesSource && ReadScale == null)
                    missing.Add("variantScale");
                if (ReadPacking == null)
                    missing.Add(UsesSource ? "GetPackingSettings" : "packingSettings");
                if (ReadTexture == null)
                    missing.Add(UsesSource ? "GetTextureSettings" : "textureSettings");
                if (ReadPlatform == null)
                    missing.Add("GetPlatformSettings");
                if (missing.Count > 0)
                    ReadUnavailableReason = $"SpriteAtlas V2 requires public {type.Name} readback APIs in this Editor: " + string.Join(", ", missing) + ".";
            }

            private static ImporterApi FindNative()
            {
                var importer = new ImporterApi(typeof(SpriteAtlasImporter));
                if (importer.ReadUnavailableReason == null)
                    return importer;
                var source = new ImporterApi(typeof(SpriteAtlasAsset), true);
                return source.ReadUnavailableReason == null ? source : importer;
            }

            private static PropertyInfo FindProperty(Type type, string name, Type valueType)
            {
                var member = type.GetProperty(name, PublicInstance, null, valueType, Type.EmptyTypes, null);
                return member?.GetGetMethod() != null && member.PropertyType == valueType ? member : null;
            }

            private static MethodInfo FindMethod(Type type, string name, Type returnType, params Type[] arguments)
            {
                var method = type.GetMethod(name, PublicInstance, null, arguments, null);
                return
                    method != null
                    && !method.ContainsGenericParameters
                    && method.ReturnType == returnType
                    && method.GetParameters().Select(parameter => parameter.ParameterType).SequenceEqual(arguments)
                    // Newer Editors retain obsolete source method stubs which no longer read or write settings.
                    && !method.IsDefined(typeof(ObsoleteAttribute), true)
                    ? method
                    : null;
            }

            internal string WriteUnavailableReason(string setting)
            {
                bool writable = setting switch
                {
                    "include_in_build" => WriteInclude != null,
                    "variant_scale" => WriteScale != null,
                    "packing" => WritePacking != null,
                    "texture" => WriteTexture != null,
                    "platforms" => WritePlatform != null,
                    _ => throw new ArgumentOutOfRangeException(nameof(setting)),
                };
                return ReadUnavailableReason ?? (writable ? null : "SpriteAtlas V2 importer has no supported public writer for " + setting + ".");
            }
        }

        internal readonly struct NativeSettings
        {
            private readonly Object target;
            private readonly ImporterApi api;

            internal NativeSettings(Object target, ImporterApi api = null)
            {
                this.target = target;
                this.api = api;
            }

            internal bool Include => target is SpriteAtlas atlas ? atlas.IsIncludeInBuild() : (bool)api.ReadInclude.Invoke(target, null);

            internal float? Scale
            {
                get
                {
                    if (api?.ReadScale != null)
                        return (float)api.ReadScale.Invoke(target, null);

                    // These are the typed scale properties used by Unity's V1 and early V2 inspectors.
                    using var serialized = new SerializedObject(target);
                    using var scale = serialized.FindProperty(target is SpriteAtlas ? "m_EditorData.variantMultiplier" : "m_ImporterData.variantMultiplier");
                    return scale?.propertyType == SerializedPropertyType.Float ? scale.floatValue : (float?)null;
                }
            }

            internal SpriteAtlasPackingSettings Packing =>
                target is SpriteAtlas atlas ? atlas.GetPackingSettings() : (SpriteAtlasPackingSettings)api.ReadPacking.Invoke(target, null);

            internal SpriteAtlasTextureSettings Texture =>
                target is SpriteAtlas atlas ? atlas.GetTextureSettings() : (SpriteAtlasTextureSettings)api.ReadTexture.Invoke(target, null);

            internal TextureImporterPlatformSettings Platform(string profile) =>
                target is SpriteAtlas atlas
                    ? atlas.GetPlatformSettings(profile)
                    : (TextureImporterPlatformSettings)api.ReadPlatform.Invoke(target, new object[] { profile });

            internal void SetInclude(bool value)
            {
                if (target is SpriteAtlas atlas)
                    atlas.SetIncludeInBuild(value);
                else
                    api.WriteInclude.Invoke(target, new object[] { value });
            }

            internal void SetScale(float value)
            {
                if (target is SpriteAtlas atlas)
                    atlas.SetVariantScale(value);
                else
                    api.WriteScale.Invoke(target, new object[] { value });
            }

            internal void SetPacking(SpriteAtlasPackingSettings value)
            {
                if (target is SpriteAtlas atlas)
                    atlas.SetPackingSettings(value);
                else
                    api.WritePacking.Invoke(target, new object[] { value });
            }

            internal void SetTexture(SpriteAtlasTextureSettings value)
            {
                if (target is SpriteAtlas atlas)
                    atlas.SetTextureSettings(value);
                else
                    api.WriteTexture.Invoke(target, new object[] { value });
            }

            internal void SetPlatform(TextureImporterPlatformSettings value)
            {
                if (target is SpriteAtlas atlas)
                    atlas.SetPlatformSettings(value);
                else
                    api.WritePlatform.Invoke(target, new object[] { value });
            }
        }

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
                if (!allowed.Contains(value.Name) || member?.GetGetMethod() == null || member.GetSetMethod() == null)
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
                values[field] = member?.GetGetMethod() == null ? (JToken)JValue.CreateNull() : Scalar(member.GetValue(settings));
            }
            return values;
        }

        private static JToken Scalar(object value)
        {
            if (value is TextureImporterFormat format && format == TextureImporterFormat.Automatic)
                return new JValue(nameof(TextureImporterFormat.Automatic));
            return value.GetType().IsEnum ? new JValue(value.ToString()) : JToken.FromObject(value);
        }

        private static bool Matches(object owner, JObject values)
        {
            if (values == null)
                return true;
            return values.Properties().All(value => JToken.DeepEquals(value.Value, Scalar(owner.GetType().GetProperty(value.Name).GetValue(owner))));
        }

        internal bool Matches(NativeSettings native)
        {
            return (!Include.HasValue || native.Include == Include.Value)
                && (!Scale.HasValue || native.Scale == Scale.Value)
                && (packing == null || Matches(native.Packing, packing))
                && (texture == null || Matches(native.Texture, texture))
                && platforms.All(profile => Matches(native.Platform(profile.Key), profile.Value));
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

        internal void RequireWritable(ImporterApi api)
        {
            void Require(string setting)
            {
                string reason = api?.WriteUnavailableReason(setting);
                if (reason != null)
                    throw new ArgumentException(reason);
            }

            if (Include.HasValue)
                Require("include_in_build");
            if (Scale.HasValue)
                Require("variant_scale");
            if (packing != null)
                Require("packing");
            if (texture != null)
                Require("texture");
            if (platforms.Count > 0)
                Require("platforms");
        }

        internal void RequireScaleReadback(NativeSettings native)
        {
            if (Scale.HasValue && !native.Scale.HasValue)
                throw new ArgumentException("The requested SpriteAtlas variant_scale has no verifiable native readback on this asset.");
        }

        internal void Apply(NativeSettings native)
        {
            if (Include.HasValue)
                native.SetInclude(Include.Value);
            if (Scale.HasValue)
                native.SetScale(Scale.Value);
            if (packing != null)
                native.SetPacking((SpriteAtlasPackingSettings)Patch(native.Packing, packing));
            if (texture != null)
                native.SetTexture((SpriteAtlasTextureSettings)Patch(native.Texture, texture));
            foreach (var profile in platforms)
            {
                var settings = (TextureImporterPlatformSettings)Patch(native.Platform(profile.Key), profile.Value);
                settings.name = profile.Key;
                native.SetPlatform(settings);
            }
        }
    }
}
