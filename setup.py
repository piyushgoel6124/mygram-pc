import subprocess
import sys
import os
import time

def print_banner():
    print("=" * 60)
    print("      MyGram Instagram Scraper - System Setup & Launch")
    print("=" * 60)
    print("\nStarting automated environment setup for Windows...\n")

def run_command(command, description):
    print(f"[*] {description}...")
    try:
        subprocess.check_call(command, shell=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[!] ERROR: {description} failed.")
        print(f"Details: {e}")
        return False

def setup():
    print_banner()

    # 1. Upgrade pip
    if not run_command(f"{sys.executable} -m pip install --upgrade pip", "Upgrading pip"):
        return

    # 2. Install requirements
    if os.path.exists("requirements.txt"):
        if not run_command(f"{sys.executable} -m pip install -r requirements.txt", "Installing dependencies from requirements.txt"):
            return
    else:
        print("[!] requirements.txt not found. Installing core packages manually...")
        core_pkgs = "playwright requests psutil flask flask-cors pandas openpyxl"
        if not run_command(f"{sys.executable} -m pip install {core_pkgs}", f"Installing {core_pkgs}"):
            return

    # 3. Install Playwright Browsers
    if not run_command(f"{sys.executable} -m playwright install chromium", "Installing Playwright (Chromium)"):
        print("\n[!] Playwright installation failed. You might need to run 'playwright install' manually.")
    
    print("\n" + "=" * 60)
    print("[SUCCESS] Setup complete! All dependencies are installed.")
    print("=" * 60 + "\n")
    
    time.sleep(1)
    
    print("[*] Launching insta_session_manager.py...")
    try:
        # Launch the main script
        subprocess.call([sys.executable, "insta_session_manager.py"])
    except Exception as e:
        print(f"[!] Failed to launch the script: {e}")

if __name__ == "__main__":
    try:
        setup()
    except KeyboardInterrupt:
        print("\n\n[!] Setup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[!] An unexpected error occurred: {e}")
        input("\nPress Enter to exit...")
