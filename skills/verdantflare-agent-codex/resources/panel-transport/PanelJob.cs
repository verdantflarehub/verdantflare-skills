using System.ComponentModel;
using System.Runtime.InteropServices;

internal static class PanelJob
{
    // The OS closes the non-inheritable job handle when the transport exits.
    // Assign before starting the server so that every child inherits membership.
    internal static void BindCurrentProcess()
    {
        var job = CreateJobObjectW(0, null);
        if (job == 0)
            throw new Win32Exception();
        var limits = new ExtendedLimits { Basic = new BasicLimits { Flags = 0x2000 } };
        if (!SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf<ExtendedLimits>()) ||
            !AssignProcessToJobObject(job, GetCurrentProcess()))
        {
            var error = new Win32Exception();
            CloseHandle(job);
            throw error;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        public long ProcessTime, JobTime;
        public uint Flags;
        public nuint MinimumWorkingSet, MaximumWorkingSet;
        public uint ActiveProcesses;
        public nuint Affinity;
        public uint Priority, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public BasicLimits Basic;
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        public nuint ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    [DllImport("kernel32", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern nint CreateJobObjectW(nint attributes, string? name);
    [DllImport("kernel32", SetLastError = true)]
    private static extern bool SetInformationJobObject(nint job, int kind, ref ExtendedLimits limits, int size);
    [DllImport("kernel32", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(nint job, nint process);
    [DllImport("kernel32")]
    private static extern nint GetCurrentProcess();
    [DllImport("kernel32")]
    private static extern bool CloseHandle(nint handle);
}
