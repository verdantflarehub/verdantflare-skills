using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

internal static class WindowBinding
{
    [DllImport("ntdll.dll")]
    private static extern int NtQueryInformationProcess(nint process, int kind, [Out] nint[] info, int size, out int used);

    internal static async Task<string?> Socket()
    {
        var info = new nint[6];
        using var self = Process.GetCurrentProcess();
        if (NtQueryInformationProcess(self.Handle, 0, info, info.Length * IntPtr.Size, out _) != 0)
            return null;
        using var parent = Process.GetProcessById((int)info[5]);
        if (!parent.ProcessName.Equals("Code", StringComparison.OrdinalIgnoreCase))
            return null;
        var file = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "Code", "User", "globalStorage", "verdantflare.codex-panel-window", "windows", parent.Id + ".binding");
        for (int attempt = 0; attempt < 60; attempt++)
        {
            if (File.Exists(file))
            {
                using var binding = JsonDocument.Parse(await File.ReadAllTextAsync(file));
                var root = binding.RootElement;
                var started = DateTimeOffset.FromUnixTimeMilliseconds((long)root.GetProperty("started").GetDouble());
                if (root.GetProperty("pid").GetInt32() == parent.Id &&
                    Math.Abs((parent.StartTime.ToUniversalTime() - started.UtcDateTime).TotalSeconds) < 3)
                    return root.GetProperty("socket").GetString();
            }
            await Task.Delay(100);
        }
        return null;
    }

    internal static string? BundledCli()
    {
        // Codex appends its own platform binary directory to PATH when spawning its CLI.
        var last = (Environment.GetEnvironmentVariable("PATH") ?? "").TrimEnd(';').Split(';')[^1];
        if (!Path.IsPathFullyQualified(last)) return null;
        var bin = new DirectoryInfo(last);
        var extension = bin.Parent?.Parent;
        if (extension is null || !extension.Name.StartsWith("openai.chatgpt-", StringComparison.OrdinalIgnoreCase))
            return null;
        return Path.Combine(bin.FullName, "codex.exe");
    }
}
