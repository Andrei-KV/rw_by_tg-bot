import subprocess

from dotenv import load_dotenv

load_dotenv()
# Выполнить "ls -la"
result = subprocess.run(["ls", "-la"], capture_output=True, text=True)

print("Код возврата:", result.returncode)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
