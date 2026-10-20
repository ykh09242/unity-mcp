using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using Unity.IO.LowLevel.Unsafe;
using UnityEditor.PackageManager;
using NativeObject = MCPForUnity.Editor.Tools.Profiler.MemorySnapshotComparison.NativeObject;
using Snapshot = MCPForUnity.Editor.Tools.Profiler.MemorySnapshotComparison.Snapshot;

namespace MCPForUnity.Editor.Tools.Profiler
{
    // Exact inspected package ABI only. No file-length or heap-reservation approximation of object memory.
    internal static class MemorySnapshotAnalysisOps
    {
        private const string SupportedPackage = "1.1.12";
        private const long MaximumFileBytes = 512L * 1024 * 1024;
        private const long MaximumReadBytes = 64L * 1024 * 1024;

        private sealed class SnapshotProblem : Exception
        {
            internal readonly string Code;
            internal readonly JArray Columns;

            internal SnapshotProblem(string code, string message, JArray columns = null)
                : base(message)
            {
                Code = code;
                Columns = columns;
            }
        }

        private sealed class Backend
        {
            internal Type ReaderType;
            internal Type EntryType;
            internal MethodInfo Read;
            internal string Version;
            internal string Error;
            internal int ProfileTargetInfoBytes;
            internal int ProfileTargetSessionOffset;

            internal Backend()
            {
                ReaderType = Type.GetType("Unity.MemoryProfiler.Editor.Format.QueriedSnapshot.FileReader, Unity.MemoryProfiler.Editor");
                if (ReaderType == null)
                {
                    Error = "memory_profiler_package_missing";
                    return;
                }
                Version = PackageInfo.FindForAssembly(ReaderType.Assembly)?.version;
                if (Version != SupportedPackage)
                {
                    Error = "unsupported_reader_package_version";
                    return;
                }
                EntryType = ReaderType.Assembly.GetType("Unity.MemoryProfiler.Editor.Format.QueriedSnapshot.EntryType");
                Read = ReaderType
                    .GetMethods()
                    .SingleOrDefault(method =>
                        method.Name == "Read" && method.GetParameters().Length == 5 && method.GetParameters()[1].ParameterType == typeof(long)
                    );
                if (
                    EntryType == null
                    || Read == null
                    || !Read.GetParameters()[3].ParameterType.IsEnum
                    || ReaderType.GetMethod("Open", new[] { typeof(string) }) == null
                    || ReaderType.GetProperty("FormatVersionNumeric") == null
                    || ReaderType.GetMethod("GetEntryCount") == null
                    || ReaderType.GetMethod("GetEntryFormat") == null
                    || ReaderType.GetMethod("GetSizeForEntryRange") == null
                    || !typeof(IDisposable).IsAssignableFrom(ReaderType)
                    || !typeof(IDisposable).IsAssignableFrom(Read.ReturnType)
                    || Read.ReturnType.GetProperty("Result") == null
                    || !typeof(IEnumerable<byte>).IsAssignableFrom(Read.ReturnType.GetProperty("Result").PropertyType)
                    || !typeof(IDisposable).IsAssignableFrom(Read.ReturnType.GetProperty("Result").PropertyType)
                )
                {
                    Error = "unsupported_reader_abi";
                    return;
                }
                Type targetInfo = ReaderType.Assembly.GetType("Unity.MemoryProfiler.Editor.Format.ProfileTargetInfo");
                if (targetInfo == null || targetInfo.GetField("SessionGUID")?.FieldType != typeof(uint))
                {
                    Error = "unsupported_reader_abi";
                    return;
                }
                // StructLayout.Size is a minimum, not sizeof: the inspected current fields extend
                // through byte520 despite the attribute's512. Match the actual pinned package ABI.
                ProfileTargetInfoBytes = Marshal.SizeOf(targetInfo);
                ProfileTargetSessionOffset = Marshal.OffsetOf(targetInfo, "SessionGUID").ToInt32();
                if (ProfileTargetInfoBytes != 520 || ProfileTargetSessionOffset != 0)
                    Error = "unsupported_reader_abi";
            }
        }

