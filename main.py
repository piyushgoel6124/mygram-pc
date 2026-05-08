import sys
from app_server import start_server
from session_manager import get_all_sessions, login_and_save_session

def print_menu():
    print("\n--- MyGram Instagram Scraper ---")
    print("1. Login and Save Session (Headed)")
    print("2. Start API Server (Flask + Tunnel)")
    print("3. Exit")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        start_server()
    else:
        print_menu()
        choice = input("\nSelect an option: ").strip()
        if choice == '1':
            # login_and_save_session logic
            pass
        elif choice == '2':
            start_server()
        else:
            sys.exit()
