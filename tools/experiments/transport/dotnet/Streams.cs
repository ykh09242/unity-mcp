using System.Buffers.Binary;
using System.IO.Pipes;
using System.Net;
using System.Net.Sockets;

// Owns actual OS stream endpoints. Same-process tasks exclude process scheduling.
internal sealed class Streams(Stream server, Stream client, string? path = null) : IAsyncDisposable
{
    public Stream Server { get; } = server;
    public Stream Client { get; } = client;

    public static async Task<Streams> Open(string kind, CancellationToken token, bool boundedBuffers = false)
    {
        if (kind == "pipe")
        {
            var name = "unity-mcp-owned-" + Guid.NewGuid().ToString("N");
            var server = new NamedPipeServerStream(
                name,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly,
                65536,
                65536
            );
            var client = new NamedPipeClientStream(".", name, PipeDirection.InOut, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            await Task.WhenAll(server.WaitForConnectionAsync(token), client.ConnectAsync(token));
            return new Streams(server, client);
        }
        string? path = kind == "unix" ? Path.Combine(Path.GetTempPath(), "umcp-" + Guid.NewGuid().ToString("N")[..12] + ".sock") : null;
        using var listener = new Socket(path is null ? AddressFamily.InterNetwork : AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        EndPoint endpoint = path is null ? new IPEndPoint(IPAddress.Loopback, 0) : new UnixDomainSocketEndPoint(path);
        listener.Bind(endpoint);
        listener.Listen(1);
        var clientSocket = new Socket(listener.AddressFamily, SocketType.Stream, ProtocolType.Unspecified);
        if (boundedBuffers)
        {
            clientSocket.SendBufferSize = 65536;
            clientSocket.ReceiveBufferSize = 65536;
        }
        if (path is null)
            clientSocket.NoDelay = true;
        var accept = listener.AcceptAsync(token).AsTask();
        await clientSocket.ConnectAsync(listener.LocalEndPoint!, token);
        var serverSocket = await accept;
        if (boundedBuffers)
            serverSocket.SendBufferSize = 65536;
        if (path is null)
            serverSocket.NoDelay = true;
        return new Streams(new NetworkStream(serverSocket, ownsSocket: true), new NetworkStream(clientSocket, ownsSocket: true), path);
    }

    public async ValueTask DisposeAsync()
    {
        await Server.DisposeAsync();
        await Client.DisposeAsync();
        if (path is not null)
            File.Delete(path);
    }
}

internal static class Frames
{
    public const int MaxBytes = 8 * 1024 * 1024;

    public static async Task Write(Stream stream, ReadOnlyMemory<byte> value, CancellationToken token)
    {
        if (value.Length > MaxBytes)
            throw new InvalidDataException("Frame capacity exceeded");
        byte[] header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, value.Length);
        await stream.WriteAsync(header, token);
        await stream.WriteAsync(value, token);
        await stream.FlushAsync(token);
    }

    public static async Task<byte[]> Read(Stream stream, CancellationToken token, int delayMs = 0)
    {
        byte[] header = new byte[4];
        await stream.ReadExactlyAsync(header, token);
        int length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length < 0 || length > MaxBytes)
            throw new InvalidDataException("Frame capacity exceeded");
        byte[] value = new byte[length];
        for (int offset = 0; offset < length; )
        {
            int count = Math.Min(65536, length - offset);
            await stream.ReadExactlyAsync(value.AsMemory(offset, count), token);
            offset += count;
            if (delayMs > 0)
                await Task.Delay(delayMs, token);
        }
        return value;
    }
}
