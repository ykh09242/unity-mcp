using System;
using System.Collections.Generic;
using System.Text;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal sealed class WirePeer
{
    // Keep every admitted ticket so a second owner cannot be hidden by replacing the first.
    private readonly List<(CommandResumeJournal.Ticket Ticket, Command Command, int Connection)> owners = new();
    private readonly HashSet<CommandResumeJournal.Ticket> started = new();
    private CommandResumeJournal journal;
    private int writes;

    private object Step(JObject step)
    {
        switch ((string)step["op"])
        {
            case "observe_command":
                return ObserveCommand((string)step["wire"]);
            case "native_result":
                return PreservationResult((string)step["id"], NativeReferences());
            case "reset":
                journal = new CommandResumeJournal(() => null, _ => { }, () => 1000);
                owners.Clear();
                started.Clear();
                writes = 0;
                return new { session = journal.SessionId, generation = NativeCommandArbitrator.Shared.Generation };
            case "request":
                Command command = TransportJson.DeserializeCommand((string)step["wire"]);
                var admission = journal.Admit(command.resume, command.type, command.@params);
                if (admission.Ticket != null)
                    owners.Add((admission.Ticket, command, (int)step["connection"]));
                return Frame(admission, command, (int)step["connection"], (string)step["fault"]);
            case "start":
                foreach (var owner in owners)
                    if (journal.TryStart(owner.Ticket))
                    {
                        writes++;
                        started.Add(owner.Ticket);
                    }
                return new { writes };
            case "complete":
                var frames = new List<object>();
                foreach (var owner in owners.ToArray())
                    if (started.Remove(owner.Ticket))
                    {
                        var payload = new JObject { ["success"] = true, ["data"] = owner.Command.@params.DeepClone() };
                        var settled = journal.Complete(owner.Ticket, payload);
                        frames.Add(Frame(settled, owner.Command, owner.Connection, null));
                        journal.Release(owner.Ticket);
                        owners.Remove(owner);
                    }
                return new { writes, frames };
            default:
                throw new ArgumentException("Unknown fixture event");
        }
    }

    private static object ObserveCommand(string wire)
    {
        var payload = TransportJson.ParseObject(wire);
        var parameters = payload.Value<JObject>("params") ?? new JObject();
        return new { received = parameters.ToString(Formatting.None), response = PreservationResult(payload.Value<string>("id"), parameters) };
    }

    private static object NativeReferences()
    {
        const string path = "Assets/장면/카페.unity";
        return new
        {
            references = new Dictionary<string, object>[]
            {
                new() { ["fileID"] = long.MaxValue, ["assetPath"] = path },
                new()
                {
                    ["fileID"] = long.MinValue,
                    ["assetPath"] = path.Normalize(NormalizationForm.FormD),
                    ["optional"] = null,
                },
            },
        };
    }

    private static object PreservationResult(string id, object result)
    {
        var response = TransportCommandResponse.FromObject(new { status = "success", result });
        return new { stdio = response.ToJson(), websocket = TransportCommandResponse.ToWebSocketJson(id, response) };
    }

    private object Frame(CommandResumeJournal.Admission admission, Command command, int connection, string fault)
    {
        // Corrupt only the specified field of a real receipt before the production serializer writes it.
        switch (fault)
        {
            case "omitted":
                admission.Receipt.Remove("state");
                break;
            case "null":
                admission.Receipt["state"] = JValue.CreateNull();
                break;
            case "unknown":
                admission.Receipt["state"] = "future-state";
                break;
        }
        var response = admission.Response(command.arbitration);
        string attemptId = (string)command.arbitration["request_nonce"];
        return new
        {
            state = admission.State,
            accepted = admission.Ticket != null,
            connection,
            writes,
            attempt = attemptId,
            stdio = response.ToJson(),
            websocket = TransportCommandResponse.ToWebSocketJson(attemptId, response),
        };
    }

    public static int Main()
    {
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            var peer = new WirePeer();
            string line;
            while ((line = Console.ReadLine()) != null)
            {
                object result;
                try
                {
                    result = peer.Step(JObject.Parse(line));
                }
                catch (Exception error)
                {
                    // Preserve a failing transition for the breadth-first counterexample.
                    result = new { peer_error = error.GetType().Name, message = error.Message };
                }
                Console.WriteLine(JsonConvert.SerializeObject(result));
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
