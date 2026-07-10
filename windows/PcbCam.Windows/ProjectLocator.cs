using System.IO;

namespace PcbCam.Windows;

internal static class ProjectLocator
{
    public static string? FindRoot()
    {
        var configured = Environment.GetEnvironmentVariable("PCB_CAM_ROOT");
        if (IsRoot(configured)) return Path.GetFullPath(configured!);

        foreach (var origin in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            var directory = new DirectoryInfo(origin);
            while (directory is not null)
            {
                if (IsRoot(directory.FullName)) return directory.FullName;
                directory = directory.Parent;
            }
        }
        return null;
    }

    private static bool IsRoot(string? path) =>
        !string.IsNullOrWhiteSpace(path) &&
        File.Exists(Path.Combine(path, "scripts", "new-project.rb")) &&
        Directory.Exists(Path.Combine(path, "pcb_cam"));
}