        internal static object Status(JObject parameters)
        {
            var backend = new Backend();
            return new SuccessResponse(
                "Read the explicitly supported snapshot reader capability.",
                new
                {
                    reader_supported = backend.Error == null,
                    package_version = backend.Version,
                    required_package_version = SupportedPackage,
                    reason = backend.Error,
                    minimum_format = 11,
                    maximum_format = 18,
                    native_object_bytes = backend.Error == null,
                    profile_target_info_bytes = backend.ProfileTargetInfoBytes,
                    profile_target_session_offset = backend.ProfileTargetSessionOffset,
                    managed_heap_object_bytes = false,
                    retained_bytes = false,
                }
            );
        }

        internal static object Compare(JObject parameters)
        {
            var p = new ToolParams(parameters);
            if (!ProfilerPathUtility.TryResolve(p, "snapshot_a", true, false, out string pathA, out ErrorResponse pathError))
                return pathError;
            if (!ProfilerPathUtility.TryResolve(p, "snapshot_b", true, false, out string pathB, out pathError))
                return pathError;
            int objectLimit = MemoryAnalysisParams.Integer(parameters, "object_limit", 1, 256, 128);
            int maximum = MemoryAnalysisParams.Integer(parameters, "max_objects", 1, 200000, 200000);
            string prefix = "";
            if (parameters["name_prefix"] != null && parameters["name_prefix"].Type != JTokenType.Null)
            {
                if (parameters["name_prefix"].Type != JTokenType.String || parameters["name_prefix"].Value<string>().Length > 128)
                    throw new ArgumentException("name_prefix must be a string of at most 128 characters.");
                prefix = parameters["name_prefix"].Value<string>();
            }
            var backend = new Backend();
            if (backend.Error != null)
                return new ErrorResponse(
                    "snapshot_reader_unavailable",
                    new
                    {
                        reason = backend.Error,
                        actual_package_version = backend.Version,
                        required_package_version = SupportedPackage,
                    }
                );
            try
            {
                Snapshot a = Load(backend, pathA, maximum, prefix);
                Snapshot b = Load(backend, pathB, maximum, prefix);
                var comparison = MemorySnapshotComparison.Compare(a, b, objectLimit);
                return new SuccessResponse(
                    "Compared actual native-object snapshot records, not snapshot file lengths.",
                    new JObject
                    {
                        ["byte_semantics"] = "native_object_size",
                        ["reader_package_version"] = backend.Version,
                        ["snapshot_a"] = new JObject
                        {
                            ["path"] = a.Path,
                            ["format_version"] = a.Format,
                            ["session_id"] = a.Session,
                            ["selected_object_count"] = a.Objects.Count,
                        },
                        ["snapshot_b"] = new JObject
                        {
                            ["path"] = b.Path,
                            ["format_version"] = b.Format,
                            ["session_id"] = b.Session,
                            ["selected_object_count"] = b.Objects.Count,
                        },
                        ["name_prefix"] = prefix,
                        ["types"] = comparison.Types,
                        ["object_delta"] = comparison.Delta,
                        ["native_object_bytes_a"] = comparison.BytesA,
                        ["native_object_bytes_b"] = comparison.BytesB,
                        ["native_object_bytes_delta"] = checked(comparison.BytesB - comparison.BytesA),
                        ["managed_heap_bytes_delta"] = JValue.CreateNull(),
                        ["retained_bytes_delta"] = JValue.CreateNull(),
                        ["limitations"] = new JArray(
                            "NativeObjects_Size is snapshot-native object accounting, not process memory, managed object size, graphics memory or retained/exclusive memory.",
                            "Managed heap crawling, retained graph analysis and allocation/root-reference size attribution are unavailable in this reader adapter.",
                            "Typed count/byte deltas compare selections; object identity deltas require matching nonzero capture sessions and native-ID widths.",
                            "Snapshot indices/addresses are not portable object handles. Output object identities are lossless strings and cannot select live Unity objects.",
                            "Input containers, entry counts and native-read byte allocations are checked before the corresponding native allocation/read. Unknown package ABI or format refuses rather than guessing."
                        ),
                    }
                );
            }
            catch (MemorySnapshotComparison.LimitException)
            {
                return new ErrorResponse("memory_work_limit", new { message = "Selected native type count exceeds 512.", columns = (JArray)null });
            }
            catch (SnapshotProblem error)
            {
                return new ErrorResponse(error.Code, new { message = error.Message, columns = error.Columns });
            }
            catch (FileNotFoundException)
            {
                return new ErrorResponse("snapshot_file_not_found");
            }
            catch (OverflowException)
            {
                return new ErrorResponse("snapshot_size_overflow");
            }
            catch (Exception error)
            {
                return new ErrorResponse(
                    "snapshot_read_failed",
                    new { message = (error is TargetInvocationException ? error.InnerException : error)?.Message }
                );
            }
        }

