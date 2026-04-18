import subprocess
import os
import shutil
import time

CLI = ["python3", "cli.py"]
TEST_DIR = "test_repo"

def run_cmd(cmd, input_text=None):
    print(f"Running: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input=input_text)
    if process.returncode != 0:
        print(f"Error: {stderr}")
    return process.returncode, stdout, stderr

def setup_test_repo():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)
    os.chdir(TEST_DIR)
    # Copy cli.py and vault_ai to test_dir for testing
    shutil.copy("../cli.py", ".")
    shutil.copytree("../vault_ai", "./vault_ai")
    # Also need remote_storage for sync test
    shutil.copytree("../remote_storage", "./remote_storage")
    shutil.copy("../server.py", ".")

def test_flow():
    # 1. Init
    rc, out, err = run_cmd(CLI + ["init"])
    assert rc == 0, "Init failed"
    
    # 2. Setup (mocking input)
    # 1 (Ollama), http://localhost:11434, llama3.2
    rc, out, err = run_cmd(CLI + ["setup"], input_text="1\n\n\n")
    assert rc == 0, "Setup failed"

    # 3. Create a file and save
    with open("hello.py", "w") as f:
        f.write("print('hello world')\n")
    
    rc, out, err = run_cmd(CLI + ["save", "-m", "Initial commit"])
    assert rc == 0, "Save failed"

    # 4. History
    rc, out, err = run_cmd(CLI + ["history"])
    assert rc == 0, "History failed"
    assert "Initial commit" in out

    # 5. Diff
    with open("hello.py", "a") as f:
        f.write("print('new line')\n")
    rc, out, err = run_cmd(CLI + ["diff"])
    assert rc == 0, "Diff failed"
    assert "hello.py" in out

    # 6. Scan
    rc, out, err = run_cmd(CLI + ["scan"])
    assert rc == 0, "Scan failed"

    # 7. Create Branch
    rc, out, err = run_cmd(CLI + ["create", "branch", "feature"])
    assert rc == 0, "Create branch failed"

    # 8. Snapshot
    rc, out, err = run_cmd(CLI + ["snapshot", "-l", "test-snap"])
    assert rc == 0, "Snapshot failed"

    # 9. Env
    rc, out, err = run_cmd(CLI + ["env", "show"])
    assert rc == 0, "Env show failed"

    # 10. Undo (Careful here, might need more setup)
    # Let's save another first
    run_cmd(CLI + ["save", "-m", "Second commit"])
    rc, out, err = run_cmd(CLI + ["undo", "last"])
    assert rc == 0, "Undo failed"

    # 11. Sandbox
    # This is tricky as it might be interactive or spawn a shell
    # But let's try 'vault sandbox status'
    rc, out, err = run_cmd(CLI + ["sandbox", "status"])
    assert rc == 0, "Sandbox status failed"

    # 12. Story
    rc, out, err = run_cmd(CLI + ["story"])
    assert rc == 0, "Story failed"

    # 13. Search
    rc, out, err = run_cmd(CLI + ["search", "hello"])
    assert rc == 0, "Search failed"

    # 14. Sandbox Flow (Nested)
    print("\n--- Sandbox Test Flow ---")
    rc, out, err = run_cmd(CLI + ["sandbox"])
    assert rc == 0, "Sandbox enter failed"
    
    rc, out, err = run_cmd(CLI + ["sandbox", "status"])
    assert rc == 0, "Sandbox status within sandbox failed"
    
    # In sandbox, add a file
    sb_dir = os.path.join(".vault", "sandbox")
    with open(os.path.join(sb_dir, "sandbox_file.txt"), "w") as f:
        f.write("sandbox content")
    
    rc, out, err = run_cmd(CLI + ["sandbox", "merge"])
    assert rc == 0, "Sandbox merge failed"
    assert os.path.exists("sandbox_file.txt")
    
    print("\n✅ All basic and sandbox command tests passed!")

if __name__ == "__main__":
    original_dir = os.getcwd()
    try:
        setup_test_repo()
        test_flow()
    finally:
        os.chdir(original_dir)
        # shutil.rmtree(TEST_DIR) # Keep for inspection if needed
