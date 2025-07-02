If you see this file, it means the Python virtual environment was missing and has now been created.

# How to activate the venv (Linux/macOS)
source venv/bin/activate

# How to activate (Windows, PowerShell)
venv\Scripts\activate

# If you want to recreate the venv manually:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

- The venv folder is not committed to source control.
- Check requirements.txt for package dependencies.

Location: petconnect-18446-498ae195/fastapi_backend/venv/
