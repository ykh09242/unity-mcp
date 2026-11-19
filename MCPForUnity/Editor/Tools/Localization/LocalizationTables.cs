using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEditor;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Localization
{
    public static partial class ManageLocalization
    {
        private static readonly Type TableType = Kind("Tables.LocalizationTable") ?? Kind("Tables.LocalizedTable") ?? Kind("LocalizedTable");
        private static readonly Type StringType = Kind("Tables.StringTable") ?? Kind("StringTable");
        private static readonly Type AssetType = Kind("Tables.AssetTable") ?? Kind("LocalizedAssetTable");
        private static readonly Type SharedType = Kind("Tables.SharedTableData") ?? Kind("Tables.KeyDatabase") ?? Kind("KeyDatabase");
        private static readonly Type IdType = SharedType
            ?.GetProperty("Entries")
            ?.PropertyType.GetGenericArguments()
            .FirstOrDefault()
            ?.GetProperty("Id")
            ?.PropertyType;
        private static readonly bool HasSmartStrings = Kind("Tables.StringTableEntry")?.GetProperty("IsSmart") != null;
        private static readonly bool HasSingleFallback =
            Kind("Locale")?.GetProperty("FallbackLocale")?.CanWrite == true && Kind("Metadata.FallbackLocale") == null;

        private static Object Shared(Object table)
        {
            if (SharedType == null)
                return null;
            Object shared = (Object)Get(table, TableType.GetProperty("SharedData") == null ? "Keys" : "SharedData");
            if (!EditorUtility.IsPersistent(shared))
                throw new ArgumentException("Shared key data must be a persistent asset.");
            return shared;
        }

        private static int BoundedCount(int count)
        {
            if (count > MaxKeys)
                throw new ArgumentException("table_limit: table or shared data exceeds 100000 entries.");
            return count;
        }

        // In the first string table format only GetEntry is public. Read the serialized IDs
        // in order to bound the work and detect duplicates that its lookup dictionary hides.
        private static (long? Id, string Name)[] OriginalStringKeys(Object table)
        {
            using var serialized = new SerializedObject(table);
            var entries = serialized.FindProperty("m_StringTableEntries");
            if (entries == null || !entries.isArray)
                throw new InvalidOperationException("unsupported_package_api: missing serialized string entries.");
            var keys = new (long?, string)[BoundedCount(entries.arraySize)];
            for (int index = 0; index < keys.Length; index++)
            {
                var key = entries.GetArrayElementAtIndex(index).FindPropertyRelative("m_Id");
                keys[index] = IdType == null ? ((long?)null, key.stringValue) : ((long?)key.longValue, null);
            }
            return keys;
        }

        private static (long? Id, string Name)[] TableKeys(Object table)
        {
            Object shared = Shared(table);
            if (shared != null)
            {
                var entries = (IList)Get(shared, "Entries");
                BoundedCount(entries.Count);
                return entries
                    .Cast<object>()
                    .Select(k => ((long?)Convert.ToInt64(Get(k, "Id"), CultureInfo.InvariantCulture), (string)Get(k, "Key")))
                    .ToArray();
            }
            if (StringType?.IsInstanceOfType(table) == true)
                return OriginalStringKeys(table);
            var map = (IDictionary)Get(table, "AssetMap");
            BoundedCount(map.Count);
            return map.Keys.Cast<string>().OrderBy(k => k, StringComparer.Ordinal).Select(k => ((long?)null, k)).ToArray();
        }

        private static IEnumerable<long> LocalIds(Object table)
        {
            if (IdType == null)
                return Enumerable.Empty<long>();
            if (table is IEnumerable pairs)
                return pairs.Cast<object>().Select(pair => Convert.ToInt64(Get(pair, "Key"), CultureInfo.InvariantCulture));
            if (StringType?.IsInstanceOfType(table) == true)
                return OriginalStringKeys(table).Select(key => key.Id.Value);
            return ((IDictionary)Get(table, "AssetMap")).Keys.Cast<object>().Select(k => Convert.ToInt64(k, CultureInfo.InvariantCulture));
        }

        private static object NativeKey(long? id, string name) => IdType == null ? name : Convert.ChangeType(id.Value, IdType, CultureInfo.InvariantCulture);

        private static object Entry(Object table, long? id, string name)
        {
            object key = NativeKey(id, name);
            if (!(table is IEnumerable) && StringType?.IsInstanceOfType(table) != true)
                return ((IDictionary)Get(table, "AssetMap"))[key];
            return Call(table, "GetEntry", new[] { key.GetType() }, key);
        }

        private static string EntryValue(object entry, bool isString)
        {
            if (entry == null)
                return null;
            if (isString)
                return (string)GetNullable(entry, HasSmartStrings ? "Value" : "Translated");
            // Newer asset entries store the complete subasset address in LocalizedValue.
            if (entry.GetType().GetProperty("Guid") != null)
                return (string)GetNullable(entry, entry.GetType().GetProperty("LocalizedValue") == null ? "Guid" : "LocalizedValue");
            return (string)entry.GetType().GetField("guid").GetValue(entry);
        }

        private static string AssetGuid(object entry) => entry.GetType().GetProperty("Guid") == null ? EntryValue(entry, false) : (string)Get(entry, "Guid");

        private static IEnumerable<string> AssetGuids(Object table)
        {
            IEnumerable values = table is IEnumerable ? (IEnumerable)Get(table, "Values") : ((IDictionary)Get(table, "AssetMap")).Values;
            return values.Cast<object>().Where(entry => !string.IsNullOrEmpty(EntryValue(entry, false))).Select(AssetGuid);
        }

        private static string IdText(long? id) => id?.ToString(CultureInfo.InvariantCulture);
    }
}