        private static Snapshot Load(Backend backend, string path, int maximum, string prefix)
        {
            // Deny concurrent writes for the entire native reader lifetime; do not consume a changing capture.
            using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                Preflight(input, backend.EntryType);
                object reader = null;
                bool attemptedOpen = false;
                try
                {
                    reader = Activator.CreateInstance(backend.ReaderType, true);
                    attemptedOpen = true;
                    object opened = backend.ReaderType.GetMethod("Open").Invoke(reader, new object[] { path });
                    if (opened.ToString() != "Success")
                        throw new SnapshotProblem("snapshot_format_unsupported", "The pinned reader refused this container: " + opened);
                    uint format = Convert.ToUInt32(backend.ReaderType.GetProperty("FormatVersionNumeric").GetValue(reader));
                    if (format < 11 || format > 18)
                        throw new SnapshotProblem("snapshot_format_unsupported", "Only inspected snapshot formats 11–18 are supported; actual " + format + ".");
                    long readBytes = 0;
                    JArray columnContracts = null;
                    uint Count(string entry) =>
                        Convert.ToUInt32(backend.ReaderType.GetMethod("GetEntryCount").Invoke(reader, new[] { Enum.Parse(backend.EntryType, entry) }));
                    byte[] Read(string entry, uint count, long? expectedSize = null)
                    {
                        object kind = Enum.Parse(backend.EntryType, entry);
                        if (Count(entry) != count)
                            throw new SnapshotProblem("snapshot_column_invalid", "Unexpected entry count for " + entry + ".", columnContracts);
                        if (count == 0)
                            return Array.Empty<byte>();
                        long size = Convert.ToInt64(
                            backend.ReaderType.GetMethod("GetSizeForEntryRange").Invoke(reader, new object[] { kind, 0L, (long)count, true })
                        );
                        if (size < 0 || size > 32L * 1024 * 1024 || checked(readBytes + size) > MaximumReadBytes)
                            throw new SnapshotProblem(
                                "memory_work_limit",
                                $"Column {entry} count {count}, bytes {size}, prior read bytes {readBytes} exceeds the 32 MiB column/64 MiB input read budget.",
                                columnContracts
                            );
                        if (expectedSize.HasValue && size != expectedSize.Value)
                            throw new SnapshotProblem(
                                "snapshot_column_invalid",
                                $"Column {entry} count {count} has {size} bytes; expected {expectedSize.Value} before native allocation.",
                                columnContracts
                            );
                        readBytes += size;
                        object operation = null;
                        object buffer = null;
                        try
                        {
                            operation = backend.Read.Invoke(
                                reader,
                                new[] { kind, (object)0L, (long)count, Enum.Parse(backend.Read.GetParameters()[3].ParameterType, "TempJob"), true }
                            );
                            if (operation.GetType().GetProperty("Error").GetValue(operation).ToString() != "Success")
                                throw new SnapshotProblem("snapshot_read_failed", "Native read failed for " + entry + ".");
                            buffer = operation.GetType().GetProperty("Result").GetValue(operation);
                            long actual = Convert.ToInt64(buffer.GetType().GetProperty("Count").GetValue(buffer));
                            if (actual != size)
                                throw new SnapshotProblem("snapshot_column_invalid", "Native buffer size mismatch for " + entry + ".");
                            byte[] bytes = new byte[(int)size];
                            int copied = 0;
                            foreach (byte value in (IEnumerable<byte>)buffer)
                            {
                                if (copied == bytes.Length)
                                    throw new SnapshotProblem("snapshot_column_invalid", "Native enumeration exceeds its declared byte count.");
                                bytes[copied++] = value;
                            }
                            if (copied != bytes.Length)
                                throw new SnapshotProblem("snapshot_column_invalid", "Copied column length mismatch.");
                            return bytes;
                        }
                        finally
                        {
                            try
                            {
                                (buffer as IDisposable)?.Dispose();
                            }
                            finally
                            {
                                (operation as IDisposable)?.Dispose();
                            }
                        }
                    }
                    uint objects = Count("NativeObjects_InstanceId");
                    uint types = Count("NativeTypes_Name");
                    if (objects > maximum)
                        throw new SnapshotProblem(
                            "memory_work_limit",
                            $"Entry NativeObjects_InstanceId count {objects} exceeds row budget {maximum} before reading columns."
                        );
                    if (types > 4096)
                        throw new SnapshotProblem("memory_work_limit", $"Entry NativeTypes_Name count {types} exceeds row budget 4096 before reading columns.");
                    int width = format >= 18 ? 8 : 4;
                    // Inspect every required column together. This is size/format querying only;
                    // Read still validates each expected count/width and byte budget before allocation.
                    columnContracts = new JArray();
                    void DescribeColumn(string entry, uint expectedCount, long? expectedBytes)
                    {
                        object kind = Enum.Parse(backend.EntryType, entry);
                        uint actualCount = Count(entry);
                        long? actualBytes =
                            actualCount == expectedCount
                                ? Convert.ToInt64(
                                    backend.ReaderType.GetMethod("GetSizeForEntryRange").Invoke(reader, new object[] { kind, 0L, (long)actualCount, true })
                                )
                                : (long?)null;
                        columnContracts.Add(
                            new JObject
                            {
                                ["entry"] = entry,
                                ["format"] = backend.ReaderType.GetMethod("GetEntryFormat").Invoke(reader, new[] { kind }).ToString(),
                                ["actual_count"] = actualCount,
                                ["expected_count"] = expectedCount,
                                ["actual_bytes_including_dynamic_offsets"] = actualBytes,
                                ["expected_bytes"] = expectedBytes,
                            }
                        );
                    }
                    DescribeColumn("Metadata_CaptureFlags", 1, 4);
                    DescribeColumn("ProfileTarget_Info", 1, backend.ProfileTargetInfoBytes);
                    DescribeColumn("NativeObjects_Name", objects, null);
                    DescribeColumn("NativeTypes_Name", types, null);
                    DescribeColumn("NativeObjects_InstanceId", objects, objects * (long)width);
                    DescribeColumn("NativeObjects_NativeObjectAddress", objects, objects * 8L);
                    DescribeColumn("NativeObjects_Size", objects, objects * 8L);
                    DescribeColumn("NativeObjects_NativeTypeArrayIndex", objects, objects * 4L);
                    byte[] flags = Read("Metadata_CaptureFlags", 1, 4);
                    if (flags.Length != 4 || (BitConverter.ToUInt32(flags, 0) & 2) == 0)
                        throw new SnapshotProblem("snapshot_capture_missing_native_objects", "The capture does not contain native object evidence.");
                    byte[] target = Read("ProfileTarget_Info", 1, backend.ProfileTargetInfoBytes);
                    var snapshot = new Snapshot
                    {
                        Path = path,
                        Format = format,
                        Session = BitConverter.ToUInt32(target, backend.ProfileTargetSessionOffset),
                    };
                    string[] names = Strings(Read("NativeObjects_Name", objects), objects);
                    string[] typeNames = Strings(Read("NativeTypes_Name", types), types);
                    byte[] ids = Read("NativeObjects_InstanceId", objects, objects * (long)width);
                    byte[] addresses = Read("NativeObjects_NativeObjectAddress", objects, objects * 8L);
                    byte[] sizes = Read("NativeObjects_Size", objects, objects * 8L);
                    byte[] typeIndices = Read("NativeObjects_NativeTypeArrayIndex", objects, objects * 4L);
                    if (ids.Length != objects * width || addresses.Length != objects * 8L || sizes.Length != objects * 8L || typeIndices.Length != objects * 4L)
                        throw new SnapshotProblem("snapshot_column_invalid", "Native object column widths do not match the inspected format.");
                    for (int i = 0; i < objects; i++)
                    {
                        int type = BitConverter.ToInt32(typeIndices, i * 4);
                        if (type < 0 || type >= typeNames.Length)
                            throw new SnapshotProblem("snapshot_column_invalid", "Native object type index is out of range.");
                        ulong rawSize = BitConverter.ToUInt64(sizes, i * 8);
                        if (rawSize > long.MaxValue)
                            throw new SnapshotProblem("snapshot_size_overflow", "Native object size exceeds the signed result range.");
                        if (!names[i].StartsWith(prefix, StringComparison.Ordinal))
                            continue;
                        string id =
                            width == 8
                                ? BitConverter.ToUInt64(ids, i * width).ToString(System.Globalization.CultureInfo.InvariantCulture)
                                : BitConverter.ToInt32(ids, i * width).ToString(System.Globalization.CultureInfo.InvariantCulture);
                        string key = id + ":" + BitConverter.ToUInt64(addresses, i * 8).ToString("x16") + ":" + typeNames[type];
                        if (snapshot.Objects.ContainsKey(key))
                            throw new SnapshotProblem("snapshot_column_invalid", "Native snapshot object identity is duplicated.");
                        snapshot.Objects.Add(
                            key,
                            new NativeObject
                            {
                                Key = key,
                                Id = id,
                                Name = names[i],
                                Type = typeNames[type],
                                Bytes = (long)rawSize,
                            }
                        );
                    }
                    return snapshot;
                }
                finally
                {
                    try
                    {
                        (reader as IDisposable)?.Dispose();
                    }
                    finally
                    {
                        // The pinned LowLevelFileReader frees only its path GCHandle. Its filename
                        // reads leave an engine cache handle open even after each read is completed.
                        // Close and complete only this attempted input, including exceptional exits.
                        if (attemptedOpen)
                            AsyncReadManager.CloseCachedFileAsync(path).Complete();
                    }
                }
            }
        }

