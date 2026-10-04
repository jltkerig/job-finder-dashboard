// Job Finder.exe: double-click launcher that runs start.ps1 from its own folder without a console window.
// Rebuild: C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /target:winexe /out:"Job Finder.exe" launcher.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static void Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(folder, "start.ps1");
        if (!File.Exists(script))
        {
            MessageBox.Show("start.ps1 was not found next to Job Finder.exe.", "Job Finder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var info = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"")
        {
            WorkingDirectory = folder,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using (var process = Process.Start(info))
        {
            string output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                string[] lines = output.Trim().Split('\n');
                string tail = string.Join("\n", lines, Math.Max(0, lines.Length - 12), Math.Min(12, lines.Length));
                MessageBox.Show("Job Finder could not start.\n\n" + tail, "Job Finder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
