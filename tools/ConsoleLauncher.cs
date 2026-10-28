using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class ConsoleLauncher
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "launch-console.ps1");
        if (!File.Exists(script))
        {
            MessageBox.Show("Keep RTLForge.exe beside launch-console.ps1 in the project folder.");
            return;
        }
        Process.Start(new ProcessStartInfo {
            FileName = "powershell.exe",
            Arguments = "-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File \"" + script + "\"",
            WorkingDirectory = root,
            UseShellExecute = true
        });
    }
}
