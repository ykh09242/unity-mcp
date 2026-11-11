using System;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.SourceFreshness
{
    internal sealed class CompileObservation
    {
        internal string CompileId;
        internal long Generation;
        internal string Outcome;
        internal string Reason;
        internal bool Complete;
        internal bool Attributed;
        internal bool Finished;
        internal int AssemblyCount;
        internal bool ReloadObserved;
        internal AssemblyIdentity[] Assemblies = Array.Empty<AssemblyIdentity>();
    }

    internal sealed class FreshnessObservation
    {
        internal string Status = "incomplete";
        internal string Reason = "no_source_observation";
        internal SourceSnapshot Snapshot;
        internal CompileObservation Compilation;

        internal JObject ToJson(string project, string session) =>
            new JObject
            {
                ["schema"] = "unity-mcp/source_freshness@1",
                ["status"] = Status,
                ["basis"] = "stable_inputs_compile_and_load_observed",
                ["project_id"] = project,
                ["session_id"] = session,
                ["source_token"] = Snapshot?.Token,
                ["source_digest"] = Snapshot?.SourceDigest,
                ["context_digest"] = Snapshot?.ContextDigest,
                ["unity_version"] = Snapshot?.UnityVersion,
                ["observed_at_unix_ms"] = Snapshot?.ObservedAtUnixMs,
                ["compile_id"] = Compilation?.CompileId,
                ["compile_generation"] = Compilation?.Generation,
                ["source_revision_confirmed"] = false,
                ["reason"] = Reason,
                ["coverage"] = new JObject
                {
                    ["complete"] = Snapshot?.Complete ?? false,
                    ["file_count"] = Snapshot?.FileCount ?? 0,
                    ["input_bytes"] = Snapshot?.InputBytes ?? 0,
                    ["reasons"] = new JArray(Snapshot?.Reasons ?? Array.Empty<string>()),
                    ["atomic_compiler_input_provenance"] = false,
                },
                ["loaded_assemblies"] = new JArray(
                    (Compilation?.Assemblies ?? Array.Empty<AssemblyIdentity>())
                        .Take(512)
                        .Select(a => new JObject
                        {
                            ["name"] = a.Name,
                            ["output_mvid"] = a.OutputMvid,
                            ["loaded_mvid"] = a.LoadedMvid,
                        })
                ),
            };
    }

    internal sealed class AssemblyIdentity
    {
        public string Name { get; }
        public string OutputMvid { get; }
        public string LoadedMvid { get; }
        internal bool Matches => !string.IsNullOrEmpty(OutputMvid) && OutputMvid == LoadedMvid;

        [Newtonsoft.Json.JsonConstructor]
        public AssemblyIdentity(string name, string outputMvid, string loadedMvid)
        {
            Name = name;
            OutputMvid = outputMvid;
            LoadedMvid = loadedMvid;
        }

        // ECMA-335 Module.Mvid: read metadata only, without loading or executing the assembly.
        // The Module table is the first metadata table; no general-purpose table parser is needed.
        internal static string ReadModuleId(string path)
        {
            try
            {
                using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                if (stream.Length < 128 || stream.Length > 128 * 1024 * 1024)
                    return null;
                using var reader = new BinaryReader(stream, Encoding.UTF8);
                void Seek(long position, long count = 4)
                {
                    if (position < 0 || count < 0 || position > stream.Length - count)
                        throw new InvalidDataException();
                    stream.Position = position;
                }
                Seek(0x3c);
                uint pe = reader.ReadUInt32();
                Seek(pe, 24);
                if (reader.ReadUInt32() != 0x00004550)
                    return null;
                reader.ReadUInt16();
                ushort sections = reader.ReadUInt16();
                if (sections == 0 || sections > 96)
                    return null;
                Seek(pe + 20);
                ushort optionalSize = reader.ReadUInt16();
                Seek(pe + 24);
                ushort magic = reader.ReadUInt16();
                if (magic != 0x10b && magic != 0x20b)
                    return null;
                long directories = pe + 24 + (magic == 0x20b ? 112 : 96);
                Seek(directories + 14 * 8);
                uint cliRva = reader.ReadUInt32();
                long SectionOffset(uint rva)
                {
                    for (int index = 0; index < sections; index++)
                    {
                        Seek(pe + 24 + optionalSize + index * 40 + 8, 16);
                        uint virtualSize = reader.ReadUInt32(),
                            address = reader.ReadUInt32(),
                            rawSize = reader.ReadUInt32(),
                            pointer = reader.ReadUInt32();
                        if (rva >= address && (ulong)rva < (ulong)address + Math.Min(virtualSize, rawSize))
                            return (long)pointer + rva - address;
                    }
                    throw new InvalidDataException();
                }
                long cli = SectionOffset(cliRva);
                Seek(cli + 8);
                long metadata = SectionOffset(reader.ReadUInt32());
                Seek(metadata);
                if (reader.ReadUInt32() != 0x424a5342)
                    return null;
                Seek(metadata + 12);
                uint versionSize = reader.ReadUInt32();
                if (versionSize > 1024)
                    return null;
                Seek(metadata + 16 + versionSize, 4);
                reader.ReadUInt16();
                ushort streams = reader.ReadUInt16();
                if (streams > 32)
                    return null;
                long table = -1,
                    guid = -1;
                uint guidSize = 0;
                for (int index = 0; index < streams; index++)
                {
                    uint offset = reader.ReadUInt32(),
                        size = reader.ReadUInt32();
                    var name = new StringBuilder();
                    for (int c = 0; c < 32; c++)
                    {
                        byte value = reader.ReadByte();
                        if (value == 0)
                            break;
                        name.Append((char)value);
                    }
                    Seek((stream.Position + 3) & ~3L, 0);
                    if (name.ToString() == "#~" || name.ToString() == "#-")
                        table = metadata + offset;
                    if (name.ToString() == "#GUID")
                    {
                        guid = metadata + offset;
                        guidSize = size;
                    }
                }
                if (table < 0 || guid < 0)
                    return null;
                Seek(table + 6, 18);
                byte heaps = reader.ReadByte();
                reader.ReadByte();
                ulong valid = reader.ReadUInt64();
                reader.ReadUInt64();
                if ((valid & 1) == 0)
                    return null;
                uint modules = 0;
                for (int i = 0; i < 64; i++)
                    if ((valid & (1UL << i)) != 0)
                    {
                        uint rows = reader.ReadUInt32();
                        if (i == 0)
                            modules = rows;
                    }
                if (modules != 1)
                    return null;
                Seek(stream.Position + 2 + ((heaps & 1) != 0 ? 4 : 2));
                uint mvidIndex = (heaps & 2) != 0 ? reader.ReadUInt32() : reader.ReadUInt16();
                if (mvidIndex == 0 || (ulong)mvidIndex * 16 > guidSize)
                    return null;
                Seek(guid + (mvidIndex - 1L) * 16, 16);
                return new Guid(reader.ReadBytes(16)).ToString("N");
            }
            catch (Exception error)
                when (error is IOException || error is UnauthorizedAccessException || error is ArgumentException || error is OverflowException)
            {
                return null;
            }
        }
    }
}
