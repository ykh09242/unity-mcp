using System;
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
    internal static class MemorySnapshotAnalysisOps
    {
        private const string ReaderPackage = "com.unity.memoryprofiler";
        private const string FormatNamespace = "Unity.MemoryProfiler.Editor.Format.";
        private const string ReaderNamespace = FormatNamespace + "QueriedSnapshot.";
        private const uint MinimumFormat = 8;
        private const uint SessionInfoFormat = 11;
        private const int MaximumEntryTypes = 256;
        private const long MaximumFileBytes = 8L * 1024 * 1024 * 1024;
        private const long MaximumReadBytes = 128L * 1024 * 1024;

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
            private delegate byte ValueAt<T>(ref T buffer, long index);
            private delegate ref byte ReferenceAt<T>(ref T buffer, long index);

            internal Type ReaderType;
            internal Type EntryType;
            internal ConstructorInfo Constructor;
            internal MethodInfo Open;
            internal MethodInfo Count;
            internal MethodInfo Size;
            internal MethodInfo Format;
            internal MethodInfo Read;
            internal MethodInfo DisposeOperation;
            internal bool ExplicitOffsets;
            internal PropertyInfo VersionNumber;
            internal PropertyInfo ReadError;
            internal PropertyInfo Result;
            internal PropertyInfo BufferCount;
            internal object Success;
            internal object TempJob;
            internal Action<object, byte[]> Copy;
            internal uint LastFormat;
            internal string Version;
            internal string Error;
            internal string MissingContract;
            internal int ProfileTargetInfoBytes;
            internal int ProfileTargetSessionOffset;

            internal Backend()
            {
                try
                {
                    var reader = Type.GetType(ReaderNamespace + "FileReader, Unity.MemoryProfiler.Editor");
                    if (reader == null)
                    {
                        var installed = PackageInfo.FindForAssetPath("Packages/" + ReaderPackage + "/package.json");
                        Version = installed?.version;
                        Error = installed == null ? "memory_profiler_package_missing" : "unsupported_reader_abi";
                        if (installed != null)
                            MissingContract = ReaderNamespace + "FileReader";
                        return;
                    }
                    var package = PackageInfo.FindForAssembly(reader.Assembly);
                    Version = package?.version;
                    if (package?.name != ReaderPackage)
                    {
                        Error = "unrecognized_reader_package";
                        return;
                    }
                    Qualify(reader);
                }
                catch (Exception ex)
                    when (ex is MemberAccessException
                        || ex is TypeLoadException
                        || ex is ArgumentException
                        || ex is AmbiguousMatchException
                        || ex is TargetInvocationException
                    )
                {
                    Error = "unsupported_reader_abi";
                    MissingContract = ex.Message;
                }
            }

            private void Qualify(Type reader)
            {
                var assembly = reader.Assembly;
                Type InReader(string name) => assembly.GetType(ReaderNamespace + name, true);
                ReaderType = reader;
                EntryType = InReader("EntryType");
                Type format = InReader("EntryFormat");
                Type error = InReader("ReadError");
                Type operation = InReader("GenericReadOperation");
                Type buffer = assembly.GetType("Unity.MemoryProfiler.Editor.Containers.DynamicArray`1", true).MakeGenericType(typeof(byte));
                Type targetInfo = assembly.GetType(FormatNamespace + "ProfileTargetInfo", true);
                Require(!reader.IsAbstract && typeof(IDisposable).IsAssignableFrom(reader), reader.FullName);
                Constructor = reader.GetConstructor(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic, null, Type.EmptyTypes, null);
                Require(reader.IsValueType || Constructor != null, "FileReader()");
                DisposeOperation = Method(operation, "Dispose", typeof(void));
                Require(buffer.IsValueType && typeof(IDisposable).IsAssignableFrom(buffer), buffer.FullName);
                Open = Method(reader, "Open", error, typeof(string));
                Count = Method(reader, "GetEntryCount", typeof(uint), EntryType);
                Format = Method(reader, "GetEntryFormat", format, EntryType);
                Type allocator = Type.GetType("Unity.Collections.Allocator, UnityEngine.CoreModule");
                if (allocator != null)
                {
                    TempJob = EnumMember(allocator, typeof(int), "TempJob", 3);
                    Read = FindMethod(reader, "Read", operation, EntryType, typeof(long), typeof(long), allocator, typeof(bool));
                }
                if (Read == null)
                {
                    // Some package builds expose the Collections AllocatorHandle overload.
                    var manager = Type.GetType("Unity.Collections.AllocatorManager, Unity.Collections");
                    var handle = manager?.GetNestedType("AllocatorHandle");
                    var readHandle = handle == null ? null : FindMethod(reader, "Read", operation, EntryType, typeof(long), typeof(long), handle, typeof(bool));
                    if (readHandle != null)
                    {
                        Require(PackageInfo.FindForAssembly(manager.Assembly)?.name == "com.unity.collections", manager.FullName);
                        var tempJob = manager.GetField("TempJob", BindingFlags.Public | BindingFlags.Static);
                        Require(handle.IsValueType && tempJob?.FieldType == handle && tempJob.IsInitOnly, "AllocatorManager.TempJob");
                        TempJob = tempJob.GetValue(null);
                        var index = handle.GetField("Index", BindingFlags.Public | BindingFlags.Instance);
                        Require(index?.FieldType == typeof(ushort) && (ushort)index.GetValue(TempJob) == 3, "AllocatorHandle.Index");
                        Read = readHandle;
                    }
                }
                ExplicitOffsets = Read != null;
                if (ExplicitOffsets)
                    Size = Method(reader, "GetSizeForEntryRange", typeof(long), EntryType, typeof(long), typeof(long), typeof(bool));
                else
                {
                    // The earlier pair always includes the dynamic string offset table.
                    Require(allocator != null, "Read allocator");
                    Read = Method(reader, "Read", operation, EntryType, typeof(long), typeof(long), allocator);
                    Size = Method(reader, "GetSizeForEntryRange", typeof(long), EntryType, typeof(long), typeof(long));
                }
                VersionNumber = Property(reader, "FormatVersionNumeric", typeof(uint));
                ReadError = Property(operation, "Error", error);
                Result = Property(operation, "Result", buffer);
                BufferCount = Property(buffer, "Count", typeof(long));
                var indexer = buffer.GetProperty("Item", new[] { typeof(long) })?.GetGetMethod();
                Require(
                    indexer != null && (indexer.ReturnType == typeof(byte) || indexer.ReturnType == typeof(byte).MakeByRefType()),
                    buffer.FullName + ".Item"
                );
                Copy =
                    (Action<object, byte[]>)
                        typeof(Backend)
                            .GetMethod(nameof(ByteCopier), BindingFlags.NonPublic | BindingFlags.Static)
                            .MakeGenericMethod(buffer)
                            .Invoke(null, new object[] { indexer });
                Success = EnumMember(error, typeof(ushort), "Success", 1);
                EnumMember(EntryType, typeof(ushort), "Metadata_Version", 0);
                EnumMember(EntryType, typeof(ushort), "Metadata_CaptureFlags", 3);
                EnumMember(EntryType, typeof(ushort), "NativeTypes_Name", 5);
                EnumMember(EntryType, typeof(ushort), "NativeObjects_NativeTypeArrayIndex", 7);
                EnumMember(EntryType, typeof(ushort), "NativeObjects_InstanceId", 10);
                EnumMember(EntryType, typeof(ushort), "NativeObjects_Name", 11);
                EnumMember(EntryType, typeof(ushort), "NativeObjects_NativeObjectAddress", 12);
                EnumMember(EntryType, typeof(ushort), "NativeObjects_Size", 13);
                EnumMember(EntryType, typeof(ushort), "ProfileTarget_Info", 59);
                int entries = Convert.ToInt32(Enum.Parse(EntryType, "Count"));
                Require(entries > 59 && entries <= MaximumEntryTypes, "EntryType.Count");
                EnumMember(format, typeof(ushort), "Undefined", 0);
                EnumMember(format, typeof(ushort), "SingleElement", 1);
                EnumMember(format, typeof(ushort), "ConstantSizeElementArray", 2);
                EnumMember(format, typeof(ushort), "DynamicSizeElementArray", 3);
                Type versions = assembly.GetType(FormatNamespace + "FormatVersion", true);
                EnumMember(versions, typeof(uint), "SnapshotMinSupportedFormatVersion", MinimumFormat);
                EnumMember(versions, typeof(uint), "ProfileTargetInfoAndMemStatsVersion", SessionInfoFormat);
                LastFormat = SessionInfoFormat;
                while (LastFormat < 18 && Enum.IsDefined(versions, LastFormat + 1))
                    LastFormat++;
                // Versions 19 and 20 extend managed graph/static bookkeeping. The native object
                // columns retain their ordinals and entity ID width. Qualify the declared extensions.
                if (
                    LastFormat == 18
                    && Enum.GetName(versions, 19u) == "ManagedConnectionsVersion"
                    && Enum.GetName(EntryType, (ushort)93) == "ManagedObjects_Target"
                    && Enum.GetName(EntryType, (ushort)101) == "NativeObjects_GCHandle"
                    && entries > 101
                )
                    LastFormat = 19;
                if (
                    LastFormat == 19
                    && Enum.GetName(versions, 20u) == "StaticFieldAndNonGCHeapVersion"
                    && Enum.GetName(EntryType, (ushort)102) == "TypeDescriptions_StaticFieldSlots"
                    && Enum.GetName(EntryType, (ushort)104) == "ManagedHeapSections_Flags"
                    && entries > 104
                )
                    LastFormat = 20;

                // Preflight mirrors the native reader's two on-disk header overlays.
                Type entry = InReader("EntryHeader");
                Type block = InReader("BlockHeader");
                Require(Marshal.SizeOf(entry) == 18 && Marshal.SizeOf(block) == 16, "EntryHeader / BlockHeader size");
                FieldAt(entry, "Format", format, 0);
                FieldAt(entry, "BlockIndex", typeof(uint), 2);
                FieldAt(entry, "EntriesMeta", typeof(uint), 6);
                FieldAt(entry, "HeaderMeta", typeof(ulong), 10);
                FieldAt(block, "ChunkSize", typeof(ulong), 0);
                FieldAt(block, "TotalBytes", typeof(ulong), 8);
                Type signature = InReader("FileReader+FormatSignature");
                EnumMember(signature, typeof(uint), "HeaderSignature", 0xAEABCDCD);
                EnumMember(signature, typeof(uint), "DirectorySignature", 0xCDCDAEAB);
                EnumMember(signature, typeof(uint), "FooterSignature", 0xABCDCDAE);
                EnumMember(signature, typeof(uint), "ChapterSectionVersion", 0x20170724);
                EnumMember(signature, typeof(uint), "BlockSectionVersion", 0x20170724);
                FieldAt(targetInfo, "SessionGUID", typeof(uint), 0);
                ProfileTargetInfoBytes = Marshal.SizeOf(targetInfo);
                ProfileTargetSessionOffset = Marshal.OffsetOf(targetInfo, "SessionGUID").ToInt32();
                Require(ProfileTargetInfoBytes == 520, "ProfileTargetInfo size");
            }

            private static Action<object, byte[]> ByteCopier<T>(MethodInfo indexer)
                where T : struct
            {
                // Some package releases omit the final element when enumerating. All inspected
                // readers provide counted byte access for the lifetime of the completed operation.
                if (indexer.ReturnType == typeof(byte))
                {
                    var at = (ValueAt<T>)indexer.CreateDelegate(typeof(ValueAt<T>));
                    return (boxed, bytes) =>
                    {
                        var buffer = (T)boxed;
                        for (int i = 0; i < bytes.Length; i++)
                            bytes[i] = at(ref buffer, i);
                    };
                }
                var reference = (ReferenceAt<T>)indexer.CreateDelegate(typeof(ReferenceAt<T>));
                return (boxed, bytes) =>
                {
                    var buffer = (T)boxed;
                    for (int i = 0; i < bytes.Length; i++)
                        bytes[i] = reference(ref buffer, i);
                };
            }

            internal long ColumnSize(object reader, object kind, uint count) =>
                count == 0
                    ? 0L
                    : (long)Size.Invoke(reader, ExplicitOffsets ? new object[] { kind, 0L, (long)count, true } : new object[] { kind, 0L, (long)count });

            internal object ReadColumn(object reader, object kind, uint count) =>
                Read.Invoke(reader, ExplicitOffsets ? new object[] { kind, 0L, (long)count, TempJob, true } : new object[] { kind, 0L, (long)count, TempJob });

            private static void Require(bool matches, string member)
            {
                if (!matches)
                    throw new MissingMemberException(member);
            }

            private static MethodInfo Method(Type type, string name, Type result, params Type[] parameters)
            {
                var method = FindMethod(type, name, result, parameters);
                Require(method != null, type.FullName + "." + name);
                return method;
            }

            private static MethodInfo FindMethod(Type type, string name, Type result, params Type[] parameters) =>
                type.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .FirstOrDefault(candidate =>
                        candidate.Name == name
                        && !candidate.IsGenericMethod
                        && candidate.ReturnType == result
                        && candidate.GetParameters().Select(p => p.ParameterType).SequenceEqual(parameters)
                    );

            private static PropertyInfo Property(Type type, string name, Type result)
            {
                var property = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance, null, result, Type.EmptyTypes, null);
                Require(property?.GetGetMethod() != null, type.FullName + "." + name);
                return property;
            }

            private static object EnumMember(Type type, Type storage, string name, uint value)
            {
                Require(type.IsEnum && Enum.GetUnderlyingType(type) == storage && Enum.IsDefined(type, name), type.FullName + "." + name);
                object member = Enum.Parse(type, name);
                Require(Convert.ToUInt32(member) == value, type.FullName + "." + name);
                return member;
            }

            private static void FieldAt(Type type, string name, Type fieldType, int offset)
            {
                Require(
                    type.IsValueType
                        && type.GetField(name, BindingFlags.Instance | BindingFlags.Public)?.FieldType == fieldType
                        && Marshal.OffsetOf(type, name).ToInt64() == offset,
                    type.FullName + "." + name
                );
            }
        }

        internal static object Status(JObject parameters)
        {
            var backend = new Backend();
            return new SuccessResponse(
                "Checked the installed Memory Profiler snapshot reader.",
                new
                {
                    reader_supported = backend.Error == null,
                    package_version = backend.Version,
                    required_package = ReaderPackage,
                    reason = backend.Error,
                    missing_contract = backend.MissingContract,
                    minimum_format = MinimumFormat,
                    maximum_format = backend.Error == null ? backend.LastFormat : (uint?)null,
                    session_info_minimum_format = SessionInfoFormat,
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
            int maximum = MemoryAnalysisParams.Integer(parameters, "max_objects", 1, 1000000, 1000000);
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
                        required_package = ReaderPackage,
                        missing_contract = backend.MissingContract,
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
                            ["session_id"] = a.Format >= SessionInfoFormat ? (JToken)a.Session : JValue.CreateNull(),
                            ["selected_object_count"] = a.Objects.Count,
                        },
                        ["snapshot_b"] = new JObject
                        {
                            ["path"] = b.Path,
                            ["format_version"] = b.Format,
                            ["session_id"] = b.Format >= SessionInfoFormat ? (JToken)b.Session : JValue.CreateNull(),
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
                return new ErrorResponse("memory_work_limit", new { message = "Selected native type count exceeds 4096.", columns = (JArray)null });
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
                Preflight(input, backend.EntryType, backend.LastFormat);
                object reader = null;
                bool attemptedOpen = false;
                try
                {
                    reader = backend.Constructor == null ? Activator.CreateInstance(backend.ReaderType) : backend.Constructor.Invoke(null);
                    attemptedOpen = true;
                    object opened = backend.Open.Invoke(reader, new object[] { path });
                    if (!backend.Success.Equals(opened))
                        throw new SnapshotProblem("snapshot_format_unsupported", "The Memory Profiler reader refused this container: " + opened);
                    uint format = (uint)backend.VersionNumber.GetValue(reader);
                    if (format < MinimumFormat || format > backend.LastFormat)
                        throw new SnapshotProblem(
                            "snapshot_format_unsupported",
                            $"Snapshot format {format} exceeds the reader's {MinimumFormat}–{backend.LastFormat} range."
                        );
                    long readBytes = 0;
                    JArray columnContracts = null;
                    uint Count(string entry) => (uint)backend.Count.Invoke(reader, new[] { Enum.Parse(backend.EntryType, entry) });
                    byte[] Read(string entry, uint count, long? expectedSize = null)
                    {
                        object kind = Enum.Parse(backend.EntryType, entry);
                        if (Count(entry) != count)
                            throw new SnapshotProblem("snapshot_column_invalid", "Unexpected entry count for " + entry + ".", columnContracts);
                        if (count == 0)
                            return Array.Empty<byte>();
                        long size = backend.ColumnSize(reader, kind, count);
                        if (size < 0 || size > 64L * 1024 * 1024 || checked(readBytes + size) > MaximumReadBytes)
                            throw new SnapshotProblem(
                                "memory_work_limit",
                                $"Column {entry} count {count}, bytes {size}, prior read bytes {readBytes} exceeds the 64 MiB column/128 MiB input read budget.",
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
                            operation = backend.ReadColumn(reader, kind, count);
                            if (!backend.Success.Equals(backend.ReadError.GetValue(operation)))
                                throw new SnapshotProblem("snapshot_read_failed", "Native read failed for " + entry + ".");
                            buffer = backend.Result.GetValue(operation);
                            long actual = (long)backend.BufferCount.GetValue(buffer);
                            if (actual != size)
                                throw new SnapshotProblem("snapshot_column_invalid", "Native buffer size mismatch for " + entry + ".");
                            byte[] bytes = new byte[(int)size];
                            backend.Copy(buffer, bytes);
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
                                if (operation != null)
                                    backend.DisposeOperation.Invoke(operation, null);
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
                        long? actualBytes = actualCount == expectedCount ? backend.ColumnSize(reader, kind, actualCount) : (long?)null;
                        columnContracts.Add(
                            new JObject
                            {
                                ["entry"] = entry,
                                ["format"] = backend.Format.Invoke(reader, new[] { kind }).ToString(),
                                ["actual_count"] = actualCount,
                                ["expected_count"] = expectedCount,
                                ["actual_bytes_including_dynamic_offsets"] = actualBytes,
                                ["expected_bytes"] = expectedBytes,
                            }
                        );
                    }
                    DescribeColumn("Metadata_CaptureFlags", 1, 4);
                    if (format >= SessionInfoFormat)
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
                    var snapshot = new Snapshot
                    {
                        Path = path,
                        Format = format,
                        Session =
                            format >= SessionInfoFormat
                                ? BitConverter.ToUInt32(Read("ProfileTarget_Info", 1, backend.ProfileTargetInfoBytes), backend.ProfileTargetSessionOffset)
                                : 0u,
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
                        // LowLevelFileReader frees its path GCHandle. Its filename
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

        private static void Preflight(Stream input, Type entryType, uint maximumFormat)
        {
            // Bound the package reader's metadata allocations before native Open.
            if (input.Length < 32)
                throw new SnapshotProblem("snapshot_format_unsupported", "Snapshot file size is outside the bounded container range.");
            if (input.Length > MaximumFileBytes)
                throw new SnapshotProblem("memory_work_limit", "Snapshot exceeds the 8 GiB input budget.");
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
            if (entryCount > supportedEntries || supportedEntries > MaximumEntryTypes)
                throw new SnapshotProblem("snapshot_format_unsupported", "Container entry count exceeds the supported reader layout.");
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
                if (chunk == 0 || chunk > uint.MaxValue || total > MaximumFileBytes)
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
            uint snapshotVersion = 0;
            bool hasNativeObjects = false;
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
                bool strings = entryName == "NativeObjects_Name" || entryName == "NativeTypes_Name";
                if (format == 0)
                    entryOffsets[entryIndex] = 0; // An explicit Undefined header also describes an unwritten chapter.
                bool emptyArray = format == 2 && (uint)last == 0;
                if (format != 0 && strings && format != 3 && !emptyArray)
                    throw new SnapshotProblem("snapshot_column_invalid", "Native string column is missing its dynamic offset table: " + entryName);
                if (emptyArray)
                {
                    // No buffer is read at zero rows. Validate the on-disk fixed element width
                    // separately, since a byte-size query for zero elements always returns zero.
                    uint elementBytes = entryName switch
                    {
                        "NativeObjects_InstanceId" => snapshotVersion >= 18 ? 8u : 4u,
                        "NativeObjects_NativeTypeArrayIndex" => 4u,
                        "NativeObjects_NativeObjectAddress" or "NativeObjects_Size" => 8u,
                        _ => meta,
                    };
                    if (elementBytes != meta)
                        throw new SnapshotProblem("snapshot_column_invalid", $"Invalid empty column width {meta} for {entryName}; expected {elementBytes}.");
                }
                if (
                    format == 3
                    && (
                        entryName == "Metadata_CaptureFlags"
                        || entryName == "ProfileTarget_Info"
                        || entryName == "NativeObjects_InstanceId"
                        || entryName == "NativeObjects_NativeObjectAddress"
                        || entryName == "NativeObjects_Size"
                        || entryName == "NativeObjects_NativeTypeArrayIndex"
                    )
                )
                    throw new SnapshotProblem("snapshot_column_invalid", "Expected a fixed-width native column: " + entryName);
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
                if (entryName == "Metadata_Version" || entryName == "Metadata_CaptureFlags")
                {
                    // Version is a single element. The equivalent one-element fixed array for
                    // capture flags starts at byte zero of its block.
                    bool fixedFlag = entryName == "Metadata_CaptureFlags" && format == 2 && last == 1;
                    ulong start = fixedFlag ? 0 : last;
                    if (
                        (format != 1 && !fixedFlag)
                        || meta != 4
                        || start > blockTotals[block]
                        || 4UL > blockTotals[block] - start
                        || 4UL > blockChunks[block] - start % blockChunks[block]
                    )
                        throw new SnapshotProblem("snapshot_column_invalid", entryName + " must contain one four-byte scalar before native open.");
                    long restore = input.Position;
                    ulong chunkIndex = start / blockChunks[block];
                    At(checked(blockOffsets[block] + 16 + chunkIndex * 8), 8);
                    ulong position = file.ReadUInt64();
                    At(checked(position + start % blockChunks[block]), 4);
                    uint scalar = file.ReadUInt32();
                    if (entryName == "Metadata_Version")
                    {
                        snapshotVersion = scalar;
                        if (scalar < MinimumFormat || scalar > maximumFormat)
                            throw new SnapshotProblem(
                                "snapshot_format_unsupported",
                                $"Only inspected snapshot formats {MinimumFormat}–{maximumFormat} are supported; actual {scalar}."
                            );
                    }
                    else
                        hasNativeObjects = (scalar & 2) != 0;
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
                    if (strings && previous != 0)
                        throw new SnapshotProblem("snapshot_column_invalid", "The native string offset table must start at zero.");
                    for (uint i = 0; i < meta; i++)
                    {
                        ulong next = file.ReadUInt64();
                        if (next < previous || next > blockTotals[block])
                            throw new SnapshotProblem("snapshot_column_invalid", "Invalid dynamic entry offsets.");
                        if (strings && (next - previous > 4096 || previous == blockTotals[block] && previous % blockChunks[block] == 0))
                            throw new SnapshotProblem("snapshot_column_invalid", "Native string exceeds its read budget or starts beyond the last chunk.");
                        previous = next;
                    }
                }
                if (metadataBytes > 32L * 1024 * 1024)
                    throw new SnapshotProblem("memory_work_limit", "Snapshot native open metadata exceeds 32 MiB.");
            }
            if (snapshotVersion == 0)
                throw new SnapshotProblem("snapshot_format_unsupported", "Snapshot has no supported version scalar.");
            if (hasNativeObjects)
            {
                void RequireChapter(string name)
                {
                    int index = Convert.ToInt32(Enum.Parse(entryType, name));
                    if (index >= entryOffsets.Length || entryOffsets[index] == 0)
                        throw new SnapshotProblem("snapshot_column_invalid", "Native capture is missing required chapter " + name + ".");
                }
                RequireChapter("NativeTypes_Name");
                RequireChapter("NativeObjects_NativeTypeArrayIndex");
                RequireChapter("NativeObjects_InstanceId");
                RequireChapter("NativeObjects_Name");
                RequireChapter("NativeObjects_NativeObjectAddress");
                RequireChapter("NativeObjects_Size");
                if (snapshotVersion >= SessionInfoFormat)
                    RequireChapter("ProfileTarget_Info");
            }
        }
    }
}
