using System.Buffers.Binary;
using System.Text;
using System.Text.Json;

namespace UnityMcp.ScriptDiagnostics;

public static class WireProtocol
{
    public const int MaxRequestBytes = 272 * 1024 * 1024;
    public const int MaxResultBytes = 9 * 1024 * 1024;
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        MaxDepth = 32,
    };

    public static async Task<AnalysisRequest> ReadRequestAsync(Stream stream, CancellationToken cancellation = default)
    {
        var prefix = new byte[4];
        await stream.ReadExactlyAsync(prefix, cancellation);
        int length = BinaryPrimitives.ReadInt32LittleEndian(prefix);
        if (length <= 0 || length > MaxRequestBytes)
            throw new InvalidDataException("request_frame_limit");
        // A bounded spool avoids another context-sized byte array while reading the frame.
        using var payload = new MemoryStream(Math.Min(length, 65536));
        var buffer = new byte[65536];
        for (int remaining = length; remaining > 0; )
        {
            int read = await stream.ReadAsync(buffer.AsMemory(0, Math.Min(buffer.Length, remaining)), cancellation);
            if (read == 0)
                throw new EndOfStreamException("incomplete_request_frame");
            await payload.WriteAsync(buffer.AsMemory(0, read), cancellation);
            remaining -= read;
        }
        payload.Position = 0;
        var request = await JsonSerializer.DeserializeAsync<AnalysisRequest>(payload, Json, cancellation) ?? throw new InvalidDataException("request_required");
        Validate(request);
        return request;
    }

    internal static void Validate(AnalysisRequest request)
    {
        if (request.Protocol != 1)
            throw new InvalidDataException("protocol_mismatch");
        if (request.SourceToken.Length != 64 || !request.SourceToken.All(Uri.IsHexDigit))
            throw new InvalidDataException("source_token_required");
        if (request.SelectedPaths.Length is < 1 or > 256 || request.Assemblies.Length is < 1 or > 512)
            throw new InvalidDataException("selection_or_assembly_limit");
        var comparer = OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal;
        var selected = request.SelectedPaths.Select(p => AnalyzerConfiguration.FullPath(request.ProjectRoot, p)).ToHashSet(comparer);
        if (selected.Count != request.SelectedPaths.Length)
            throw new InvalidDataException("duplicate_selection");
        var seen = new HashSet<string>(comparer);
        long total = 0,
            selectedBytes = 0;
        int documents = 0;
        foreach (var assembly in request.Assemblies)
        {
            if (assembly.References.Length > 4096 || assembly.Defines.Length > 4096)
                throw new InvalidDataException("context_limit");
            foreach (var source in assembly.Sources.Concat(assembly.Configs).Concat(assembly.AdditionalFiles))
            {
                if (++documents > 100000)
                    throw new InvalidDataException("context_limit");
                string path = AnalyzerConfiguration.FullPath(request.ProjectRoot, source.Path);
                int bytes = Encoding.UTF8.GetByteCount(source.Text);
                total += bytes;
                if (total > 256L * 1024 * 1024)
                    throw new InvalidDataException("context_limit");
                if (selected.Contains(path) && seen.Add(path))
                {
                    selectedBytes += bytes;
                    if (bytes > 2 * 1024 * 1024 || selectedBytes > 16 * 1024 * 1024)
                        throw new InvalidDataException("selection_bytes_limit");
                }
            }
        }
        if (!selected.SetEquals(seen))
            throw new InvalidDataException("selected_source_missing");
    }

    public static async Task WriteResultAsync(Stream stream, AnalysisResult result, CancellationToken cancellation = default)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(result, Json);
        if (bytes.Length > MaxResultBytes)
            throw new InvalidDataException("result_frame_limit");
        var prefix = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(prefix, bytes.Length);
        await stream.WriteAsync(prefix, cancellation);
        await stream.WriteAsync(bytes, cancellation);
        await stream.FlushAsync(cancellation);
    }
}
