using System.Security.Cryptography;
using System.Text.Json;

internal sealed record Hello(string Session, int Generation, string Role, string Token);

// Synthetic credentials never reach product endpoints or evidence output.
internal sealed class SessionBinding : IDisposable
{
    private readonly string identity = Guid.NewGuid().ToString("N");
    private readonly byte[] secret = RandomNumberGenerator.GetBytes(32);
    private readonly CancellationTokenSource lifetime = new(TimeSpan.FromSeconds(15));
    private readonly HashSet<string> roles = [];
    public CancellationToken Token => lifetime.Token;
    public Hello Greeting(string role) => new(identity, 1, role, Convert.ToHexString(secret));

    public async Task<bool> Authenticate(Streams streams, Hello greeting, string expectedRole)
    {
        var incoming = Frames.Read(streams.Server, Token);
        await Frames.Write(streams.Client, JsonSerializer.SerializeToUtf8Bytes(greeting), Token);
        var hello = JsonSerializer.Deserialize<Hello>(await incoming)
            ?? throw new InvalidDataException("Missing handshake");
        bool accepted = !lifetime.IsCancellationRequested && hello.Session == identity
            && hello.Generation == 1 && hello.Role == expectedRole
            && hello.Token.Length == 64
            && hello.Token.All(c => "0123456789ABCDEFabcdef".Contains(c))
            && !roles.Contains(expectedRole)
            && CryptographicOperations.FixedTimeEquals(Convert.FromHexString(hello.Token), secret);
        if (accepted) roles.Add(expectedRole);
        await Frames.Write(streams.Server, new byte[] { accepted ? (byte)1 : (byte)0 }, Token);
        var response = await Frames.Read(streams.Client, Token);
        if (response[0] != (accepted ? 1 : 0)) throw new InvalidDataException("Handshake response mismatch");
        return accepted;
    }

    public async Task MonitorControl(Stream control)
    {
        byte[] value = new byte[1];
        int count = await control.ReadAsync(value, Token);
        if (count != 0) throw new InvalidDataException("Unexpected control frame");
        lifetime.Cancel();
    }

    public void Dispose() => lifetime.Dispose();
}
