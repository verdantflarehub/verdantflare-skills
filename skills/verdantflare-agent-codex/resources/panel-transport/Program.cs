using System.Diagnostics;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;

if (args.Length == 1 && Path.IsPathFullyQualified(args[0]))
    return await Relay(args[0], CancellationToken.None);

// Invoked as the CLI of an explicitly configured VS Code window.
var upstream = Environment.GetEnvironmentVariable("VERDANTFLARE_CODEX_PANEL_CLI");
var endpoint = Environment.GetEnvironmentVariable("VERDANTFLARE_CODEX_PANEL_SOCKET");
var automatic = string.IsNullOrEmpty(upstream) && string.IsNullOrEmpty(endpoint) && OperatingSystem.IsWindows();
if (automatic)
    upstream = WindowBinding.BundledCli();
if (string.IsNullOrWhiteSpace(upstream) || !Path.IsPathFullyQualified(upstream) ||
    !File.Exists(upstream) || string.Equals(upstream, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase))
    return 2;

var launch = new ProcessStartInfo(upstream) { UseShellExecute = false, CreateNoWindow = true };
foreach (var argument in args)
    launch.ArgumentList.Add(argument);
var panelServer = args.Count(argument => argument == "app-server") == 1 && args.Contains("--analytics-default-enabled");
if (automatic && panelServer)
    endpoint = await WindowBinding.Socket();
if (!panelServer || (automatic && endpoint is null))
{
    launch.RedirectStandardInput = launch.RedirectStandardOutput = launch.RedirectStandardError = true;
    using var cli = Process.Start(launch)!;
    async Task ForwardInput()
    {
        try { await Console.OpenStandardInput().CopyToAsync(cli.StandardInput.BaseStream); }
        catch (IOException) { }
        finally { cli.StandardInput.Close(); }
    }
    _ = ForwardInput();
    var stdout = cli.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
    var stderr = cli.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
    await cli.WaitForExitAsync();
    await Task.WhenAll(stdout, stderr);
    return cli.ExitCode;
}
if (string.IsNullOrWhiteSpace(endpoint) || !Path.IsPathFullyQualified(endpoint) ||
    args.Contains("--listen") || args.Contains("--stdio"))
    return 2;

using var endpointLease = PanelEndpoint.TryAcquire(endpoint);
if (endpointLease is null)
    return 2;
launch.ArgumentList.Add("--listen");
launch.ArgumentList.Add("unix://" + endpoint);
launch.RedirectStandardOutput = true;
if (OperatingSystem.IsWindows())
    PanelJob.BindCurrentProcess();
using var server = Process.Start(launch)!;
using var stopped = new CancellationTokenSource();
var exited = server.WaitForExitAsync();
try
{
    using var startup = new CancellationTokenSource(TimeSpan.FromSeconds(15));
    while (!File.Exists(endpoint) && !server.HasExited)
        await Task.Delay(50, startup.Token);
    if (server.HasExited)
        return server.ExitCode;
    var relay = Relay(endpoint, stopped.Token);
    if (await Task.WhenAny(relay, exited) == exited)
        return server.ExitCode;
    return await relay;
}
finally
{
    stopped.Cancel();
    if (!server.HasExited)
    {
        server.Kill(entireProcessTree: true);
        await server.WaitForExitAsync();
    }
}

static async Task<int> Relay(string endpoint, CancellationToken stop)
{
    using var handler = new SocketsHttpHandler { UseProxy = false };
    handler.ConnectCallback = async (_, token) =>
    {
        var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        try
        {
            await socket.ConnectAsync(new UnixDomainSocketEndPoint(endpoint), token);
            return new NetworkStream(socket, ownsSocket: true);
        }
        catch
        {
            socket.Dispose();
            throw;
        }
    };
    using var http = new HttpMessageInvoker(handler);
    using var websocket = new ClientWebSocket();
    using var shutdown = CancellationTokenSource.CreateLinkedTokenSource(stop);
    try
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await websocket.ConnectAsync(new Uri("ws://localhost/rpc"), http, deadline.Token);
        var send = Task.Run(async () =>
        {
            using var input = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false, true));
            while (await input.ReadLineAsync(shutdown.Token) is string message)
            {
                await websocket.SendAsync(Encoding.UTF8.GetBytes(message).AsMemory(),
                    WebSocketMessageType.Text, true, shutdown.Token);
            }
        });
        var receive = Task.Run(async () =>
        {
            var output = Console.OpenStandardOutput();
            var buffer = new byte[32768];
            var newline = new byte[] { 10 };
            while (!shutdown.IsCancellationRequested)
            {
                var part = await websocket.ReceiveAsync(buffer.AsMemory(), shutdown.Token);
                if (part.MessageType == WebSocketMessageType.Close)
                    return;
                if (part.MessageType != WebSocketMessageType.Text)
                    throw new IOException("Invalid RPC frame");
                await output.WriteAsync(buffer.AsMemory(0, part.Count), shutdown.Token);
                if (part.EndOfMessage)
                {
                    await output.WriteAsync(newline, shutdown.Token);
                    await output.FlushAsync(shutdown.Token);
                }
            }
        });
        await await Task.WhenAny(send, receive);
        return 0;
    }
    catch (Exception error) when (error is IOException or WebSocketException or OperationCanceledException or SocketException)
    {
        Console.Error.WriteLine("Panel transport disconnected");
        return 1;
    }
    finally
    {
        shutdown.Cancel();
        websocket.Abort();
    }
}