        private static string[] Strings(byte[] bytes, uint count)
        {
            if (count == 0)
                return Array.Empty<string>();
            long header = (count + 1L) * 8;
            if (header > bytes.Length)
                throw new SnapshotProblem("snapshot_column_invalid", "Dynamic string offsets exceed the native column.");
            var names = new string[count];
            var utf8 = new UTF8Encoding(false, true);
            for (int i = 0; i < count; i++)
            {
                long start = BitConverter.ToInt64(bytes, i * 8);
                long end = BitConverter.ToInt64(bytes, (i + 1) * 8);
                if (start < 0 || end < start || end - start > 4096 || end > bytes.Length - header)
                    throw new SnapshotProblem("snapshot_column_invalid", "String offsets/length violate the inspected column budget.");
                names[i] = utf8.GetString(bytes, checked((int)(header + start)), checked((int)(end - start)));
            }
            return names;
        }

        private static void Preflight(FileStream input, Type entryType)
        {
            // Guard every allocation performed by the pinned FileReader.Open before entering native code.
            if (input.Length < 32)
                throw new SnapshotProblem("snapshot_format_unsupported", "Snapshot file size is outside the bounded container range.");
            if (input.Length > MaximumFileBytes)
                throw new SnapshotProblem("memory_work_limit", "Snapshot exceeds the 512 MiB input budget.");
            var file = new BinaryReader(input, Encoding.UTF8, true);
            long length = input.Length;
            void At(ulong position, long required)
            {
                if (position > (ulong)length || required < 0 || (ulong)required > (ulong)length - position)
                    throw new SnapshotProblem("snapshot_format_unsupported", "Container offset exceeds the input file.");
                input.Position = (long)position;
            }
            if (file.ReadUInt32() != 0xAEABCDCD)
                throw new SnapshotProblem("snapshot_format_unsupported", "Unknown snapshot header signature.");
            input.Position = length - 12;
            ulong directory = file.ReadUInt64();
            if (file.ReadUInt32() != 0xABCDCDAE)
                throw new SnapshotProblem("snapshot_format_unsupported", "Unknown snapshot footer signature.");
            At(directory, 20);
            if (file.ReadUInt32() != 0xCDCDAEAB || file.ReadUInt32() != 0x20170724)
                throw new SnapshotProblem("snapshot_format_unsupported", "Unknown snapshot directory format.");
            ulong blocksOffset = file.ReadUInt64();
            uint entryCount = file.ReadUInt32();
            int supportedEntries = Convert.ToInt32(Enum.Parse(entryType, "Count"));
            if (entryCount > supportedEntries)
                throw new SnapshotProblem("snapshot_format_unsupported", "Container has columns newer than the pinned reader.");
            At((ulong)input.Position, entryCount * 8L);
            var entryOffsets = new ulong[entryCount];
            for (int i = 0; i < entryCount; i++)
                entryOffsets[i] = file.ReadUInt64();
            At(blocksOffset, 8);
            if (file.ReadUInt32() != 0x20170724)
                throw new SnapshotProblem("snapshot_format_unsupported", "Unknown snapshot block format.");
            uint blocks = file.ReadUInt32();
            if (blocks > 4096)
                throw new SnapshotProblem("memory_work_limit", "Snapshot native block count exceeds the open budget.");
            At((ulong)input.Position, blocks * 8L);
            var blockOffsets = new ulong[blocks];
            var blockChunks = new ulong[blocks];
            var blockTotals = new ulong[blocks];
            for (int i = 0; i < blocks; i++)
                blockOffsets[i] = file.ReadUInt64();
            long metadataBytes = 0;
            for (int blockIndex = 0; blockIndex < blockOffsets.Length; blockIndex++)
            {
                ulong offset = blockOffsets[blockIndex];
                At(offset, 16);
                ulong chunk = file.ReadUInt64();
                ulong total = file.ReadUInt64();
                blockChunks[blockIndex] = chunk;
                blockTotals[blockIndex] = total;
                if (chunk == 0 || total > MaximumFileBytes)
                    throw new SnapshotProblem("memory_work_limit", "Snapshot block sizes exceed the bounded open budget.");
                ulong offsets = total / chunk + (total % chunk == 0 ? 0UL : 1UL);
                if (offsets > 131072)
                    throw new SnapshotProblem("memory_work_limit", "Snapshot chunk-offset count exceeds the open budget.");
                metadataBytes = checked(metadataBytes + (long)offsets * 8);
                if (metadataBytes > 32L * 1024 * 1024)
                    throw new SnapshotProblem("memory_work_limit", "Native open metadata exceeds 32 MiB.");
                At((ulong)input.Position, (long)offsets * 8);
                for (ulong i = 0; i < offsets; i++)
                {
                    ulong data = file.ReadUInt64();
                    At(data, (long)Math.Min(chunk, total - i * chunk));
                    input.Position = checked((long)offset + 16 + (long)(i + 1) * 8);
                }
            }
            bool versionFound = false;
            for (int entryIndex = 0; entryIndex < entryOffsets.Length; entryIndex++)
            {
                ulong offset = entryOffsets[entryIndex];
                if (offset == 0)
                    continue;
                At(offset, 18);
                ushort format = file.ReadUInt16();
                uint block = file.ReadUInt32();
                uint meta = file.ReadUInt32();
                ulong last = file.ReadUInt64();
                string entryName = Enum.GetName(entryType, (ushort)entryIndex) ?? entryIndex.ToString();
                if (format > 3 || format != 0 && block >= blocks)
                    throw new SnapshotProblem("snapshot_format_unsupported", "Invalid snapshot entry header.");
                // Match Entry.Count's uint cast exactly: compact constant headers have only four
                // meaningful count bytes. The 18-byte ABI overlay can include the next header's bytes
                // in HeaderMeta's high word. They are not count bits or an allocation request.
                // Constant entries allocate no row storage on Open; selected reads stay bounded below.
                if (format == 2)
                    last = (uint)last;
                if (format == 2 && checked((ulong)meta * last) > blockTotals[block])
                    throw new SnapshotProblem(
                        "snapshot_column_invalid",
                        $"Constant entry {entryName} count {last}, element bytes {meta} exceeds owning block bytes {blockTotals[block]}."
                    );
                if (format == 1 && (last > blockTotals[block] || meta > blockTotals[block] - last || meta > blockChunks[block] - last % blockChunks[block]))
                    throw new SnapshotProblem("snapshot_column_invalid", "Scalar column bytes exceed the owning chunk or native block.");
                if (entryIndex == Convert.ToInt32(Enum.Parse(entryType, "Metadata_Version")))
                {
                    if (format != 1 || meta != 4 || last > blockTotals[block] || 4UL > blockTotals[block] - last)
                        throw new SnapshotProblem("snapshot_column_invalid", "Metadata_Version must be the inspected four-byte scalar before native open.");
                    long restore = input.Position;
                    ulong chunkIndex = last / blockChunks[block];
                    At(checked(blockOffsets[block] + 16 + chunkIndex * 8), 8);
                    ulong position = file.ReadUInt64();
                    At(checked(position + last % blockChunks[block]), 4);
                    uint version = file.ReadUInt32();
                    if (version < 11 || version > 18)
                        throw new SnapshotProblem(
                            "snapshot_format_unsupported",
                            "Only inspected snapshot formats 11–18 are supported; actual " + version + "."
                        );
                    versionFound = true;
                    input.Position = restore;
                }
                if (format == 3)
                {
                    if (meta == 0 || meta > 1000000)
                        throw new SnapshotProblem(
                            "memory_work_limit",
                            $"Dynamic entry {entryName} count {meta} is outside the 1–1000000 native-open allocation budget."
                        );
                    metadataBytes = checked(metadataBytes + meta * 8L);
                    if (metadataBytes > 32L * 1024 * 1024)
                        throw new SnapshotProblem(
                            "memory_work_limit",
                            $"Dynamic entry {entryName} count {meta} raises native-open metadata to {metadataBytes} bytes, exceeding 32 MiB."
                        );
                    At((ulong)input.Position, meta * 8L);
                    ulong previous = last;
                    if (previous > blockTotals[block])
                        throw new SnapshotProblem("snapshot_column_invalid", "Dynamic offsets exceed the owning block.");
                    for (uint i = 0; i < meta; i++)
                    {
                        ulong next = file.ReadUInt64();
                        if (next < previous || next > blockTotals[block])
                            throw new SnapshotProblem("snapshot_column_invalid", "Invalid dynamic entry offsets.");
                        previous = next;
                    }
                }
                if (metadataBytes > 32L * 1024 * 1024)
                    throw new SnapshotProblem("memory_work_limit", "Snapshot native open metadata exceeds 32 MiB.");
            }
            if (!versionFound)
                throw new SnapshotProblem("snapshot_format_unsupported", "Snapshot has no supported version scalar.");
        }
    }
}
