using System.Net.Sockets;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

internal static class PanelEndpoint
{
    internal static FileStream? TryAcquire(string path)
    {
        FileStream lease;
        try
        {
            lease = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }
        catch (IOException)
        {
            return null;
        }
        try
        {
            if (!File.Exists(path) || (OperatingSystem.IsWindows() && RemoveDisconnectedSocket(path)))
                return lease;
        }
        catch
        {
            lease.Dispose();
            throw;
        }
        lease.Dispose();
        return null;
    }

    private static bool RemoveDisconnectedSocket(string path)
    {
        // Pin the reparse point itself. Query and delete via the same handle.
        const uint deleteAndReadAttributes = 0x10080, openExisting = 3, openReparsePoint = 0x00200000;
        const uint unixSocketTag = 0x80000023;
        using var file = CreateFileW(path, deleteAndReadAttributes, FileShare.ReadWrite, 0, openExisting, openReparsePoint, 0);
        if (file.IsInvalid || !GetFileInformationByHandleEx(file, 9, out var info, 8) || info.Tag != unixSocketTag)
            return false;
        using var probe = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        try
        {
            probe.Connect(new UnixDomainSocketEndPoint(path));
            return false;
        }
        catch (SocketException error) when (error.SocketErrorCode == SocketError.ConnectionRefused)
        {
            int deleteFile = 1;
            return SetFileInformationByHandle(file, 4, ref deleteFile, sizeof(int));
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct AttributeTag { public uint Attributes, Tag; }
    [DllImport("kernel32", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, FileShare share, nint security,
        uint disposition, uint flags, nint template);
    [DllImport("kernel32")]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int kind, out AttributeTag info, int size);
    [DllImport("kernel32")]
    private static extern bool SetFileInformationByHandle(SafeFileHandle file, int kind, ref int disposition, int size);
}
