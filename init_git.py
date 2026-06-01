import subprocess
import os

os.chdir("D:/projects/fund-tracker")

cmds = [
    ["git", "config", "advice.defaultBranchName", "false"],
    ["git", "add", "."],
    ["git", "commit", "-m", "Initial commit: fund-tracker project"],
    ["git", "branch", "-m", "master", "main"],
]

for cmd in cmds:
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    print(f"exit code: {result.returncode}")
    print("-" * 50)
