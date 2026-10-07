using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

internal sealed class LocalWebSocketPeer : IDisposable
{
    private readonly TcpListener _listener = new TcpListener(IPAddress.Loopback, 0);
    private readonly object _sendGate = new object();
    private TcpClient _client;
    private NetworkStream _stream;
    public readonly ConcurrentQueue<JObject> Received = new ConcurrentQueue<JObject>();
    public readonly ConcurrentQueue<byte[]> Binary = new ConcurrentQueue<byte[]>();
    public readonly ConcurrentQueue<string> Order = new ConcurrentQueue<string>();
    public Action<JObject> OnStart;
    public Task Ready { get; }
    public Task Reader { get; private set; }
    public Uri Uri { get; }

    public LocalWebSocketPeer()
    {
        _listener.Start();
        Uri = new Uri("ws://127.0.0.1:" + ((IPEndPoint)_listener.LocalEndpoint).Port + "/fixture");
        Ready = Task.Run(Accept);
    }

    private void Accept()
    {
        _client = _listener.AcceptTcpClient();
        _stream = _client.GetStream();
        var header = new StringBuilder();
        while (!header.ToString().EndsWith("\r\n\r\n", StringComparison.Ordinal))
            header.Append((char)_stream.ReadByte());
        string key = null;
        foreach (var line in header.ToString().Split(new[] { "\r\n" }, StringSplitOptions.None))
            if (line.StartsWith("Sec-WebSocket-Key:", StringComparison.OrdinalIgnoreCase))
                key = line.Substring(line.IndexOf(':') + 1).Trim();
        using var sha = SHA1.Create();
        string accept = Convert.ToBase64String(sha.ComputeHash(Encoding.ASCII.GetBytes(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
        byte[] reply = Encoding.ASCII.GetBytes(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n"
        );
        _stream.Write(reply, 0, reply.Length);
        Reader = Task.Run(ReadFrames);
    }

    public void Send(JObject message)
    {
        byte[] payload = Encoding.UTF8.GetBytes(message.ToString(Newtonsoft.Json.Formatting.None));
        if (payload.Length > 65535)
            throw new InvalidOperationException("Fixture messages must be small");
        lock (_sendGate)
        {
            _stream.WriteByte(0x81);
            if (payload.Length < 126)
                _stream.WriteByte((byte)payload.Length);
            else
            {
                _stream.WriteByte(126);
                _stream.WriteByte((byte)(payload.Length >> 8));
                _stream.WriteByte((byte)payload.Length);
            }
            _stream.Write(payload, 0, payload.Length);
        }
    }

    private byte[] ReadExact(int size)
    {
        var bytes = new byte[size];
        for (int at = 0; at < size; )
        {
            int read = _stream.Read(bytes, at, size - at);
            if (read == 0)
                throw new EndOfStreamException();
            at += read;
        }
        return bytes;
    }

    private void ReadFrames()
    {
        try
        {
            while (true)
            {
                byte[] first = ReadExact(2);
                int opcode = first[0] & 15;
                int length = first[1] & 127;
                if (length == 126)
                {
                    var expanded = ReadExact(2);
                    length = expanded[0] * 256 + expanded[1];
                }
                else if (length == 127)
                {
                    var expanded = ReadExact(8);
                    length = checked(expanded[4] * 16777216 + expanded[5] * 65536 + expanded[6] * 256 + expanded[7]);
                }
                byte[] mask = (first[1] & 128) != 0 ? ReadExact(4) : null;
                byte[] payload = ReadExact(length);
                if (mask != null)
                    for (int i = 0; i < length; i++)
                        payload[i] ^= mask[i % 4];
                if (opcode == 8)
                    return;
                if (opcode == 2)
                {
                    Binary.Enqueue(payload);
                    Order.Enqueue("binary");
                }
                if (opcode == 1)
                {
                    var message = JObject.Parse(Encoding.UTF8.GetString(payload));
                    Order.Enqueue(message.Value<string>("type"));
                    Received.Enqueue(message);
                    if (message.Value<string>("type") == "result_start")
                        OnStart?.Invoke(message);
                }
            }
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    public void Dispose()
    {
        _client?.Dispose();
        _listener.Stop();
    }
}
