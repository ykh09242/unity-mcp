using System;
using System.IO;
using MCPForUnity.Editor.Services.Transport.Transports;

// Complete production sources are compiled beside this process; no libc stubs.
internal static class PosixCredentialRoundTripHarness
{
    public static int Main(string[] args)
    {
        if (args.Length != 1)
            return 1;
        string generation = Console.ReadLine();
        string token = Console.ReadLine();
        StdioLaunchCredential published = null;
        bool disposed = false;
        try
        {
            published = new StdioLaunchCredential(generation, token, args[0]);
            token = null;
            Console.WriteLine("READY");
            Console.Out.Flush();
            if (Console.ReadLine() != "dispose")
                return 1;
            published.Dispose();
            disposed = true;
            Console.WriteLine("DISPOSED");
            return 0;
        }
        catch (IOException)
        {
            Console.WriteLine("REJECTED");
            return 2;
        }
        catch (ArgumentException)
        {
            Console.WriteLine("INVALID");
            return 3;
        }
        catch (Exception error)
        {
            // Exception messages/locals may contain credential content; report type only.
            Console.WriteLine("FAIL " + error.GetType().Name);
            return 1;
        }
        finally
        {
            token = null;
            if (!disposed)
                published?.Dispose();
        }
    }
}
