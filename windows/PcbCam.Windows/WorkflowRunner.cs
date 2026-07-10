using System.Diagnostics;
using System.IO;
using System.Text;

namespace PcbCam.Windows;

internal sealed record WorkflowProgress(int Percent, string Stage, string? Line = null);

internal sealed class WorkflowRunner
{
    private readonly string _root;

    public WorkflowRunner(string root) => _root = root;

    public static IReadOnlyList<string> Validate(string root)
    {
        var failures = new List<string>();
        if (!File.Exists(Path.Combine(root, "scripts", "new-project.rb")))
            failures.Add("PCB CAM workflow script is missing.");
        var python = Environment.GetEnvironmentVariable("PCB_CAM_PYTHON") ?? Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
            failures.Add("Python environment is missing (.venv\\Scripts\\python.exe).");
        var configuredPowerShell = Environment.GetEnvironmentVariable("PCB_CAM_POWERSHELL");
        if (configuredPowerShell is not null && !File.Exists(configuredPowerShell))
            failures.Add("PCB_CAM_POWERSHELL does not point to a PowerShell executable.");
        if (FindOnPath("ruby.exe") is null)
            failures.Add("Windows Ruby is not available on PATH.");
        var legacyStarter = Environment.GetEnvironmentVariable("PCB_CAM_LEGACY_NEW_PROJECT");
        if (legacyStarter is null)
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            legacyStarter = new[]
            {
                Path.Combine(home, "Desktop", "project", "scripts", "new-project.rb"),
                @"C:\Users\taf2\Desktop\project\scripts\new-project.rb",
            }.FirstOrDefault(File.Exists);
        }
        if (legacyStarter is null || !File.Exists(legacyStarter))
            failures.Add("Legacy PCB project starter is missing. Set PCB_CAM_LEGACY_NEW_PROJECT to its new-project.rb.");
        if (!File.Exists(@"C:\cygwin64\home\taf2\flatcam\camlib.py") && !File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "flatcam", "camlib.py")))
            failures.Add("FlatCAM Beta source is missing from its expected checkout location.");
        return failures;
    }

    public async Task RunAsync(
        string zipPath,
        string projectPath,
        IProgress<WorkflowProgress> progress,
        CancellationToken cancellationToken)
    {
        var ruby = FindOnPath("ruby.exe") ?? throw new InvalidOperationException("Ruby was not found on PATH.");
        var script = Path.Combine(_root, "scripts", "new-project.rb");
        var start = new ProcessStartInfo
        {
            FileName = ruby,
            WorkingDirectory = _root,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        start.ArgumentList.Add(script);
        start.ArgumentList.Add(projectPath);
        start.ArgumentList.Add(zipPath);
        start.Environment["PYTHONUNBUFFERED"] = "1";

        using var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        var output = new StringBuilder();
        process.OutputDataReceived += (_, e) => HandleLine(e.Data, output, progress);
        process.ErrorDataReceived += (_, e) => HandleLine(e.Data, output, progress);

        progress.Report(new(3, "Starting project workflow…"));
        if (!process.Start())
            throw new InvalidOperationException("The PCB CAM workflow could not be started.");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        try
        {
            await process.WaitForExitAsync(cancellationToken);
            process.WaitForExit(); // Drain redirected asynchronous output before evaluating the result.
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
            throw;
        }

        if (process.ExitCode != 0)
            throw new WorkflowException(process.ExitCode, output.ToString());

        progress.Report(new(100, "Machine files are ready."));
    }

    private static void HandleLine(
        string? line,
        StringBuilder output,
        IProgress<WorkflowProgress> progress)
    {
        if (string.IsNullOrWhiteSpace(line)) return;
        lock (output) output.AppendLine(line);

        var normalized = line.ToLowerInvariant();
        var stage = normalized switch
        {
            var s when s.Contains("prepare.rb") || s.Contains("project_starter") => new WorkflowProgress(12, "Preparing Gerber layers…", line),
            var s when s.StartsWith("gerber:") => new WorkflowProgress(28, "Reading Gerber layers…", line),
            var s when s.StartsWith("excellon:") || s.StartsWith("split drill:") => new WorkflowProgress(40, "Preparing drill groups…", line),
            var s when s.StartsWith("alignment:") => new WorkflowProgress(48, "Creating alignment geometry…", line),
            var s when s.StartsWith("isolation:") || s.StartsWith("paint:") => new WorkflowProgress(58, "Creating machining geometry…", line),
            var s when s.StartsWith("cutout:") => new WorkflowProgress(68, "Creating board cutout…", line),
            var s when s.StartsWith("wrote:") => new WorkflowProgress(78, "FlatCAM project created…", line),
            var s when s.Contains("scripts/gen.all.ps1") => new WorkflowProgress(84, "Generating Carvera NC files…", line),
            var s when s.StartsWith("flatcam project:") => new WorkflowProgress(96, "Finalizing output…", line),
            _ => new WorkflowProgress(-1, string.Empty, line),
        };
        progress.Report(stage);
    }

    private static string? FindOnPath(string executable)
    {
        foreach (var folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            try
            {
                var candidate = Path.Combine(folder.Trim('"'), executable);
                if (File.Exists(candidate)) return candidate;
            }
            catch { /* Ignore malformed PATH entries. */ }
        }
        return null;
    }
}

internal sealed class WorkflowException : Exception
{
    public int ExitCode { get; }
    public string Log { get; }

    public WorkflowException(int exitCode, string log)
        : base($"PCB CAM stopped with exit code {exitCode}.")
    {
        ExitCode = exitCode;
        Log = log;
    }
}
